"""查询能力信号与检索计划契约回归。"""

from fxi.domain.entities import EntityManager
from fxi.index_retrieval.query_engine import QueryEngine
from fxi.index_retrieval.retrieval_plan import (
    RetrievalPlan,
    ScopeContext,
    canonical_capability,
    match_intent_signals,
)


def test_query_can_expose_multiple_capability_signals(temp_workspace):
    EntityManager(temp_workspace).upsert_entity(
        "work_a", "char_pair", name="甲乙", category="character"
    )

    result = QueryEngine(temp_workspace)._heuristic_decompose(
        "work_a", "甲乙后来为什么和另一方是什么关系"
    )

    capabilities = [signal.capability for signal in result.signals]
    assert result.intent == "causal_reason"
    assert capabilities[:3] == ["causal_reason", "timeline", "relationship"]
    assert all(0.0 <= signal.confidence <= 1.0 for signal in result.signals)


def test_work_config_merges_supported_terms_and_reports_unknown_capabilities(temp_workspace):
    work_dir = temp_workspace.projects_dir / "work_a"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "work.yaml").write_text(
        "work_id: work_a\n"
        "query_intent_keywords:\n"
        "  skill: [能力查询]\n"
        "  unsupported_route: [自定义查询]\n",
        encoding="utf-8",
    )
    EntityManager(temp_workspace).upsert_entity(
        "work_a", "char_actor", name="登记角色", category="character"
    )

    result = QueryEngine(temp_workspace)._heuristic_decompose(
        "work_a", "登记角色的能力查询"
    )

    assert result.intent == "skill"
    assert any(signal.capability == "skill" for signal in result.signals)
    assert any(item["code"] == "UNKNOWN_QUERY_CAPABILITY" for item in result.diagnostics)


def test_action_terms_are_not_global_stopwords(temp_workspace):
    EntityManager(temp_workspace).upsert_entity(
        "work_a", "char_actor", name="登记角色", category="character"
    )

    result = QueryEngine(temp_workspace)._heuristic_decompose(
        "work_a", "登记角色离开后发生什么"
    )

    assert "离开" in result.keywords
    assert "发生" in result.keywords


def test_short_or_unknown_patterns_do_not_become_capabilities():
    signals, diagnostics = match_intent_signals(
        "问题包含自定义查询",
        {"unknown": ("自定义查询",), "relationship": ("关",)},
        matched_by="config",
    )

    assert signals == []
    assert {item["code"] for item in diagnostics} == {
        "UNKNOWN_QUERY_CAPABILITY",
        "QUERY_CAPABILITY_PATTERN_TOO_SHORT",
    }


def test_retrieval_plan_keeps_scope_as_an_explicit_contract():
    scope = ScopeContext(
        work_id="work_a",
        source_id="source_a",
        source_version="v1",
        timeline_id="main",
    )
    plan = RetrievalPlan(scope=scope, capabilities=("general_search",))

    assert plan.scope.work_id == "work_a"
    assert plan.scope.source_version == "v1"
    assert canonical_capability("item_ownership") == "ownership"


def test_scope_rejects_partial_source_binding():
    try:
        ScopeContext(work_id="work_a", source_id="source-only")
    except ValueError as exc:
        assert "source_id" in str(exc)
    else:
        raise AssertionError("partial source scope was accepted")


def test_generic_capability_benchmark_keeps_signal_recall(temp_workspace):
    benchmark = [
        ("为什么会发生这个转折", "causal_reason"),
        ("为何出现这个结果", "causal_reason"),
        ("这件事的原因是什么", "causal_reason"),
        ("发生的缘故是什么", "causal_reason"),
        ("结果的缘由是什么", "causal_reason"),
        ("怎么会走到这里", "causal_reason"),
        ("因何改变", "causal_reason"),
        ("后来为什么离开", "causal_reason"),
        ("为何合作破裂", "causal_reason"),
        ("原因和经过是什么", "causal_reason"),
        ("时间线如何排列", "timeline"),
        ("事情经过如何", "timeline"),
        ("整个过程是什么", "timeline"),
        ("这段经历如何", "timeline"),
        ("后来发生了什么", "timeline"),
        ("事件顺序如何", "timeline"),
        ("时间线和关系如何", "timeline"),
        ("经过与原因", "timeline"),
        ("过程中的关系", "timeline"),
        ("经历的先后顺序", "timeline"),
        ("他们的关系是什么", "relationship"),
        ("双方是否对立", "relationship"),
        ("两人能否合作", "relationship"),
        ("这段情感如何", "relationship"),
        ("他看重谁", "relationship"),
        ("关系和原因是什么", "relationship"),
        ("对立关系如何形成", "relationship"),
        ("合作关系何时开始", "relationship"),
        ("情感关系的经过", "relationship"),
        ("看重与合作的顺序", "relationship"),
    ]
    engine = QueryEngine(temp_workspace)
    for question, expected in benchmark:
        decomposition = engine._heuristic_decompose("work_a", question)
        assert expected in {signal.capability for signal in decomposition.signals}
