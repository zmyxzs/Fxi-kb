"""Synthetic F5 tests: branch heads, POD filtering, and conflict approval."""

from hashlib import sha256

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.branch_service import (
    BranchService,
    BranchServiceError,
    filter_post_divergence_claims,
)
from fxi.knowledge.branches import DivergenceAnchor
from fxi.knowledge.conflict_sets import ConflictDecision, ConflictError, ConflictService
from fxi.knowledge.contracts import Actor, ApprovalRef, Scope
from fxi.knowledge.objects import Claim, KnowledgeHead


def _scope(branch: str = "branch-main", actor: str = "actor-test") -> Scope:
    return Scope(work_id="work-synthetic", branch_id=branch, as_of=1, purpose="test", actor=actor)


def _claim(claim_id: str, value: str, *, as_of: int = 1) -> Claim:
    return Claim(
        claim_id=claim_id,
        subject_ref="object-1",
        predicate="has_value",
        value=value,
        object_type_uri="example.note@1",
        scope=Scope(work_id="work-synthetic", branch_id="branch-main", as_of=as_of, purpose="test", actor="actor-test"),
    )


def test_branch_parent_pod_filter_and_head_cas() -> None:
    service = BranchService(created_by="actor-test")
    root = service.create("work-synthetic", None, None)
    child = service.create(
        "work-synthetic",
        root.branch_id,
        DivergenceAnchor(source_snapshot_ref="snapshot-canon", narrative_order=3, reason="synthetic POD"),
    )
    assert child.parent_branch == root.branch_id
    assert child.divergence is not None
    current = service.head("work-synthetic", child.branch_id)
    replacement = KnowledgeHead(
        work_id=child.work_id,
        branch_id=child.branch_id,
        knowledge_version="version-next",
        version_hash=sha256_hex({"version": "next"}),
        cas_revision=1,
    )
    assert service.compare_and_set_head("work-synthetic", child.branch_id, current, replacement).cas_revision == 1
    with pytest.raises(BranchServiceError) as stale:
        service.compare_and_set_head("work-synthetic", child.branch_id, current, replacement)
    assert stale.value.code == "STALE_VERSION"

    before = _claim("claim-before", "before")
    after = _claim("claim-after", "after")
    static_after = _claim("claim-static", "static")
    retained = filter_post_divergence_claims(
        (before, after, static_after),
        child,
        narrative_orders={"claim-before": 2, "claim-after": 4, "claim-static": 4},
        static_claim_refs=("claim-static",),
    )
    assert tuple(claim.claim_id for claim in retained) == ("claim-before", "claim-static")
    context = service.context_ref(
        "work-synthetic",
        child.branch_id,
        (before, after, static_after),
        narrative_orders={"claim-before": 2, "claim-after": 4, "claim-static": 4},
        static_claim_refs=("claim-static",),
    )
    assert context.post_divergence_filtered is True
    assert context.filtered_claim_refs == ("claim-after",)


def test_conflict_requires_approval_and_is_idempotent_without_claim_mutation() -> None:
    first = _claim("claim-a", "left")
    second = _claim("claim-b", "right")
    service = ConflictService(policy={"review": "human"})
    conflicts = service.detect((second, first))
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.status == "UNRESOLVED"
    assert len(conflict.candidate_solutions) == 2

    scope = _scope()
    actor = Actor(actor_id="reviewer", role="approver", scope=Scope(work_id="work-synthetic", branch_id="branch-main", as_of=1, purpose="review", actor="reviewer"))
    decision = ConflictDecision(
        decision_id="decision-1",
        conflict_id=conflict.conflict_id,
        selected_candidate_id=conflict.candidate_solutions[0].candidate_id,
        rationale="synthetic human choice",
        scope=Scope(work_id="work-synthetic", branch_id="branch-main", as_of=1, purpose="review", actor="reviewer"),
        actor=actor,
        idempotency_key="idem-1",
    )
    approval = ApprovalRef(
        approval_id="approval-1",
        proposal_id="decision-1",
        actor=actor,
        approval_hash=sha256("approval-1".encode()).hexdigest(),
    )
    resolved = service.resolve(conflict.conflict_id, decision, approval)
    assert resolved.status == "APPROVED"
    assert service.get(conflict.conflict_id).status == "RESOLVED"
    assert first.status == "ASSERTED" and second.status == "ASSERTED"
    replay = service.resolve(conflict.conflict_id, decision, approval)
    assert replay.idempotent_replay is True
    with pytest.raises(ConflictError) as mismatch:
        different = ConflictDecision.model_validate({
            **decision.model_dump(mode="python"),
            "idempotency_key": "idem-2",
            "decision_hash": "",
        })
        service.resolve(conflict.conflict_id, different, approval)
    assert mismatch.value.code == "IDEMPOTENCY_CONFLICT"
