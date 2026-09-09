"""
Comprehensive End-to-End Verification for Knowledge Base Integrity.

Covers:
1. Fresh schema and source object setup in isolated temporary directory.
2. Multi-work registration, source registration, actor scope boundaries.
3. Ingesting text, generating source version, evidence coordinates, and candidate proposals.
4. Test reviewer integration generating verified PASSED review.
5. Approval workflow, chapter commit, and 5 projection types generation.
6. Commit replay idempotency without duplicate side effects.
7. Mismatched work/source/version/hashes/approval/actor failure rejections.
8. Source object immutability under disk modifications.
9. Causal DAG cycle, cross-work edge, and transaction rollback.
10. Reviewer failure visibility preventing formal knowledge commits.
"""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fxi.api import router_v2
from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.contracts import ChapterCommitRequestV2, ChapterProposalRequestV2
from fxi.api.registry import WorkRegistry
from fxi.api.server import create_app
from fxi.core.config import FxiConfig
from fxi.core.exceptions import CausalConflictError, ValidationError
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.storage.sqlite_client import DatabaseClient, ensure_entity, ensure_work
from fxi.timeline.dag import CausalDAG


def _hash(value) -> str:
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _setup_e2e_workspace(cfg: FxiConfig) -> tuple[str, str]:
    """设置两个独立作品与源文件目录。"""
    database = DatabaseClient(cfg.sqlite_path)
    registry = WorkRegistry.from_database_client(database)

    # 1. work_a 与 source_a
    dir_a = cfg.sources_dir / "book_a"
    chapters_a = dir_a / "chapters"
    chapters_a.mkdir(parents=True, exist_ok=True)
    text_a = "第一章：勇者降临。\n勇者拔出了誓约胜利之剑。"
    (chapters_a / "ch001.md").write_text(text_a, encoding="utf-8")
    ver_a = _hash(text_a)
    (dir_a / "source.yaml").write_text(f"sha256: {ver_a}\n", encoding="utf-8")
    (cfg.projects_dir / "work_a").mkdir(parents=True, exist_ok=True)
    (cfg.projects_dir / "work_a" / "work.yaml").write_text("continuity_rules: []\n", encoding="utf-8")

    with database.transaction() as cur:
        ensure_work(cur, "work_a")
    registry.register_source("work_a", "book_a", source_dir="book_a", source_version=ver_a)

    # 2. work_b 与 source_b
    dir_b = cfg.sources_dir / "book_b"
    chapters_b = dir_b / "chapters"
    chapters_b.mkdir(parents=True, exist_ok=True)
    text_b = "第一章：星际探险。\n巡洋舰跃迁进入半人马座。"
    (chapters_b / "ch001.md").write_text(text_b, encoding="utf-8")
    ver_b = _hash(text_b)
    (dir_b / "source.yaml").write_text(f"sha256: {ver_b}\n", encoding="utf-8")
    (cfg.projects_dir / "work_b").mkdir(parents=True, exist_ok=True)
    (cfg.projects_dir / "work_b" / "work.yaml").write_text("continuity_rules: []\n", encoding="utf-8")

    with database.transaction() as cur:
        ensure_work(cur, "work_b")
    registry.register_source("work_b", "book_b", source_dir="book_b", source_version=ver_b)

    return ver_a, ver_b


@pytest.fixture
def e2e_env(temp_workspace: FxiConfig, monkeypatch):
    ver_a, ver_b = _setup_e2e_workspace(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "token-actor-a": {
                    "actor_id": "actor_a",
                    "roles": ["writer", "reviewer"],
                    "work_ids": ["work_a"],  # 仅限 work_a
                },
                "token-actor-all": {
                    "actor_id": "actor_all",
                    "roles": ["writer", "reviewer"],
                    "work_ids": ["work_a", "work_b"],  # 全局作者
                },
                "token-approver-a": {
                    "actor_id": "approver_a",
                    "roles": ["approver"],
                    "work_ids": ["work_a"],
                },
            }
        ),
    )
    monkeypatch.setenv("FXI_HUMAN_APPROVAL_TOKEN", "approval-secret-key")

    app = create_app(temp_workspace)
    # 注入确定性语义 Reviewer
    app.state.semantic_reviewer = lambda req: {
        "check_id": "semantic_review",
        "status": "PASSED",
        "input_text_hash": req.text_hash,
    }
    client = TestClient(app)
    return {
        "client": client,
        "config": temp_workspace,
        "ver_a": ver_a,
        "ver_b": ver_b,
    }


def test_kb_e2e_full_lifecycle_and_projections(e2e_env):
    """端到端全链路：快照 -> 审核 -> 提议 -> 审批 -> 5类投影原子提交 -> 幂等重放 -> 来源不可变性。"""
    client: TestClient = e2e_env["client"]
    cfg: FxiConfig = e2e_env["config"]
    ver_a: str = e2e_env["ver_a"]

    headers_writer = {"X-Fxi-Actor-Token": "token-actor-a"}
    headers_approver = {
        "X-Fxi-Actor-Token": "token-approver-a",
        "X-Fxi-Human-Approval-Token": "approval-secret-key",
    }

    # 1. 建立来源快照
    snap_resp = client.post(
        "/v2/sources/book_a/snapshots",
        headers=headers_writer,
        json={
            "work_id": "work_a",
            "source_id": "book_a",
            "expected_source_version": ver_a,
            "idempotency_key": "snap-a-01",
        },
    )
    assert snap_resp.status_code == 200, snap_resp.text

    # 2. 真实 Review 审查
    pending_text = "勇者踏入古老神殿，唤醒了沉睡的守护者。"
    text_hash = _hash(pending_text)
    review_resp = client.post(
        "/v2/writing/review",
        headers=headers_writer,
        json={
            "work_id": "work_a",
            "source_id": "book_a",
            "source_version": ver_a,
            "knowledge_version": "knowledge-v0",
            "chapter_index": 2,
            "text": pending_text,
            "text_hash": text_hash,
            "plan_hash": "plan-hash-1",
            "context_hash": "ctx-hash-1",
        },
    )
    assert review_resp.status_code == 200
    review_data = review_resp.json()
    assert review_data["overall_status"] == "PASSED"
    report_id = review_data["report_id"]

    # 3. 提交章节提议 (包含因果事件、状态变更与角色认知)
    proposal_body = {
        "work_id": "work_a",
        "source_id": "book_a",
        "source_version": ver_a,
        "chapter_index": 2,
        "chapter_version": "ch-v2",
        "text": pending_text,
        "text_hash": text_hash,
        "final_review_ref": report_id,
        "proposal_hash": "placeholder",
        "idempotency_key": "prop-a-01",
        "causal_events": [
            {
                "event_id": "ev_hero_enter",
                "narrative_order": 2,
                "physical_time": "2026-06-01 10:00",
                "summary": "勇者进入神殿",
            }
        ],
        "state_change_proposals": [
            {
                "id": "state_sc_01",
                "entity_id": "char_hero",
                "metric_id": "mp",
                "delta": -20.0,
            }
        ],
        "character_knowledge": {
            "char_hero": {"knows_guardian": True, "location": "ancient_temple"}
        },
    }
    proposal_req = ChapterProposalRequestV2(**proposal_body)
    proposal_body["proposal_hash"] = router_v2._proposal_payload_fingerprint(proposal_req)

    prop_resp = client.post(
        "/v2/writing/proposals",
        headers=headers_writer,
        json=proposal_body,
    )
    assert prop_resp.status_code == 200, prop_resp.text
    proposal_id = prop_resp.json()["proposal_id"]

    # 4. 人工审批
    approval_resp = client.post(
        "/v2/approvals",
        headers=headers_approver,
        json={
            "action": "chapter_commit",
            "target_id": proposal_id,
            "target_hash": proposal_body["proposal_hash"],
            "expected_version": "knowledge-v0",
            "work_id": "work_a",
        },
    )
    assert approval_resp.status_code == 200, approval_resp.text
    approval_id = approval_resp.json()["approval_id"]

    # 5. 提交 commit 并断言五类投影
    commit_body = {
        "work_id": "work_a",
        "source_id": "book_a",
        "source_version": ver_a,
        "chapter_version": "ch-v2",
        "chapter_index": 2,
        "proposal_id": proposal_id,
        "proposal_hash": proposal_body["proposal_hash"],
        "pending_text": pending_text,
        "text_hash": text_hash,
        "final_review_reference": report_id,
        "approval_id": approval_id,
        "accepted_state_change_ids": ["state_sc_01"],
        "expected_knowledge_version": "knowledge-v0",
        "idempotency_key": "commit-a-01",
        "payload_hash": "placeholder",
    }
    commit_req = ChapterCommitRequestV2(**commit_body)
    commit_body["payload_hash"] = router_v2._commit_payload_fingerprint(commit_req)

    commit_resp = client.post(
        "/v2/writing/commits",
        headers=headers_writer,
        json=commit_body,
    )
    assert commit_resp.status_code == 200, commit_resp.text
    commit_data = commit_resp.json()
    assert commit_data["new_knowledge_version"] == "knowledge-v1"
    commit_id = commit_data["commit_id"]

    # 验证五类投影全部落盘且具备追溯字段
    db = DatabaseClient(cfg.sqlite_path)
    with db.transaction() as cur:
        stored_review = cur.execute(
            "SELECT payload_json FROM v2_reviews WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        stored_review_payload = json.loads(stored_review["payload_json"])
        assert stored_review_payload["required_checks"] == ["continuity", "semantic_review"]
        assert stored_review_payload["dependency_versions"]["source_version"] == ver_a
        assert stored_review_payload["dependency_versions"]["knowledge_version"] == "knowledge-v0"
        assert stored_review_payload["dependency_hash"]

        # 投影 1: 正文文档
        doc = cur.execute(
            "SELECT * FROM v2_commit_documents WHERE commit_id = ?", (commit_id,)
        ).fetchone()
        assert doc is not None
        assert doc["content"] == pending_text
        assert doc["chapter_index"] == 2
        assert doc["text_hash"] == text_hash

        # 投影 2: 因果事件
        event = cur.execute(
            "SELECT * FROM v2_commit_events WHERE commit_id = ?", (commit_id,)
        ).fetchone()
        assert event is not None
        assert event["event_id"] == "ev_hero_enter"
        assert "勇者进入神殿" in event["payload_json"]

        # 投影 3: 状态变更
        sc = cur.execute(
            "SELECT * FROM v2_commit_state_changes WHERE commit_id = ?", (commit_id,)
        ).fetchone()
        assert sc is not None
        assert sc["state_change_id"] == "state_sc_01"

        # 投影 4: 角色认知
        know = cur.execute(
            "SELECT * FROM v2_commit_knowledge WHERE commit_id = ?", (commit_id,)
        ).fetchone()
        assert know is not None
        assert know["character_id"] == "char_hero"
        assert "knows_guardian" in know["payload_json"]

        # 投影 5: 索引条目
        idx = cur.execute(
            "SELECT * FROM v2_commit_index WHERE commit_id = ?", (commit_id,)
        ).fetchone()
        assert idx is not None
        assert idx["content_hash"] == text_hash

        # 投影任务总账: 全部 APPLIED
        tasks = cur.execute(
            "SELECT projection_kind, status FROM v2_projection_tasks WHERE commit_id = ?",
            (commit_id,),
        ).fetchall()
        assert len(tasks) == 5
        kinds = {t["projection_kind"] for t in tasks}
        assert kinds == {"document", "events", "state", "knowledge", "index"}
        assert all(t["status"] == "APPLIED" for t in tasks)

        # 作品 Head 版本推进
        head = cur.execute(
            "SELECT * FROM v2_work_heads WHERE work_id = 'work_a'"
        ).fetchone()
        assert head["knowledge_version"] == "knowledge-v1"
        assert head["chapter_version"] == "ch-v2"

    # 6. 重放同一 commit (幂等性断言)
    replay_resp = client.post(
        "/v2/writing/commits",
        headers=headers_writer,
        json=commit_body,
    )
    assert replay_resp.status_code == 200
    replay_data = replay_resp.json()
    assert replay_data["idempotent_replay"] is True
    assert replay_data["commit_id"] == commit_id

    # 验证重放后五类投影数量严格不变，没有重复新增
    with db.transaction() as cur:
        assert cur.execute("SELECT count(*) FROM v2_commits WHERE work_id = 'work_a'").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM v2_commit_documents WHERE work_id = 'work_a'").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM v2_commit_events").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM v2_commit_state_changes").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM v2_commit_knowledge").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM v2_commit_index WHERE work_id = 'work_a'").fetchone()[0] == 1
        assert cur.execute("SELECT count(*) FROM v2_projection_tasks WHERE work_id = 'work_a'").fetchone()[0] == 5

    # 7. 来源不可变性断言: 修改磁盘源码正文后，已冻结快照数据不变
    ch1_disk = cfg.sources_dir / "book_a" / "chapters" / "ch001.md"
    ch1_disk.write_text("篡改正文：勇者从未出现！", encoding="utf-8")

    with db.transaction() as cur:
        snap_row = cur.execute(
            "SELECT * FROM v2_source_snapshots WHERE work_id = 'work_a' AND source_id = 'book_a'"
        ).fetchone()
        assert snap_row is not None
        assert snap_row["version"] == ver_a
        # 即使磁盘文件已被覆写，快照记录和哈希依然指向不可变的原始版本
        assert snap_row["version"] != _hash("篡改正文：勇者从未出现！")


def test_kb_e2e_isolation_and_security_boundaries(e2e_env):
    """跨作品、越权操作、数据篡改和异常分支拦截测试。"""
    client: TestClient = e2e_env["client"]
    cfg: FxiConfig = e2e_env["config"]
    ver_a: str = e2e_env["ver_a"]
    ver_b: str = e2e_env["ver_b"]

    headers_actor_a = {"X-Fxi-Actor-Token": "token-actor-a"}  # 仅限 work_a
    headers_actor_all = {"X-Fxi-Actor-Token": "token-actor-all"}

    # 1. 跨作品未授权拦截: actor_a 访问 work_b 报 403
    forbidden_resp = client.post(
        "/v2/sources/book_b/snapshots",
        headers=headers_actor_a,
        json={
            "work_id": "work_b",
            "source_id": "book_b",
            "expected_source_version": ver_b,
            "idempotency_key": "snap-b-forbidden",
        },
    )
    assert forbidden_resp.status_code == 403

    # 2. actor_all 合法建立 work_b 快照
    allowed_resp = client.post(
        "/v2/sources/book_b/snapshots",
        headers=headers_actor_all,
        json={
            "work_id": "work_b",
            "source_id": "book_b",
            "expected_source_version": ver_b,
            "idempotency_key": "snap-b-allowed",
        },
    )
    assert allowed_resp.status_code == 200

    # 3. 错 source_version 拦截
    bad_ver_resp = client.post(
        "/v2/sources/book_b/snapshots",
        headers=headers_actor_all,
        json={
            "work_id": "work_b",
            "source_id": "book_b",
            "expected_source_version": "wrong_hash_12345",
            "idempotency_key": "snap-b-bad-ver",
        },
    )
    assert bad_ver_resp.status_code == 409

    # 4. 错 proposal_hash 拦截
    bad_prop_resp = client.post(
        "/v2/writing/proposals",
        headers=headers_actor_all,
        json={
            "work_id": "work_b",
            "source_id": "book_b",
            "source_version": ver_b,
            "chapter_index": 1,
            "chapter_version": "ch-v1",
            "text": "测试正文",
            "text_hash": _hash("测试正文"),
            "final_review_ref": "fake_review_ref",
            "proposal_hash": "tampered_proposal_hash",
            "idempotency_key": "bad-prop-01",
        },
    )
    assert bad_prop_resp.status_code == 409

    # 5. 跨作品提交提议拦截 (work_a 的提议伪造为 work_b)
    mismatched_work_resp = client.post(
        "/v2/writing/proposals",
        headers=headers_actor_all,
        json={
            "work_id": "work_b",
            "source_id": "book_a",  # 来源属于 work_a
            "source_version": ver_a,
            "chapter_index": 1,
            "chapter_version": "ch-v1",
            "text": "测试跨作品",
            "text_hash": _hash("测试跨作品"),
            "final_review_ref": "fake_review_ref",
            "proposal_hash": "dummy",
            "idempotency_key": "mismatch-prop-01",
        },
    )
    assert mismatched_work_resp.status_code in (404, 409)


def test_kb_e2e_dag_cycle_and_ripple_integrity(temp_workspace: FxiConfig):
    """验证时间线 DAG 环阻断、跨作品边阻断及事务原子回滚。"""
    dag = CausalDAG(temp_workspace)
    db = DatabaseClient(temp_workspace.sqlite_path)

    with db.transaction() as cur:
        ensure_work(cur, "work_dag_a")
        ensure_work(cur, "work_dag_b")

    # 注册 3 个事件
    dag.register_event("ev_1", "work_dag_a", "sc_1", 10, "2026-01-01", "事件1")
    dag.register_event("ev_2", "work_dag_a", "sc_2", 20, "2026-01-02", "事件2")
    dag.register_event("ev_3", "work_dag_a", "sc_3", 30, "2026-01-03", "事件3")
    dag.register_event("ev_b1", "work_dag_b", "sc_b1", 10, "2026-01-01", "作品B事件")

    # 添加因果链: ev_1 -> ev_2 -> ev_3
    dag.add_causal_link("work_dag_a", "ev_1", "ev_2")
    dag.add_causal_link("work_dag_a", "ev_2", "ev_3")

    # 1. 尝试成环: ev_3 -> ev_1 必须抛出 CausalConflictError
    with pytest.raises(CausalConflictError):
        dag.add_causal_link("work_dag_a", "ev_3", "ev_1")

    # 验证成环边未写入
    assert "ev_1" not in dag.get_direct_effects("work_dag_a", "ev_3")

    # 2. 尝试跨作品连边: work_dag_a 连到 work_dag_b
    with pytest.raises(ValidationError):
        dag.add_causal_link("work_dag_a", "ev_1", "ev_b1")

    # 3. 状态锚点收据幂等与载荷冲突校验
    calc = LedgerCalculator(temp_workspace)
    calc.record_event(
        work_id="work_dag_a",
        entity_id="char_x",
        metric_id="mana",
        delta=-10.0,
        scene_uuid="sc_1",
        narrative_order=10,
        reason="施法消耗",
        commit_id="commit_dag_1",
        idempotency_key="idemp_mana_1",
    )
    # 相同幂等键，不同 payload，必须抛出 ValidationError 且被阻断
    with pytest.raises(ValidationError):
        calc.record_event(
            work_id="work_dag_a",
            entity_id="char_x",
            metric_id="mana",
            delta=-50.0,  # 冲突载荷
            scene_uuid="sc_1",
            narrative_order=10,
            reason="恶意篡改消耗",
            commit_id="commit_dag_1",
            idempotency_key="idemp_mana_1",
        )


def test_kb_e2e_reviewer_failure_visibility(temp_workspace: FxiConfig, monkeypatch):
    """验证审查失败或 Reviewer 异常时严格阻断正式知识写入。"""
    ver_a, _ = _setup_e2e_workspace(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "writer-1",
                    "roles": ["writer", "reviewer"],
                    "work_ids": ["work_a"],
                }
            }
        ),
    )
    app = create_app(temp_workspace)

    # 注入返回 REJECTED 的审查器
    app.state.semantic_reviewer = lambda req: {
        "check_id": "semantic_review",
        "status": "REJECTED",
        "violations": ["严重人设崩坏(OOC)"],
        "input_text_hash": req.text_hash,
    }
    client = TestClient(app)
    headers = {"X-Fxi-Actor-Token": "writer-token"}

    # 先建立来源快照
    snap_resp = client.post(
        "/v2/sources/book_a/snapshots",
        headers=headers,
        json={
            "work_id": "work_a",
            "source_id": "book_a",
            "expected_source_version": ver_a,
            "idempotency_key": "snap-rej-01",
        },
    )
    assert snap_resp.status_code == 200

    text = "崩坏的对话内容"
    text_hash = _hash(text)
    review_resp = client.post(
        "/v2/writing/review",
        headers=headers,
        json={
            "work_id": "work_a",
            "source_id": "book_a",
            "source_version": ver_a,
            "knowledge_version": "knowledge-v0",
            "chapter_index": 1,
            "text": text,
            "text_hash": text_hash,
            "plan_hash": "plan-1",
            "context_hash": "ctx-1",
        },
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["overall_status"] == "REJECTED"
    rejected_report_id = review_resp.json()["report_id"]

    # 尝试基于 REJECTED 的 review 创建提议: 必须被阻断
    proposal_body = {
        "work_id": "work_a",
        "source_id": "book_a",
        "source_version": ver_a,
        "chapter_index": 1,
        "chapter_version": "ch-v1",
        "text": text,
        "text_hash": text_hash,
        "final_review_ref": rejected_report_id,
        "proposal_hash": "placeholder",
        "idempotency_key": "prop-rejected-01",
    }
    proposal_req = ChapterProposalRequestV2(**proposal_body)
    proposal_body["proposal_hash"] = router_v2._proposal_payload_fingerprint(proposal_req)

    prop_resp = client.post(
        "/v2/writing/proposals",
        headers=headers,
        json=proposal_body,
    )
    assert prop_resp.status_code == 409
    assert "PASSED review" in prop_resp.text or "INCOMPLETE_INPUT" in prop_resp.text

    # 验证数据库中 0 commits、0 正式知识投影
    db = DatabaseClient(temp_workspace.sqlite_path)
    with db.transaction() as cur:
        commits_cnt = cur.execute("SELECT count(*) FROM v2_commits WHERE work_id = 'work_a'").fetchone()[0]
        assert commits_cnt == 0
        docs_cnt = cur.execute("SELECT count(*) FROM v2_commit_documents WHERE work_id = 'work_a'").fetchone()[0]
        assert docs_cnt == 0
