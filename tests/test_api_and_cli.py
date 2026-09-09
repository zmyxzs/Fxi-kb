"""
FastAPI 路由、CLI 命令、技法沉淀与模型网关集成测试
"""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.server import create_app
from fxi.cli.main import app as cli_app
from fxi.core.config import FxiConfig
from fxi.core.exceptions import ValidationError
from fxi.materials_skills.distillation_receiver import DistillationReceiver
from fxi.model_gateway.gateway import ModelGateway
from fxi.model_gateway.providers import MockProvider
from fxi.state_ledger.calculator import LedgerCalculator


def test_api_health_and_endpoints(temp_workspace: FxiConfig, monkeypatch):
    """测试 FastAPI 服务端点"""
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "test-token": {
                    "actor_id": "test-actor",
                    "roles": ["reader", "reviewer"],
                    "work_ids": ["work_a"],
                }
            }
        ),
    )
    app = create_app(temp_workspace)
    client = TestClient(app, headers={"X-Fxi-Actor-Token": "test-token"})

    # 1. Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 2. State query
    calc = LedgerCalculator(temp_workspace)
    calc.record_event("work_a", "char_test", "gold", 500.0, "sc_1", narrative_order=1, reason="init")

    res = client.post("/v1/state/query", json={
        "work_id": "work_a",
        "entity_id": "char_test",
        "metric_id": "gold",
        "narrative_order": 1
    })
    assert res.status_code == 200
    data = res.json()
    assert data["computed_value"] == 500.0
    assert data["status"] == "EXPLICIT"

    # 3. OOC check
    res = client.post("/v1/checks/ooc", json={
        "work_id": "work_a",
        "narrative_order": 1,
        "speaker_id": "char_test",
        "draft_text": "这只是普通的问候。",
        "unrevealed_secrets": {}
    })
    assert res.status_code == 200
    assert res.json()["has_violations"] is False


def test_cli_commands(temp_workspace: FxiConfig):
    """测试 Typer CLI 命令运行"""
    runner = CliRunner()

    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "rebuild" in result.stdout
    assert "search" in result.stdout

    result_search = runner.invoke(cli_app, ["search", "--help"])
    assert result_search.exit_code == 0

    result_state = runner.invoke(cli_app, ["state", "--help"])
    assert result_state.exit_code == 0

    result_ripple = runner.invoke(cli_app, ["ripple", "--help"])
    assert result_ripple.exit_code == 0

    result_timeline = runner.invoke(cli_app, ["timeline", "--help"])
    assert result_timeline.exit_code == 0

    result_relations = runner.invoke(cli_app, ["relations", "--help"])
    assert result_relations.exit_code == 0

    result_items = runner.invoke(cli_app, ["items", "--help"])
    assert result_items.exit_code == 0

    result_skills = runner.invoke(cli_app, ["skills", "--help"])
    assert result_skills.exit_code == 0



def test_materials_and_skills_ingestion_rejects_unbound_legacy_package(temp_workspace: FxiConfig):
    """历史未绑定候选必须被当前 evidence 边界拒绝。"""
    receiver = DistillationReceiver(temp_workspace)
    with pytest.raises(ValidationError, match="MISSING_EVIDENCE_REFS|EVIDENCE_SCOPE_UNBOUND"):
        receiver.ingest_skill_package(
            {
                "slug": "synthetic_skill",
                "rules": ["synthetic-rule"],
                "anti_patterns": ["synthetic-anti-pattern"],
                "evaluation_ref": "synthetic-evaluation",
            },
            actor_id="synthetic-extractor",
            work_id="synthetic-work",
            source_id="synthetic-source",
            source_version="source-v1",
            input_hash="synthetic-input-hash",
        )


def test_model_gateway_and_cache(temp_workspace: FxiConfig):
    """测试模型网关 MockProvider、SQLite 响应缓存与费用追踪"""
    gw = ModelGateway(temp_workspace)

    # 首次调用 (use_mock=True) -> 得到 Mock 输出
    res1 = gw.complete(
        task_type="world_building",
        prompt="帮我想一个剑法名字",
        use_mock=True
    )
    assert "mock_success" in res1

    # 二次调用 -> 命中 SHA-256 缓存
    res2 = gw.complete(
        task_type="world_building",
        prompt="帮我想一个剑法名字",
        use_mock=True
    )
    assert res2 == res1

    # 审计记录已落盘
    summary = gw.tracker.get_summary()
    assert summary["total_calls"] >= 1


def test_query_engine_and_ask_endpoint(temp_workspace: FxiConfig, monkeypatch):
    """测试自然语言智能问答 QueryEngine 与 /v1/query/ask 端点"""
    from fxi.index_retrieval.query_engine import QueryEngine
    from fxi.domain.entities import EntityManager

    em = EntityManager(temp_workspace)
    em.upsert_entity("test_work", "char_hero", name="林七夜", category="character")

    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "test-token": {
                    "actor_id": "test-actor",
                    "roles": ["reader"],
                    "work_ids": ["test_work"],
                }
            }
        ),
    )

    # 1. QueryEngine direct test
    engine = QueryEngine(temp_workspace)
    res = engine.ask(work_id="test_work", question="林七夜的武器是什么", use_mock=True)
    assert res.work_id == "test_work"
    assert "林七夜" in res.decomposition.target_entities
    assert "针对关于《test_work》的问题" in res.answer

    # 2. FastAPI endpoint test
    app = create_app(temp_workspace)
    client = TestClient(app, headers={"X-Fxi-Actor-Token": "test-token"})
    res_api = client.post("/v1/query/ask", json={
        "work_id": "test_work",
        "question": "林七夜的身份是什么"
    })
    assert res_api.status_code == 200
    data = res_api.json()
    assert data["work_id"] == "test_work"
    assert "林七夜" in data["target_entities"]


def test_causal_query_and_canonical_dedup(temp_workspace: FxiConfig):
    """测试实体权威规范去重与因果问答拓扑溯源"""
    import yaml
    from fxi.domain.canonical import CanonicalRegistry
    from fxi.index_retrieval.query_engine import QueryEngine
    from fxi.domain.entities import EntityManager
    from fxi.timeline.dag import CausalDAG
    from fxi.storage.sqlite_client import DatabaseClient, ensure_work

    db = DatabaseClient(temp_workspace.sqlite_path)
    with db.transaction() as cur:
        ensure_work(cur, "w1")
        ensure_work(cur, "zhanshen_test")

    canonical_file = temp_workspace.projects_dir / "w1" / "entities" / "canonical.yaml"
    canonical_file.parent.mkdir(parents=True, exist_ok=True)
    canonical_file.write_text(
        yaml.safe_dump(
            {
                "canonical_ids": {
                    "character": {"林七夜": "char_lin_qiye", "赵空城": "char_zhao_kongcheng"},
                    "item": {"黑缎": "item_black_ribbon"},
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    # 1. 测试规范 ID 映射与别名合并
    reg = CanonicalRegistry(temp_workspace)
    assert reg.get_canonical_id("w1", "林七夜", "character") == "char_lin_qiye"
    assert reg.get_canonical_id("w1", "赵空城", "character") == "char_zhao_kongcheng"
    assert reg.get_canonical_id("w1", "黑缎", "item") == "item_black_ribbon"

    # 自定义实体 ID 保留测试
    custom_id = reg.get_canonical_id("w1", "自定义配角", "character", default_id="char_custom_001")
    assert custom_id == "char_custom_001"

    # 2. 问答拆解测试：实体子词不污染关键词，意图准确识别
    em = EntityManager(temp_workspace)
    em.upsert_entity("zhanshen_test", "char_lin_qiye", name="林七夜", category="character")

    engine = QueryEngine(temp_workspace)
    decomp = engine._heuristic_decompose("zhanshen_test", "林七夜为什么去斋戒所")
    assert "林七夜" in decomp.target_entities
    assert "七夜" not in decomp.keywords
    assert decomp.intent == "causal_reason"

    # 3. 因果图拓扑溯源测试
    dag = CausalDAG(temp_workspace)
    dag.register_event("ev_01", "zhanshen_test", "sc_01", narrative_order=268, physical_time="t1", summary="林七夜精神崩溃")
    dag.register_event("ev_02", "zhanshen_test", "sc_02", narrative_order=281, physical_time="t2", summary="林七夜被留院一年")
    dag.register_event("ev_03", "zhanshen_test", "sc_03", narrative_order=282, physical_time="t3", summary="安卿鱼进入斋戒所寻找林七夜")
    dag.add_causal_link("zhanshen_test", "ev_01", "ev_02", "direct_cause")
    dag.add_causal_link("zhanshen_test", "ev_02", "ev_03", "direct_cause")

    causes_ev3 = dag.get_direct_causes("zhanshen_test", "ev_03")
    assert causes_ev3 == ["ev_02"]
    causes_ev2 = dag.get_direct_causes("zhanshen_test", "ev_02")
    assert causes_ev2 == ["ev_01"]
    ancestors = dag.get_ancestors("zhanshen_test", "ev_03")
    assert "ev_01" in ancestors and "ev_02" in ancestors
