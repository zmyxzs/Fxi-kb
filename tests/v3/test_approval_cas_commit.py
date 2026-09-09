"""Synthetic approval/CAS/atomic commit tests for Fxi v3."""

from hashlib import sha256

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.approval_service import (
    ApprovalRequest,
    ApprovalService,
    ApprovalServiceError,
)
from fxi.knowledge.commit_service import (
    CommitExpectation,
    CommitService,
    CommitServiceError,
    InMemoryCommitRepository,
)
from fxi.knowledge.contracts import Actor, ContextView, EvidenceRef, ErrorCode, Scope, StoryCoordinate, Validity
from fxi.knowledge.objects import KnowledgeHead
from fxi.knowledge.proposal_service import ProposalRequest, ProposalService
from fxi.knowledge.review_service import ReviewRequest, ReviewService
from fxi.knowledge.source_graph import SourceCompositeRef, SourceContributionRef
from fxi.knowledge.state_change_service import StateChangeService


def _scope(actor: str = "reviewer") -> Scope:
    return Scope(
        work_id="synthetic-work",
        branch_id="main",
        as_of=3,
        purpose="commit",
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
        draft_hash=sha256(b"synthetic-draft").hexdigest(),
        coordinate=_coordinate(),
        context_view=_context(),
        knowledge_head=KnowledgeHead(
            work_id="synthetic-work",
            branch_id="main",
            knowledge_version="knowledge-v1",
            version_hash=sha256(b"head-v1").hexdigest(),
        ),
        source_composite=_source(),
        required_checks=("semantic",),
        semantic_reviewer_id="semantic-reviewer",
        semantic_reviewer_version="v1",
    )
    return ReviewService(
        semantic_reviewer=lambda _: {"checks": [{"check_id": "semantic", "status": "PASSED", "required": True}]},
        reviewer_id="semantic-reviewer",
        reviewer_version="v1",
    ).review(request)


def _state_changes(review):
    evidence = EvidenceRef(
        evidence_id="evidence-1",
        source_snapshot_ref="snapshot-1",
        source_id="synthetic-source",
        source_version="source-v1",
        document_id="document-1",
        start=0,
        end=4,
        excerpt_hash=sha256(b"proof").hexdigest(),
        normalization_version="n1",
        scope=_scope(),
    )
    artifact_hash = sha256(b"synthetic-artifact").hexdigest()
    return StateChangeService().create(
        review,
        [{
            "change_id": "change-1",
            "type_uri": "synthetic.state@1",
            "before": {"value": 0},
            "after": {"value": 1},
            "delta": {"value": 1},
            "evidence_refs": [evidence.model_dump(mode="json")],
            "claim_refs": ["claim-1"],
            "scope": _scope().model_dump(mode="json"),
            "narrative_order": 1,
            "source_artifact_hash": artifact_hash,
        }],
        artifact_hash,
    )


def _flow():
    review = _review()
    state_change_set = _state_changes(review)
    proposal_service = ProposalService()
    proposal = proposal_service.create(ProposalRequest(
        draft_ref=review.draft_ref,
        draft_hash=review.draft_hash,
        review=review,
        state_change_set=state_change_set,
        context_view=_context(),
        knowledge_head=KnowledgeHead(
            work_id="synthetic-work",
            branch_id="main",
            knowledge_version="knowledge-v1",
            version_hash=sha256(b"head-v1").hexdigest(),
        ),
        source_composite=_source(),
    ))
    actor = Actor(actor_id="approver-1", role="approver", scope=_scope("approver-1"))
    approval_service = ApprovalService(proposal_service=proposal_service, clock=lambda: 100.0)
    approval = approval_service.create(
        ApprovalRequest(proposal=proposal, idempotency_key="approval-request-1", expires_at=200),
        actor,
    )
    repository = InMemoryCommitRepository()
    expectation = CommitExpectation(
        work_id="synthetic-work",
        branch_id="main",
        expected_knowledge_version="knowledge-v1",
        chapter_version="chapter-v1",
        idempotency_key="commit-request-1",
        actor=actor,
        review=review,
        state_change_set=state_change_set,
        context_view=_context(),
        knowledge_head=KnowledgeHead(
            work_id="synthetic-work",
            branch_id="main",
            knowledge_version="knowledge-v1",
            version_hash=sha256(b"head-v1").hexdigest(),
        ),
        source_composite=_source(),
        chapter={
            "text": "synthetic approved draft",
            "text_hash": sha256(b"synthetic approved draft").hexdigest(),
        },
        projection_kinds=("fts", "wiki"),
    )
    service = CommitService(
        repository=repository,
        proposal_service=proposal_service,
        approval_service=approval_service,
    )
    return service, repository, proposal, approval_service, approval, expectation, actor


def test_writer_cannot_create_approval_and_expired_approval_is_rejected() -> None:
    _, _, proposal, approval_service, _, _, _ = _flow()
    writer = Actor(actor_id="writer-1", role="writer", scope=_scope("writer-1"))
    service = ApprovalService(proposal_service=approval_service.proposal_service, clock=lambda: 100.0)
    with pytest.raises(ApprovalServiceError) as writer_error:
        service.create(ApprovalRequest(proposal=proposal), writer)
    assert writer_error.value.code == ErrorCode.AUTHORIZATION_FAILED.value

    approver = Actor(actor_id="approver-2", role="approver", scope=_scope("approver-2"))
    with pytest.raises(ApprovalServiceError) as expiry_error:
        service.create(ApprovalRequest(proposal=proposal, expires_at=100), approver)
    assert expiry_error.value.code == ErrorCode.APPROVAL_EXPIRED.value


def test_commit_is_cas_checked_idempotent_and_approval_is_consumed() -> None:
    service, repository, proposal, approval_service, approval, expectation, actor = _flow()

    receipt = service.commit(proposal, approval, expectation)
    replay = service.commit(proposal, approval, expectation)

    assert receipt.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.commit_id == receipt.commit_id
    assert repository.get_head("synthetic-work", "main").knowledge_version == receipt.new_knowledge_version
    assert len(repository.knowledge_versions) == 1
    assert len(repository.state_changes) == 1
    assert len(repository.chapters) == 1
    assert repository.projection_tasks[f"projection-wiki-{receipt.new_knowledge_version}"]["rebuildable"] is True
    assert approval_service.get_record(approval.approval_id).consumed is True

    altered = expectation.model_copy(update={"chapter": {"text": "different"}})
    with pytest.raises(CommitServiceError) as conflict_error:
        service.commit(proposal, approval, altered)
    assert conflict_error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT.value


def test_stale_head_is_rejected_before_any_authority_write() -> None:
    service, repository, proposal, approval_service, approval, expectation, _ = _flow()
    repository.seed_head(KnowledgeHead(
        work_id="synthetic-work",
        branch_id="main",
        knowledge_version="knowledge-v0",
        version_hash=sha256(b"other-head").hexdigest(),
    ))

    with pytest.raises(CommitServiceError) as error:
        service.commit(proposal, approval, expectation)

    assert error.value.code == ErrorCode.CAS_CONFLICT.value
    assert len(repository.commits) == 0
    assert approval_service.get_record(approval.approval_id).consumed is False


def test_storage_failure_rolls_back_chapter_state_head_receipt_and_approval() -> None:
    service, _, proposal, approval_service, approval, expectation, _ = _flow()

    class FailingRepository(InMemoryCommitRepository):
        def store(self, **kwargs):  # type: ignore[no-untyped-def]
            super().store(**kwargs)
            raise RuntimeError("synthetic storage failure")

    failing_repository = FailingRepository()
    service.repository = failing_repository
    with pytest.raises(RuntimeError):
        service.commit(proposal, approval, expectation)

    assert failing_repository.heads == {}
    assert failing_repository.knowledge_versions == {}
    assert failing_repository.commits == {}
    assert failing_repository.receipts == {}
    assert failing_repository.chapters == {}
    assert failing_repository.state_changes == []
    assert failing_repository.projection_tasks == {}
    assert approval_service.get_record(approval.approval_id).consumed is False
