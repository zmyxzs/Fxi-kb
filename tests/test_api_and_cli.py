"""
FastAPI 路由、CLI 命令、技法沉淀与模型网关集成测试
"""

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from fxi.api.server import create_app
from fxi.cli.main import app as cli_app
from fxi.core.config import FxiConfig
from fxi.materials_skills.anti_patterns import AntiPatternRepository
from fxi.materials_skills.distillation_receiver import DistillationReceiver
from fxi.materials_skills.skill_store import SkillStore
from fxi.model_gateway.gateway import ModelGateway
from fxi.model_gateway.providers import MockProvider
from fxi.state_ledger.calculator import LedgerCalculator


def test_api_health_and_endpoints(temp_workspace: FxiConfig):
    """测试 FastAPI 服务端点"""
    app = create_app(temp_workspace)
    client = TestClient(app)

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


def test_materials_and_skills_ingestion(temp_workspace: FxiConfig):
    """测试技法接收器、技法库与反模式检测器"""
    receiver = DistillationReceiver(temp_workspace)
    resp = receiver.ingest_skill_package({
        "slug": "golden_three_chapters",
        "rules": ["危机引入 -> 期待感钩子 -> 阶梯兑现"],
        "anti_patterns": ["开篇十万字设定介绍无冲突"]
    })
    assert resp["status"] == "success"

    store = SkillStore(temp_workspace)
    skills = store.list_skills()
    assert "golden_three_chapters" in skills
    rules = store.load_rules("golden_three_chapters")
    assert len(rules) == 1

    # 负面教条库读取
    repo = AntiPatternRepository(temp_workspace)
    constraints = repo.get_negative_constraints(skill_slug="golden_three_chapters")
    assert len(constraints) == 1
    assert "开篇十万字" in constraints[0]


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
