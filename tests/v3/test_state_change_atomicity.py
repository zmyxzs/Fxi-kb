"""Synthetic StateChangeSet validation tests; no story assets are used."""

from hashlib import sha256

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import ContextView, EvidenceRef, ErrorCode, Scope, StoryCoordinate, Validity
from fxi.knowledge.objects import KnowledgeHead
from fxi.knowledge.review_service import ReviewRequest, ReviewService
from fxi.knowledge.source_graph import SourceCompositeRef, SourceContributionRef
from fxi.knowledge.state_change_service import StateChangeService, StateChangeServiceError


ARTIFACT_HASH = sha256(b"synthetic-artifact").hexdigest()


def _scope() -> Scope:
    return Scope(
        work_id="synthetic-work",
        branch_id="main",
        as_of=3,
        purpose="state-change",
        actor="reviewer",
    )


def _coordinate() -> StoryCoordinate:
    return StoryCoordinate(
        work_id="synthetic-work",
        source_id="synthetic-source",
        source_version="source-v1",
        branch_id="main",
        as_of=3,
        chapter_index=1,
        scene_index=0,
        narrative_order=1,
        knowledge_version="knowledge-v1",
    )


def _source() -> SourceCompositeRef:
    contribution = SourceContributionRef(
        binding_id="binding-1",
        snapshot_id="snapshot-1",
        source_id="synthetic-source",
        source_version="source-v1",
        role="reference",
        priority=1,
        license="synthetic",
        branch_id="main",
        content_hash=sha256_hex("source"),
        validity=Validity(valid_from=0, valid_to=10),
    )
    return SourceCompositeRef(
        composite_id="composite-1",
        work_id="synthetic-work",
        branch_id="main",
        as_of=3,
        binding_ids=("binding-1",),
        source_snapshot_refs=("snapshot-1",),
        source_versions=("source-v1",),
        priorities=(1,),
        licenses=("synthetic",),
        contributions=(contribution,),
    )


def _context() -> ContextView:
    return ContextView(
        view_id="context-1",
        work_id="synthetic-work",
        branch_id="main",
        as_of=3,
        purpose="writing",
        scope=_scope(),
    )


def _review():
    request = ReviewRequest(
        draft_ref="draft-1",
        draft_hash=sha256(b"draft").hexdigest(),
        coordinate=_coordinate(),
        context_view=_context(),
        knowledge_head=KnowledgeHead(
            work_id="synthetic-work",
            branch_id="main",
            knowledge_version="knowledge-v1",
            version_hash=sha256(b"head").hexdigest(),
        ),
        source_composite=_source(),
        required_checks=("semantic",),
        semantic_reviewer_id="reviewer-1",
        semantic_reviewer_version="v1",
    )
    return ReviewService(
        semantic_reviewer=lambda _: {"checks": [{"check_id": "semantic", "status": "PASSED", "required": True}]},
        reviewer_id="reviewer-1",
        reviewer_version="v1",
    ).review(request)


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id="evidence-1",
        source_snapshot_ref="snapshot-1",
        source_id="synthetic-source",
        source_version="source-v1",
        document_id="document-1",
        start=0,
        end=5,
        excerpt_hash=sha256(b"proof").hexdigest(),
        normalization_version="n1",
        scope=_scope(),
    )


def _change(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "change_id": "change-1",
        "type_uri": "synthetic.state@1",
        "before": {"value": 0},
        "after": {"value": 1},
        "delta": {"value": 1},
        "evidence_refs": [_evidence().model_dump(mode="json")],
        "claim_refs": ["claim-1"],
        "scope": _scope().model_dump(mode="json"),
        "narrative_order": 1,
        "source_artifact_hash": ARTIFACT_HASH,
    }
    value.update(updates)
    return value


def test_state_change_requires_final_review_and_retains_explicit_refs() -> None:
    report = _review()
    result = StateChangeService().build(report, [_change()], ARTIFACT_HASH)

    assert result.status == "VALID"
    assert result.change_set.review_ref == report.report_id
    assert result.change_set.changes[0]["claim_refs"] == ("claim-1",)
    assert result.change_set.changes[0]["source_artifact_hash"] == ARTIFACT_HASH


def test_missing_evidence_is_incomplete_and_cannot_be_created_formally() -> None:
    report = _review()
    result = StateChangeService().build(report, [_change(evidence_refs=[])], ARTIFACT_HASH)

    assert result.status == "INCOMPLETE"
    assert "NO_EVIDENCE" in result.diagnostics
    with pytest.raises(StateChangeServiceError) as error:
        StateChangeService().create(report, [_change(evidence_refs=[])], ARTIFACT_HASH)
    assert error.value.code == ErrorCode.NO_EVIDENCE.value


def test_unresolved_change_is_never_formally_creatable() -> None:
    report = _review()
    result = StateChangeService().build(report, [_change(status="UNRESOLVED")], ARTIFACT_HASH)

    assert result.status == "INCOMPLETE"
    assert "UNRESOLVED" in result.diagnostics
