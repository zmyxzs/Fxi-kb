"""Synthetic tests for the F7 final review boundary."""

from hashlib import sha256

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.as_of import AsOf
from fxi.knowledge.contracts import ContextView, ErrorCode, Scope, StoryCoordinate, Validity
from fxi.knowledge.objects import KnowledgeHead
from fxi.knowledge.proposal_service import ProposalRequest, ProposalService
from fxi.knowledge.review_service import ReviewRequest, ReviewService, ReviewServiceError
from fxi.knowledge.source_graph import SourceCompositeRef, SourceContributionRef
from fxi.knowledge.state_change_service import StateChangeService


def _scope(actor: str = "reviewer") -> Scope:
    return Scope(
        work_id="synthetic-work",
        branch_id="main",
        as_of=3,
        purpose="review",
        actor=actor,
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
        pov_id=None,
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
        content_hash=sha256_hex("synthetic-source"),
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
        purpose="writing-review",
        scope=_scope(),
    )


def _request(**updates: object) -> ReviewRequest:
    data: dict[str, object] = {
        "draft_ref": "draft-1",
        "draft_hash": sha256(b"synthetic-draft").hexdigest(),
        "coordinate": _coordinate(),
        "context_view": _context(),
        "knowledge_head": KnowledgeHead(
            work_id="synthetic-work",
            branch_id="main",
            knowledge_version="knowledge-v1",
            version_hash=sha256(b"head-v1").hexdigest(),
        ),
        "source_composite": _source(),
        "required_checks": ("syntax",),
        "semantic_reviewer_id": "semantic-reviewer",
        "semantic_reviewer_version": "v1",
    }
    data.update(updates)
    return ReviewRequest(**data)


class _Reviewer:
    reviewer_id = "semantic-reviewer"
    reviewer_version = "v1"

    def review(self, request: dict[str, object]) -> dict[str, object]:
        return {
            "checks": [
                {
                    "check_id": "syntax",
                    "status": "PASSED",
                    "required": True,
                    "findings": [],
                }
            ]
        }


def test_missing_semantic_reviewer_is_final_incomplete() -> None:
    report = ReviewService().review(_request())

    assert report.overall_status == "INCOMPLETE"
    assert report.blocking_findings[0]["code"] == "SEMANTIC_REVIEWER_MISSING"
    assert report.report_hash


def test_review_recomputes_final_status_and_preserves_f1_bindings() -> None:
    service = ReviewService(semantic_reviewer=_Reviewer())
    report = service.review(_request())

    assert report.overall_status == "PASSED"
    assert report.coordinate == _coordinate()
    assert report.context_hash == _context().view_hash
    assert report.report_hash
    assert not hasattr(report, "proposal_id")
    assert service.get(report.report_id).report_hash == report.report_hash


def test_review_rejects_foreign_context_before_reviewer_call() -> None:
    foreign = ContextView(
        view_id="foreign-context",
        work_id="other-work",
        branch_id="main",
        as_of=3,
        purpose="writing-review",
        scope=Scope(
            work_id="other-work",
            branch_id="main",
            as_of=3,
            purpose="writing-review",
            actor="reviewer",
        ),
    )
    with pytest.raises(ReviewServiceError) as error:
        ReviewService(semantic_reviewer=_Reviewer()).review(_request(context_view=foreign))

    assert error.value.code == ErrorCode.INVALID_SCOPE.value


def test_review_rejects_stale_source_version() -> None:
    with pytest.raises(ReviewServiceError) as error:
        ReviewService(semantic_reviewer=_Reviewer()).review(
            _request(source_version="source-v2")
        )

    assert error.value.code == ErrorCode.STALE_VERSION.value


def test_proposal_is_pending_and_binds_final_review_state_context_and_head() -> None:
    review = ReviewService(semantic_reviewer=_Reviewer()).review(_request())
    artifact_hash = sha256(b"artifact").hexdigest()
    evidence = {
        "evidence_id": "evidence-1",
        "source_snapshot_ref": "snapshot-1",
        "source_id": "synthetic-source",
        "source_version": "source-v1",
        "document_id": "document-1",
        "start": 0,
        "end": 4,
        "excerpt_hash": sha256(b"proof").hexdigest(),
        "normalization_version": "n1",
        "scope": _scope().model_dump(mode="json"),
    }
    changes = [{
        "change_id": "change-1",
        "type_uri": "synthetic.state@1",
        "before": {"value": 0},
        "after": {"value": 1},
        "delta": {"value": 1},
        "evidence_refs": [evidence],
        "claim_refs": ["claim-1"],
        "scope": _scope().model_dump(mode="json"),
        "narrative_order": 1,
        "source_artifact_hash": artifact_hash,
    }]
    state_change_set = StateChangeService().create(review, changes, artifact_hash)
    service = ProposalService()
    request = ProposalRequest(
        draft_ref=review.draft_ref,
        draft_hash=review.draft_hash,
        review=review,
        state_change_set=state_change_set,
        context_view=_context(),
        knowledge_head=_request().knowledge_head,
        source_composite=_source(),
    )

    proposal = service.create(request)
    replay = service.create(request)

    assert proposal.status == "PENDING_CONFIRMATION"
    assert proposal.review_ref == review.report_id
    assert proposal.state_change_set_ref == state_change_set.change_set_id
    assert replay.proposal_hash == proposal.proposal_hash
    assert service.get_record(proposal.proposal_id).context_view.view_hash == proposal.context_hash
