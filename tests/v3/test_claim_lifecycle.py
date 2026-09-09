"""Synthetic claim lifecycle, scope, evidence and perspective tests."""

from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from fxi.domain_packages.novel import (
    ClaimLifecycle,
    ClaimPerspective,
    ClaimStatus,
    ConflictCode,
    NovelClaim,
    classify_claim_conflict,
    diagnose_temporal_order,
    extract_claim,
    extract_claim_view,
)
from fxi.knowledge.contracts import EvidenceRef, Scope


def _scope(work_id: str = "work-synthetic") -> Scope:
    return Scope(work_id=work_id, branch_id="branch-main", as_of=4, purpose="test", actor="actor-test")


def _evidence(scope: Scope, evidence_id: str = "evidence-1") -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        source_snapshot_ref="snapshot-synthetic",
        source_id="source-synthetic",
        source_version="version-1",
        document_id="document-synthetic",
        start=0,
        end=8,
        excerpt_hash=sha256(b"synthetic").hexdigest(),
        normalization_version="normalization-1",
        scope=scope,
    )


def _claim(*, claim_id: str = "claim-1", value: str = "one", perspective: str = "CANONICAL", perspective_ref: str | None = None, supersedes_claim_ref: str | None = None) -> NovelClaim:
    scope = _scope()
    return NovelClaim(
        claim_id=claim_id,
        subject="subject-1",
        predicate="has_state",
        value=value,
        type="novel.character@1",
        scope=scope,
        evidence=( _evidence(scope, f"evidence-{claim_id}"), ),
        perspective=perspective,
        perspective_ref=perspective_ref,
        supersedes_claim_ref=supersedes_claim_ref,
        version="v1",
    )


def test_claim_lifecycle_accepts_supported_transitions_and_rejects_terminal_reversal() -> None:
    claim = _claim()
    disputed = ClaimLifecycle.transition(claim, ClaimStatus.DISPUTED.value, version="v2")
    invalidated = ClaimLifecycle.transition(disputed, ClaimStatus.INVALIDATED.value, version="v3")
    assert disputed.status == "DISPUTED"
    assert invalidated.status == "INVALIDATED"
    assert invalidated.version == "v3"
    with pytest.raises(ValueError):
        ClaimLifecycle.transition(invalidated, ClaimStatus.ASSERTED.value)
    with pytest.raises(ValueError):
        ClaimLifecycle.transition(claim, "NOT_A_LIFECYCLE_STATUS")


def test_claim_requires_scope_and_evidence_and_preserves_kernel_boundary() -> None:
    scope = _scope()
    with pytest.raises(ValidationError):
        NovelClaim(
            claim_id="claim-no-evidence",
            subject="subject-1",
            predicate="has_state",
            value="one",
            type="novel.character@1",
            scope=scope,
            evidence=(),
        )
    with pytest.raises(ValidationError):
        NovelClaim.model_validate({**_claim().model_dump(), "evidence_refs": (_evidence(_scope("other-work")),)})
    kernel_claim = extract_claim(_claim())
    assert kernel_claim.subject_ref == "subject-1"
    assert kernel_claim.object_type_uri == "novel.character@1"
    assert extract_claim_view(kernel_claim).claim_id == kernel_claim.claim_id


def test_retcon_misunderstanding_and_character_knowledge_are_distinct_diagnostics() -> None:
    canonical = _claim(value="one")
    retcon = _claim(claim_id="claim-retcon", value="two", perspective="RETCON", supersedes_claim_ref=canonical.claim_id)
    misunderstanding = _claim(claim_id="claim-misunderstood", value="three", perspective="MISUNDERSTANDING", perspective_ref="character-1")
    knowledge = _claim(claim_id="claim-knowledge", value="four", perspective="CHARACTER_KNOWLEDGE", perspective_ref="character-2")
    assert classify_claim_conflict(canonical, retcon).code is ConflictCode.RETCON
    assert classify_claim_conflict(canonical, misunderstanding).code is ConflictCode.MISUNDERSTANDING
    assert classify_claim_conflict(canonical, knowledge).code is ConflictCode.CHARACTER_KNOWLEDGE
    assert classify_claim_conflict(canonical, retcon).auto_applied is False


def test_scope_isolation_and_temporal_order_do_not_create_causality() -> None:
    other_work = NovelClaim.model_validate({**_claim(claim_id="claim-other-work", value="two").model_dump(), "scope": _scope("other-work"), "evidence_refs": (_evidence(_scope("other-work"), "evidence-other"),), "claim_hash": ""})
    assert classify_claim_conflict(_claim(), other_work).code is ConflictCode.NONE
    diagnostic = diagnose_temporal_order(earlier_ref="event-1", later_ref="event-2")
    assert diagnostic.code is ConflictCode.TEMPORAL_ORDER_NOT_CAUSAL
