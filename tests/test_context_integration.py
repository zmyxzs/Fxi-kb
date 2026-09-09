from fxi.character_knowledge.knowledge_tracker import KnowledgeTracker
from fxi.index_retrieval.context_pruner import ContextPruner
from fxi.materials_skills.candidate_store import package_hash
import pytest


def _insert_claim(config, family_key, claim_id, statement, scope):
    from fxi.storage.sqlite_client import DatabaseClient

    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO claim_families
                (family_key, claim_family_id, owner_id, scope, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (family_key, family_key, "test-owner", scope, "now"),
        )
        cur.execute(
            """
            INSERT INTO claim_versions
                (family_key, claim_id, claim_family_id, version, work_id,
                 status, statement, semantic_hash, created_at)
            VALUES (?, ?, ?, 1, 'work_a', 'accepted', ?, ?, 'now')
            """,
            (family_key, claim_id, family_key, statement, f"hash-{claim_id}"),
        )


def test_versioned_context_filters_pov_claims_and_pod_events(temp_workspace):
    _insert_claim(
        temp_workspace,
        "future-family",
        "secret_future",
        "第五章才揭示的凶手身份",
        "character_secret",
    )
    _insert_claim(
        temp_workspace,
        "public-family",
        "public_fact",
        "城门在雨夜关闭",
        "public",
    )
    KnowledgeTracker(temp_workspace).record_learned_claim(
        "work_a",
        "pov_hero",
        "secret_future",
        narrative_order=5,
    )

    style_package = {
        "version": "style-v3",
        "style_rules": ["规则一", "规则二", "规则三", "规则四"],
    }
    result = ContextPruner(temp_workspace).assemble_writing_context(
        work_id="work_a",
        source_id="source-a",
        source_version="source-v1",
        knowledge_version="knowledge-v7",
        style_package_version="style-v3",
        style_selection="approved",
        style_package={
            "candidate_id": "candidate-v3",
            "version": "style-v3",
            "status": "APPROVED",
            "package_hash": package_hash(style_package),
            "package": style_package,
        },
        pov_character_id="pov_hero",
        current_narrative_order=4,
        scene_beat={"scene_type": "dialogue"},
        canon_events=[
            {"event_id": "before", "narrative_order": 8, "summary": "分歧前动态"},
            {"event_id": "after", "narrative_order": 12, "summary": "分歧后动态"},
            {
                "event_id": "lore-after",
                "narrative_order": 13,
                "summary": "分歧后仍有效的世界法则",
                "is_static_lore": True,
            },
        ],
        divergence_narrative_order=10,
        budget=12,
    )

    claim_ids = {claim["claim_id"] for claim in result["claims"]}
    event_ids = {event["event_id"] for event in result["causal_events"]}
    assert "secret_future" not in claim_ids
    assert "public_fact" in claim_ids
    assert event_ids == {"before", "lore-after"}
    assert result["versions"] == {
        "source_version": "source-v1",
        "knowledge_version": "knowledge-v7",
        "style_package_version": "style-v3",
    }
    assert result["completeness_status"] == "COMPLETE"
    assert result["package_hash"] == package_hash(style_package)
    assert result["style_view_hash"] == result["style_view"]["view_hash"]
    assert result["view_hash"]
    assert "approved" in result["selection_reason"]
    assert any("POD filtered" in item for item in result["pruning_log"])
    assert any(item.startswith("budget ") for item in result["pruning_log"])
    assert "secret_future" not in result["assembled_context"]


def test_context_missing_versions_and_style_are_incomplete(temp_workspace):
    result = ContextPruner(temp_workspace).assemble_writing_context(
        work_id="work_a",
        source_id="source-a",
        mode="freeform",
        style_selection="approved",
    )

    assert result["completeness_status"] == "INCOMPLETE"
    assert {
        "source_version",
        "knowledge_version",
        "style_package_version",
        "style_package",
    }.issubset(set(result["missing_required"]))
    assert result["assembled_context"]


def test_style_selection_qualifies_conditions_and_preserves_method_fields(temp_workspace):
    package = {
        "version": "style-candidate-v1",
        "style_rules": [
            {
                "method_id": "dialogue-delay",
                "operation": "先承接误判，再延迟解释",
                "condition": {"scene_type": "dialogue"},
            },
            {
                "method_id": "battle-only",
                "operation": "战斗短句爆发",
                "condition": {"scene_type": "battle"},
            },
        ],
    }
    result = ContextPruner(temp_workspace).assemble_writing_context(
        work_id="work_a",
        source_id="source-a",
        source_version="source-v1",
        knowledge_version="knowledge-v1",
        style_package_version="style-candidate-v1",
        style_selection="evaluation_candidate",
        style_package={
            "candidate_id": "candidate-v1",
            "version": "style-candidate-v1",
            "status": "EVALUATION_CANDIDATE",
            "package_hash": package_hash(package),
            "package": package,
        },
        mode="freeform",
        purpose="drafting",
        scene_beat={"scene_type": "dialogue"},
    )

    assert [item["method_id"] for item in result["style_rules"]] == ["dialogue-delay"]
    assert result["style_rules"][0]["operation"] == "先承接误判，再延迟解释"
    assert [item["asset_id"] for item in result["style_view"]["rejected"]] == ["battle-only"]
    assert result["style_view"]["rejected"][0]["selected"] is False


def test_approved_selection_rejects_unapproved_package(temp_workspace):
    package = {"version": "style-v1", "style_rules": ["候选规则"]}
    with pytest.raises(ValueError, match="STYLE_SELECTION_STATUS_CONFLICT"):
        ContextPruner(temp_workspace).assemble_writing_context(
            work_id="work_a",
            source_id="source-a",
            source_version="source-v1",
            knowledge_version="knowledge-v1",
            style_package_version="style-v1",
            style_selection="approved",
            style_package={
                "version": "style-v1",
                "status": "EVALUATION_CANDIDATE",
                "package_hash": package_hash(package),
                "package": package,
            },
            mode="freeform",
        )
