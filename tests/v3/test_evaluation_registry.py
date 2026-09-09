from __future__ import annotations

from hashlib import sha256

from fxi.knowledge.candidate_service import CandidateService
from fxi.knowledge.contracts import CandidateEnvelope, EvaluationPolicy, EvidenceRef, Scope
from fxi.knowledge.evaluation_service import EvaluationService


def _hash(value: object) -> str:
    return sha256(str(value).encode()).hexdigest()


def _candidate(candidate_id: str, payload: dict[str, str]) -> CandidateEnvelope:
    scope = Scope(work_id="work-a", branch_id="branch-a", as_of=1, purpose="test", actor="actor-a")
    evidence = EvidenceRef(
        evidence_id=f"evidence-{candidate_id}",
        source_snapshot_ref="snapshot-a",
        source_id="source-a",
        source_version="version-a",
        document_id="document-a",
        start=0,
        end=4,
        excerpt_hash=_hash("text"),
        normalization_version="norm-a",
        scope=scope,
    )
    return CandidateEnvelope(
        candidate_id=candidate_id,
        artifact_kind="artifact-generic",
        work_id="work-a",
        branch_id="branch-a",
        source_snapshot_ref="snapshot-a",
        evidence_refs=(evidence,),
        input_hash=_hash(f"input-{candidate_id}"),
        extractor_id="extractor-a",
        schema_version="schema-a",
        domain_package_version="domain-a",
        policy_hash=_hash("policy-a"),
        payload=payload,
    )


def _policy(*, reviewer: bool) -> EvaluationPolicy:
    return EvaluationPolicy(
        policy_id="policy-a",
        policy_hash=_hash("policy-a"),
        evaluator_id="evaluator-a",
        evaluator_version="version-a",
        require_semantic_reviewer=reviewer,
    )


def test_evaluation_without_reviewer_is_queryable_but_incomplete() -> None:
    candidates = CandidateService()
    candidate = _candidate("candidate-a", {"opaque": "value"})
    candidates.submit(candidate)
    evaluations = EvaluationService(candidates)

    manifest = evaluations.evaluate(candidate.candidate_id, _policy(reviewer=True))

    assert manifest.status == "INCOMPLETE"
    assert manifest.input_hash == candidate.input_hash
    assert manifest.semantic_reviewer is None
    assert evaluations.get(manifest.evaluation_id).result_hash == manifest.result_hash
    assert candidates.get(candidate.candidate_id).status == "CANDIDATE"


def test_evaluation_registry_retains_reviewer_duplicate_and_adaptation() -> None:
    candidates = CandidateService()
    first = _candidate("candidate-a", {"opaque": "value"})
    second = _candidate("candidate-b", {"opaque": "value"})
    candidates.submit(first)
    candidates.submit(second)

    def evaluate(candidate: CandidateEnvelope, policy: EvaluationPolicy) -> dict[str, object]:
        return {
            "semantic_reviewer": "reviewer-a",
            "suitability": "ADAPT_REQUIRED",
            "required_adaptation": ["adaptation-a"],
            "uncertainty": {"confidence": 0.5},
            "duplicate": {"same_core": True, "surface_similarity_risk": 0.8, "fingerprint": "core-a"},
        }

    registry = EvaluationService(candidates, evaluator=evaluate, semantic_reviewer=evaluate)
    first_manifest = registry.evaluate(first.candidate_id, _policy(reviewer=True))
    second_manifest = registry.evaluate(second.candidate_id, _policy(reviewer=True))

    assert first_manifest.status == "EVALUATED"
    assert first_manifest.required_adaptation == ("adaptation-a",)
    assert second_manifest.duplicate_cluster_ref == first_manifest.duplicate_cluster_ref
    assert second_manifest.variant_of == first.candidate_id
    assert second_manifest.same_core is True
    assert registry.get_for_candidate(second.candidate_id) == (second_manifest,)
