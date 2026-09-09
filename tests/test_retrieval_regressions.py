import pytest

from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.storage.sqlite_client import DatabaseClient


def _insert_claim(
    config,
    family_key: str,
    claim_id: str,
    work_id: str | None,
    statement: str,
    family_work_id: str | None = None,
) -> None:
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO claim_families
                (family_key, claim_family_id, work_id, owner_id, scope, created_at)
            VALUES (?, ?, ?, ?, 'public', 'now')
            """,
            (family_key, family_key, family_work_id, "test-owner"),
        )
        cur.execute(
            """
            INSERT INTO claim_versions
                (family_key, claim_id, claim_family_id, version, work_id,
                 status, statement, semantic_hash, created_at)
            VALUES (?, ?, ?, 1, ?, 'accepted', ?, ?, 'now')
            """,
            (family_key, claim_id, family_key, work_id, statement, f"hash-{claim_id}"),
        )


def test_context_claims_require_explicit_work_scope(temp_workspace):
    from fxi.index_retrieval.context_pruner import ContextPruner

    _insert_claim(temp_workspace, "current", "claim_current", "work_a", "当前作品事实", "work_a")
    _insert_claim(temp_workspace, "null-work", "claim_null", None, "未归属作品的事实")
    _insert_claim(temp_workspace, "other", "claim_other", "work_b", "其他作品事实", "work_b")

    result = ContextPruner(temp_workspace).assemble_writing_context(
        work_id="work_a",
        source_id="source-a",
        source_version="source-v1",
        knowledge_version="knowledge-v1",
        mode="freeform",
        current_narrative_order=5,
    )

    assert {claim["claim_id"] for claim in result["claims"]} == {"claim_current"}
    assert "claim_null" not in result["assembled_context"]
    assert "claim_other" not in result["assembled_context"]


def test_context_mutation_errors_are_visible(temp_workspace):
    from fxi.index_retrieval.context_pruner import ContextPruner

    pruner = ContextPruner(temp_workspace)

    def fail(*args, **kwargs):
        raise RuntimeError("mutation ledger unavailable")

    pruner.mutation_ledger.list_mutations = fail
    with pytest.raises(RuntimeError, match="mutation ledger unavailable"):
        pruner.assemble_writing_context(
            work_id="work_a",
            source_id="source-a",
            source_version="source-v1",
            knowledge_version="knowledge-v1",
            mode="freeform",
        )


def test_query_evidence_keeps_state_claim_mutation_and_continuity_facts(temp_workspace):
    from fxi.domain.entities import EntityManager
    from fxi.domain.mutation_ledger import MutationLedger
    from fxi.index_retrieval.query_engine import QueryDecomposition, QueryEngine
    from fxi.state_ledger.calculator import LedgerCalculator
    from fxi.timeline.continuity import ContinuityManager

    EntityManager(temp_workspace).upsert_entity(
        "work_a", "char_state", name="状态角色", category="character"
    )
    LedgerCalculator(temp_workspace).record_event(
        "work_a",
        "char_state",
        "gold",
        7.0,
        "scene-state",
        narrative_order=4,
        reason="状态增加",
    )
    _insert_claim(temp_workspace, "query-family", "query-claim", "work_a", "问答事实", "work_a")
    MutationLedger(temp_workspace).record_mutation(
        work_id="work_a",
        trigger_chapter=3,
        cause_event="scene-state",
        entity_name_or_id="char_state",
        mutation_type="state_override",
        target_name="gold",
        mutation_id="mutation-query",
    )
    ContinuityManager(temp_workspace).record_chapter(
        work_id="work_a",
        chapter_index=4,
        title="第四章",
        tail_snippet="尾声",
        ending_location="城门",
        active_characters=["char_state"],
        ending_situation="状态已改变",
        unresolved_hooks=["下一章悬念"],
    )

    engine = QueryEngine(temp_workspace)
    evidence = engine.retrieve_evidence(
        "work_a",
        QueryDecomposition(target_entities=["状态角色"], keywords=["状态"]),
        top_k_scenes=1,
        narrative_order=5,
        divergence_narrative_order=5,
    )

    assert evidence["claims"][0]["claim_id"] == "query-claim"
    assert evidence["state_ledger"]["available"] is True
    assert evidence["states"][0]["computed_value"] == 7.0
    assert evidence["mutations"][0]["mutation_id"] == "mutation-query"
    assert evidence["continuity"]["chapter"]["chapter_index"] == 4
    answer = engine.synthesize_answer("work_a", "状态如何？", evidence, use_mock=True)
    assert "状态账本" in answer
    assert "7.0" in answer


def test_fts_uses_jieba_for_writes_and_surfaces_db_errors(temp_workspace, monkeypatch):
    fts = ChineseFTS(temp_workspace)
    monkeypatch.setattr(fts.__class__.__module__ + ".jieba.cut", lambda *args, **kwargs: iter(["统一词"]))
    fts.index_scene("scene-fts", "work_a", 1, "原始中文正文")

    with DatabaseClient(temp_workspace.sqlite_path).get_connection() as conn:
        row = conn.execute(
            "SELECT segmented_content FROM fts_scenes WHERE scene_uuid = ?",
            ("scene-fts",),
        ).fetchone()
    assert row["segmented_content"] == "统一词"

    class BrokenDatabase:
        def get_connection(self):
            raise RuntimeError("fts database unavailable")

    fts.db_client = BrokenDatabase()
    with pytest.raises(RuntimeError, match="fts database unavailable"):
        fts.search("统一词", work_id="work_a")
