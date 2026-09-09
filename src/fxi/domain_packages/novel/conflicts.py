"""Conflict classification and claim lifecycle rules for the novel package."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import Field, field_validator

from fxi.knowledge.contracts import ContractModel, _non_empty, _token

from .schemas import ClaimPerspective, ClaimStatus, NovelClaim


class ConflictCode(str, Enum):
    NONE = "NONE"
    CONTRADICTION = "CONTRADICTION"
    RETCON = "RETCON"
    MISUNDERSTANDING = "MISUNDERSTANDING"
    CHARACTER_KNOWLEDGE = "CHARACTER_KNOWLEDGE"
    TEMPORAL_ORDER_NOT_CAUSAL = "TEMPORAL_ORDER_NOT_CAUSAL"
    ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"


class ConflictDiagnostic(ContractModel):
    code: ConflictCode
    claim_refs: tuple[str, ...] = ()
    message: str
    suggested_status: str | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("claim_refs")
    @classmethod
    def validate_claim_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "claim_ref") for item in value)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _non_empty(value, "message")

    @field_validator("suggested_status")
    @classmethod
    def validate_suggested_status(cls, value: str | None) -> str | None:
        return None if value is None else _non_empty(value, "suggested_status")


class ConflictResult(ContractModel):
    conflict: bool
    code: ConflictCode
    diagnostics: tuple[ConflictDiagnostic, ...] = ()
    auto_applied: bool = False


_TRANSITIONS: Mapping[str, frozenset[str]] = {
    ClaimStatus.ASSERTED.value: frozenset({ClaimStatus.ASSERTED.value, ClaimStatus.SUPERSEDED.value, ClaimStatus.DISPUTED.value, ClaimStatus.INVALIDATED.value, ClaimStatus.UNKNOWN.value}),
    ClaimStatus.DISPUTED.value: frozenset({ClaimStatus.DISPUTED.value, ClaimStatus.ASSERTED.value, ClaimStatus.SUPERSEDED.value, ClaimStatus.INVALIDATED.value, ClaimStatus.UNKNOWN.value}),
    ClaimStatus.UNKNOWN.value: frozenset({ClaimStatus.UNKNOWN.value, ClaimStatus.ASSERTED.value, ClaimStatus.SUPERSEDED.value, ClaimStatus.DISPUTED.value, ClaimStatus.INVALIDATED.value}),
    ClaimStatus.SUPERSEDED.value: frozenset({ClaimStatus.SUPERSEDED.value}),
    ClaimStatus.INVALIDATED.value: frozenset({ClaimStatus.INVALIDATED.value}),
}


def validate_claim_transition(current_status: str, next_status: str) -> None:
    """Fail closed for lifecycle changes; extension statuses are idempotent only."""

    _non_empty(current_status, "current_status")
    _non_empty(next_status, "next_status")
    allowed = _TRANSITIONS.get(current_status, frozenset({current_status}))
    if next_status not in allowed:
        raise ValueError(f"illegal claim status transition: {current_status} -> {next_status}")


class ClaimLifecycle:
    """Pure claim transition service; it never overwrites another claim."""

    @staticmethod
    def transition(claim: NovelClaim, next_status: str, *, version: str | None = None) -> NovelClaim:
        if not isinstance(claim, NovelClaim):
            raise TypeError("claim must be a validated NovelClaim")
        validate_claim_transition(claim.status, next_status)
        next_version = claim.version if version is None else _token(version, "version")
        values = claim.model_dump()
        values.update(status=next_status, version=next_version, claim_hash="")
        return NovelClaim.model_validate(values)


def classify_claim_conflict(left: NovelClaim, right: NovelClaim) -> ConflictResult:
    """Classify same-scope value disagreement without selecting a winning fact."""

    if not isinstance(left, NovelClaim) or not isinstance(right, NovelClaim):
        raise TypeError("claim conflict classification requires validated NovelClaim instances")
    if left.scope.work_id != right.scope.work_id or left.scope.branch_id != right.scope.branch_id:
        return ConflictResult(conflict=False, code=ConflictCode.NONE)
    if (left.subject_ref, left.predicate) != (right.subject_ref, right.predicate) or left.value == right.value:
        return ConflictResult(conflict=False, code=ConflictCode.NONE)
    perspective = next(
        (item for item in (left.perspective, right.perspective) if item is not ClaimPerspective.CANONICAL),
        ClaimPerspective.CANONICAL,
    )
    code = {
        ClaimPerspective.RETCON: ConflictCode.RETCON,
        ClaimPerspective.MISUNDERSTANDING: ConflictCode.MISUNDERSTANDING,
        ClaimPerspective.CHARACTER_KNOWLEDGE: ConflictCode.CHARACTER_KNOWLEDGE,
    }.get(perspective, ConflictCode.CONTRADICTION)
    diagnostic = ConflictDiagnostic(
        code=code,
        claim_refs=(left.claim_id, right.claim_id),
        message="claims disagree within one work and branch; adjudication is required",
        suggested_status=ClaimStatus.DISPUTED.value if code is ConflictCode.CONTRADICTION else None,
        metadata={"same_scope": True, "auto_resolution": False},
    )
    return ConflictResult(conflict=True, code=code, diagnostics=(diagnostic,))


def diagnose_temporal_order(*, earlier_ref: str, later_ref: str) -> ConflictDiagnostic:
    """Return an explicit non-causal diagnostic for ordering-only evidence."""

    return ConflictDiagnostic(
        code=ConflictCode.TEMPORAL_ORDER_NOT_CAUSAL,
        claim_refs=(_token(earlier_ref, "earlier_ref"), _token(later_ref, "later_ref")),
        message="temporal order alone does not establish a causal link",
        metadata={"requires_causal_evidence": True},
    )


class NovelConflictPolicy:
    def __init__(self, *, type_uri: str):
        self.type_uri = _non_empty(type_uri, "type_uri")

    def evaluate(self, left: NovelClaim, right: NovelClaim) -> ConflictResult:
        return classify_claim_conflict(left, right)


__all__ = [
    "ClaimLifecycle",
    "ConflictCode",
    "ConflictDiagnostic",
    "ConflictResult",
    "NovelConflictPolicy",
    "classify_claim_conflict",
    "diagnose_temporal_order",
    "validate_claim_transition",
]
