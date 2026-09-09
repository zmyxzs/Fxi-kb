from __future__ import annotations

from hashlib import sha256

import pytest

from fxi.knowledge.candidate_service import CandidateService, CandidateServiceError
from fxi.knowledge.contracts import CandidateEnvelope, ErrorCode, EvidenceRef, Scope


def _hash(value: object) -> str:
    return sha256(str(value).encode()).hexdigest()


def _scope() -> Scope:
    return Scope(work_id="work-a", branch_id="branch-a", as_of=1, purpose="test", actor="actor-a")


def _candidate(candidate_id: str = "candidate-a", *, status: str = "CANDIDATE") -> CandidateEnvelope:
    scope = _scope()
    evidence = EvidenceRef(
        evidence_id="evidence-a",
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
        status=status,
        work_id="work-a",
        branch_id="branch-a",
        source_snapshot_ref="snapshot-a",
        evidence_refs=(evidence,),
        input_hash=_hash("input-a"),
        extractor_id="extractor-a",
        schema_version="schema-a",
        domain_package_version="domain-a",
        policy_hash=_hash("policy-a"),
        payload={"opaque": "value"},
    )


def test_candidate_submit_is_idempotent_and_read_isolated() -> None:
    service = CandidateService()
    envelope = _candidate()

    first = service.submit(envelope)
    replay = service.submit(envelope.model_copy(deep=True))

    assert first == replay
    fetched = service.get(envelope.candidate_id)
    assert fetched.status == "CANDIDATE"
    assert fetched.payload == {"opaque": "value"}
    fetched.payload["opaque"] = "changed"  # type: ignore[index]
    assert service.get(envelope.candidate_id).payload["opaque"] == "value"


def test_candidate_rejects_status_injection_and_conflicting_replay() -> None:
    service = CandidateService()
    service.submit(_candidate())

    with pytest.raises(CandidateServiceError) as status_error:
        CandidateService().submit(_candidate(status="APPROVED"))
    assert status_error.value.code == ErrorCode.INVALID_SCHEMA.value

    with pytest.raises(CandidateServiceError) as conflict_error:
        service.submit(_candidate().model_copy(update={"payload": {"opaque": "other"}}))
    assert conflict_error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT.value


def test_candidate_rejects_missing_or_out_of_scope_evidence() -> None:
    envelope = _candidate()
    with pytest.raises(CandidateServiceError) as missing_error:
        CandidateService().submit(envelope.model_copy(update={"evidence_refs": ()}))
    assert missing_error.value.code == ErrorCode.NO_EVIDENCE.value

    foreign = envelope.evidence_refs[0].model_copy(
        update={
            "scope": Scope(
                work_id="work-b", branch_id="branch-b", as_of=1, purpose="test", actor="actor-b"
            )
        }
    )
    with pytest.raises(CandidateServiceError) as scope_error:
        CandidateService().submit(envelope.model_copy(update={"evidence_refs": (foreign,)}))
    assert scope_error.value.code == ErrorCode.INVALID_SCOPE.value
