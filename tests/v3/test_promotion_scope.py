from __future__ import annotations

from hashlib import sha256

import pytest

from fxi.knowledge.candidate_service import CandidateService
from fxi.knowledge.contracts import Actor, ApprovalRef, CandidateEnvelope, EvaluationPolicy, EvidenceRef, Scope
from fxi.knowledge.decision_service import DecisionService, DecisionServiceError
from fxi.knowledge.decisions import DecisionAction
from fxi.knowledge.evaluation_service import EvaluationService
from fxi.knowledge.promotion_service import InMemoryKnowledgeRepository, PromotionService, PromotionServiceError


def _hash(value: object) -> str:
    return sha256(str(value).encode()).hexdigest()


def _scope(work: str = "work-a", branch: str = "branch-a", actor: str = "reviewer-a") -> Scope:
    return Scope(work_id=work, branch_id=branch, as_of=1, purpose="test", actor=actor)


def _candidate() -> CandidateEnvelope:
    return CandidateEnvelope(
        candidate_id="candidate-a",
        artifact_kind="artifact-generic",
        work_id="work-a",
        branch_id="branch-a",
        source_snapshot_ref="snapshot-a",
        evidence_refs=(
            EvidenceRef(
                evidence_id="evidence-a",
                source_snapshot_ref="snapshot-a",
                source_id="source-a",
                source_version="version-a",
                document_id="document-a",
                start=0,
                end=4,
                excerpt_hash=_hash("text"),
                normalization_version="norm-a",
                scope=_scope(),
            ),
        ),
        input_hash=_hash("input-a"),
        extractor_id="extractor-a",
        schema_version="schema-a",
        domain_package_version="domain-a",
        policy_hash=_hash("policy-a"),
        payload={"opaque": "value"},
    )


def test_promotion_requires_scope_decision_approval_and_supports_cas_idempotency() -> None:
    candidates = CandidateService()
    candidate = _candidate()
    candidates.submit(candidate)
    policy = EvaluationPolicy(
        policy_id="policy-a",
        policy_hash=_hash("policy-a"),
        evaluator_id="evaluator-a",
        evaluator_version="version-a",
        require_semantic_reviewer=False,
    )
    evaluations = EvaluationService(
        candidates,
        evaluator=lambda candidate, policy: {"suitability": "SUITABLE"},
    )
    manifest = evaluations.evaluate(candidate.candidate_id, policy)
    decisions = DecisionService(candidates, evaluations)
    actor = Actor(actor_id="reviewer-a", role="reviewer", scope=_scope())
    decision = decisions.decide(candidate.candidate_id, DecisionAction.APPROVE, actor)
    repository = InMemoryKnowledgeRepository()
    promotions = PromotionService(candidates, evaluations, repository=repository, decision_service=decisions)
    approval = ApprovalRef(
        approval_id="approval-a",
        proposal_id=candidate.candidate_id,
        actor=actor,
        approval_hash=_hash("approval-a"),
    )
    promotions.register_approval(approval)

    receipt = promotions.promote(candidate.candidate_id, approval, "genesis")
    replay = promotions.promote(candidate.candidate_id, approval, "genesis")

    assert decision.evaluation_ref == manifest.evaluation_id
    assert receipt.new_knowledge_version in repository.versions
    assert repository.heads[("work-a", "branch-a")].knowledge_version == receipt.new_knowledge_version
    assert repository.projection_tasks[receipt.projection_task_refs[0]]["status"] == "PENDING"
    assert replay.idempotent_replay is True
    assert len(repository.versions) == 1
    assert candidates.get(candidate.candidate_id).status == "CANDIDATE"


def test_decision_and_promotion_reject_foreign_scope_or_unregistered_approval() -> None:
    candidates = CandidateService()
    candidate = _candidate()
    candidates.submit(candidate)
    policy = EvaluationPolicy(
        policy_id="policy-a",
        policy_hash=_hash("policy-a"),
        evaluator_id="evaluator-a",
        evaluator_version="version-a",
        require_semantic_reviewer=False,
    )
    evaluations = EvaluationService(candidates, evaluator=lambda candidate, policy: {"suitability": "SUITABLE"})
    evaluations.evaluate(candidate.candidate_id, policy)
    decisions = DecisionService(candidates, evaluations)
    foreign_actor = Actor(actor_id="reviewer-b", role="reviewer", scope=_scope("work-b", "branch-b", "reviewer-b"))
    with pytest.raises(DecisionServiceError):
        decisions.decide(candidate.candidate_id, DecisionAction.APPROVE, foreign_actor)

    promotions = PromotionService(candidates, evaluations, decision_service=decisions)
    valid_actor = Actor(actor_id="reviewer-a", role="reviewer", scope=_scope())
    approval = ApprovalRef(
        approval_id="approval-a",
        proposal_id=candidate.candidate_id,
        actor=valid_actor,
        approval_hash=_hash("approval-a"),
    )
    with pytest.raises(PromotionServiceError) as approval_error:
        promotions.promote(candidate.candidate_id, approval, "genesis")
    assert approval_error.value.code == "APPROVAL_REQUIRED"
