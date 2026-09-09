"""通用查询拆解不能依赖特定作品词汇。"""

from pathlib import Path

from fxi.domain.entities import EntityManager
from fxi.index_retrieval.query_engine import QueryEngine


def test_unknown_query_has_no_fabricated_subject(temp_workspace):
    result = QueryEngine(temp_workspace).decompose_query(
        "work_a", "某个未登记实体的状态是什么", use_mock=True
    )

    assert result.target_entities == []


def test_work_config_can_supply_domain_intent_terms(temp_workspace):
    work_dir = temp_workspace.projects_dir / "work_a"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "work.yaml").write_text(
        "work_id: work_a\nquery_intent_keywords:\n  skill: [能力查询]\n",
        encoding="utf-8",
    )
    EntityManager(temp_workspace).upsert_entity(
        "work_a", "char_actor", name="登记角色", category="character"
    )

    result = QueryEngine(temp_workspace)._heuristic_decompose(
        "work_a", "登记角色的能力查询"
    )

    assert result.target_entities == ["登记角色"]
    assert result.intent == "skill"


def test_query_engine_has_no_known_work_literals():
    source = Path("src/fxi/index_retrieval/query_engine.py").read_text(encoding="utf-8")

    for literal in ("zhanshen", "林七夜", "黑缎", "神墟", "禁墟"):
        assert literal not in source
