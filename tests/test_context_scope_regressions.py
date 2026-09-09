from fxi.index_retrieval.context_pruner import ContextPruner


def _assemble(pruner, **overrides):
    request = {
        "work_id": "work_a",
        "source_id": "source-a",
        "source_version": "source-v1",
        "knowledge_version": "knowledge-v1",
        "mode": "freeform",
        "current_narrative_order": 4,
        "claims": [],
    }
    request.update(overrides)
    return pruner.assemble_writing_context(**request)


def test_scope_claim_with_secret_scope_cannot_bypass_pov_filter(temp_workspace):
    result = _assemble(
        ContextPruner(temp_workspace),
        claims=[
            {
                "claim_id": "unknown-secret",
                "statement": "观察者不应知道的秘密",
                "status": "accepted",
                "work_id": "work_a",
                "family_work_id": "work_a",
                "scope": "character_secret",
                "source_id": "source-a",
                "source_version": "source-v1",
                "knowledge_version": "knowledge-v1",
            }
        ],
    )

    assert result["claims"] == []
    assert "unknown-secret" not in result["assembled_context"]


def test_claim_evidence_from_another_work_is_not_in_context(temp_workspace):
    result = _assemble(
        ContextPruner(temp_workspace),
        claims=[
            {
                "claim_id": "cross-work-evidence",
                "statement": "另一作品的证据不能证明本作品事实",
                "status": "accepted",
                "work_id": "work_a",
                "family_work_id": "work_a",
                "scope": "public",
                "source_id": "source-a",
                "source_version": "source-v1",
                "knowledge_version": "knowledge-v1",
                "evidence": [
                    {
                        "work_id": "work_b",
                        "source_id": "source-a",
                        "source_version": "source-v1",
                    }
                ],
            }
        ],
    )

    assert result["claims"] == []
    assert "cross-work-evidence" not in result["assembled_context"]


def test_planned_revelations_are_scoped_and_pov_safe(temp_workspace):
    from fxi.character_knowledge.knowledge_tracker import KnowledgeTracker

    KnowledgeTracker(temp_workspace).record_learned_claim(
        "work_a",
        "hero",
        "known-secret",
        narrative_order=4,
    )
    result = _assemble(
        ContextPruner(temp_workspace),
        claims=[
            {
                "claim_id": "known-secret",
                "statement": "已揭示的秘密",
                "status": "accepted",
                "work_id": "work_a",
                "family_work_id": "work_a",
                "scope": "character_secret",
                "is_secret": True,
                "source_id": "source-a",
                "source_version": "source-v1",
                "knowledge_version": "knowledge-v1",
            }
        ],
        pov_character_id="hero",
        current_narrative_order=4,
        scene_beat={
            "planned_revelations": [
                {
                    "id": "foreign-revelation",
                    "work_id": "work_b",
                    "claim_id": "known-secret",
                },
                {
                    "id": "unknown-revelation",
                    "work_id": "work_a",
                    "claim_id": "unknown-secret",
                    "scope": "character_secret",
                },
                {
                    "id": "valid-revelation",
                    "work_id": "work_a",
                    "claim_id": "known-secret",
                    "source_id": "source-a",
                    "source_version": "source-v1",
                    "knowledge_version": "knowledge-v1",
                    "narrative_order": 4,
                },
            ]
        },
    )

    assert [item["id"] for item in result["planned_revelations"]] == ["valid-revelation"]
    assert "foreign-revelation" not in str(result["facts"])
    assert "unknown-revelation" not in str(result["facts"])
