"""Atomic proposal/approval/commit tests for the v2 workflow."""

import hashlib
import json

from fastapi.testclient import TestClient

from fxi.api import router_v2
from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.contracts import ChapterCommitRequestV2, ChapterProposalRequestV2
from fxi.api.registry import WorkRegistry
from fxi.api.server import create_app
from fxi.core.config import FxiConfig
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


def _hash(value):
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prepare_workspace(cfg: FxiConfig) -> str:
    source_dir = cfg.sources_dir / "book"
    chapters = source_dir / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    text = "第一章\n初始事实。"
    (chapters / "ch001.md").write_text(text, encoding="utf-8")
    version = _hash(text)
    (source_dir / "source.yaml").write_text(f"sha256: {version}\n", encoding="utf-8")

    (cfg.projects_dir / "commit_work").mkdir(parents=True, exist_ok=True)
    (cfg.projects_dir / "commit_work" / "work.yaml").write_text("continuity_rules: []\n", encoding="utf-8")

    database = DatabaseClient(cfg.sqlite_path)
    with database.transaction() as cur:
        ensure_work(cur, "commit_work")
    WorkRegistry.from_database_client(database).register_source(
        "commit_work",
        "book",
        source_dir="book",
        source_version=version,
    )
    return version


def test_v2_commit_is_idempotent_and_uses_version_cas(temp_workspace: FxiConfig, monkeypatch):
    source_version = _prepare_workspace(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "writer-1",
                    "roles": ["writer", "reviewer"],
                    "work_ids": ["commit_work"],
                },
                "approver-token": {
                    "actor_id": "approver-1",
                    "roles": ["approver"],
                    "work_ids": ["commit_work"],
                },
            }
        ),
    )
    monkeypatch.setenv("FXI_HUMAN_APPROVAL_TOKEN", "commit-token")

    app = create_app(temp_workspace)
    app.state.semantic_reviewer = lambda request: {
        "check_id": "semantic_review",
        "status": "PASSED",
        "input_text_hash": request.text_hash,
    }
    client = TestClient(app)
    writer_headers = {"X-Fxi-Actor-Token": "writer-token"}
    approver_headers = {
        "X-Fxi-Actor-Token": "approver-token",
        "X-Fxi-Human-Approval-Token": "commit-token",
    }

    # 1. 建立快照
    snap_res = client.post(
        "/v2/sources/book/snapshots",
        headers=writer_headers,
        json={
            "work_id": "commit_work",
            "source_id": "book",
            "expected_source_version": source_version,
            "idempotency_key": "snap-commit",
        },
    )
    assert snap_res.status_code == 200

    # 2. 章节审查以获取合法 PASSED review_ref
    text = "英雄走出门口。"
    text_hash = _hash(text)
    review_res = client.post(
        "/v2/writing/review",
        headers=writer_headers,
        json={
            "work_id": "commit_work",
            "source_id": "book",
            "source_version": source_version,
            "knowledge_version": "knowledge-v0",
            "chapter_index": 2,
            "text": text,
            "text_hash": text_hash,
            "plan_hash": "plan-1",
            "context_hash": "context-1",
        },
    )
    assert review_res.status_code == 200
    assert review_res.json()["overall_status"] == "PASSED"
    report_id = review_res.json()["report_id"]

    # 3. 创建提议
    proposal_body = {
        "work_id": "commit_work",
        "source_id": "book",
        "source_version": source_version,
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "text": text,
        "text_hash": text_hash,
        "final_review_ref": report_id,
        "dependency_versions": {"source_version": source_version},
        "proposal_hash": "placeholder",
        "idempotency_key": "proposal-1",
    }
    proposal_body["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal_body)
    )
    proposal = client.post(
        "/v2/writing/proposals",
        headers=writer_headers,
        json=proposal_body,
    )
    assert proposal.status_code == 200
    proposal_id = proposal.json()["proposal_id"]

    # 4. 人工审批
    approval = client.post(
        "/v2/approvals",
        headers=approver_headers,
        json={
            "action": "chapter_commit",
            "target_id": proposal_id,
            "target_hash": proposal_body["proposal_hash"],
            "expected_version": "knowledge-v0",
        },
    )
    assert approval.status_code == 200
    approval_id = approval.json()["approval_id"]

    # 5. 提交章节
    commit_body = {
        "work_id": "commit_work",
        "source_id": "book",
        "source_version": source_version,
        "chapter_version": "chapter-v1",
        "chapter_index": 2,
        "proposal_id": proposal_id,
        "proposal_hash": proposal_body["proposal_hash"],
        "pending_text": text,
        "text_hash": text_hash,
        "final_review_reference": report_id,
        "approval_id": approval_id,
        "accepted_state_change_ids": [],
        "expected_knowledge_version": "knowledge-v0",
        "idempotency_key": "commit-1",
        "payload_hash": "placeholder",
    }
    commit_body["payload_hash"] = router_v2._commit_payload_fingerprint(
        ChapterCommitRequestV2(**commit_body)
    )
    committed = client.post(
        "/v2/writing/commits",
        headers=writer_headers,
        json=commit_body,
    )
    assert committed.status_code == 200
    assert committed.json()["new_knowledge_version"] == "knowledge-v1"

    # 6. 重放幂等
    replay = client.post(
        "/v2/writing/commits",
        headers=writer_headers,
        json=commit_body,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["commit_id"] == committed.json()["commit_id"]

    # 7. 验证数据库状态
    client_db = DatabaseClient(temp_workspace.sqlite_path)
    with client_db.transaction() as cur:
        head = cur.execute(
            "SELECT knowledge_version, chapter_version FROM v2_work_heads WHERE work_id = ?",
            ("commit_work",),
        ).fetchone()
        proposal_row = cur.execute(
            "SELECT status FROM v2_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
    assert head["knowledge_version"] == "knowledge-v1"
    assert head["chapter_version"] == "chapter-v1"
    assert proposal_row["status"] == "COMMITTED"
