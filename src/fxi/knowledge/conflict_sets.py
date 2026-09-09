"""Generic claim conflict detection and approval-gated resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import Field, field_validator, model_validator

from fxi.core.exceptions import ValidationError

from .as_of import AsOfError, compare_as_of
from .contracts import (
    Actor,
    ApprovalRef,
    ContractModel,
    EvidenceRef,
    Scope,
    _hash,
    _stable_hash,
    _token,
)
from .objects import Claim


class ConflictError(ValidationError):
    def __init__(self, message: str, *, code: str = "CONFLICTING_ASSERTIONS") -> None:
        super().__init__(message)
        self.code = code


class ConflictCandidate(ContractModel):
    candidate_id: str
    claim_refs: tuple[str, ...]
    description: str
    evidence_refs: tuple[EvidenceRef, ...] = ()
    candidate_hash: str = ""

    @field_validator("candidate_id", "claim_refs")
    @classmethod
    def validate_refs(cls, value: Any, info: Any) -> Any:
        if info.field_name == "claim_refs":
            return tuple(_token(item, "claim_ref") for item in value)
        return _token(value, info.field_name)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("description must be non-empty")
        return value

    @model_validator(mode="before")
    @classmethod
    def calculate_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("candidate_id", "claim_refs", "description", "evidence_refs")}
        expected = _stable_hash(payload)
        supplied = result.get("candidate_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("candidate_hash does not match canonical candidate")
        result["candidate_hash"] = expected
        return result


class ConflictSet(ContractModel):
    conflict_id: str
    claim_refs: tuple[str, ...]
    conflict_type: str
    candidate_solutions: tuple[ConflictCandidate, ...]
    evidence_refs: tuple[EvidenceRef, ...] = ()
    scope: Scope
    policy: Mapping[str, Any] = Field(default_factory=dict)
    policy_hash: str = ""
    status: str = "UNRESOLVED"
    actor: Actor | None = None
    decision_hash: str | None = None
    conflict_hash: str = ""

    @field_validator("conflict_id")
    @classmethod
    def validate_conflict_id(cls, value: str) -> str:
        return _token(value, "conflict_id")

    @field_validator("claim_refs")
    @classmethod
    def validate_claim_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "claim_ref") for item in value)

    @field_validator("conflict_type", "status")
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return value

    @field_validator("decision_hash")
    @classmethod
    def validate_decision_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value, "decision_hash")

    @model_validator(mode="before")
    @classmethod
    def calculate_hashes(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        policy = result.get("policy", {})
        expected_policy = _stable_hash(policy)
        supplied_policy = result.get("policy_hash")
        if supplied_policy not in (None, "") and supplied_policy != expected_policy:
            raise ValueError("policy_hash does not match canonical policy")
        result["policy_hash"] = expected_policy
        payload = {key: result.get(key) for key in (
            "conflict_id", "claim_refs", "conflict_type", "candidate_solutions",
            "evidence_refs", "scope", "policy", "policy_hash", "status", "actor",
            "decision_hash",
        )}
        expected_conflict = _stable_hash(payload)
        supplied_conflict = result.get("conflict_hash")
        if supplied_conflict not in (None, "") and supplied_conflict != expected_conflict:
            raise ValueError("conflict_hash does not match canonical conflict")
        result["conflict_hash"] = expected_conflict
        return result


class ConflictDecision(ContractModel):
    decision_id: str
    conflict_id: str
    selected_candidate_id: str | None = None
    selected_claim_refs: tuple[str, ...] = ()
    resolution: str | None = None
    outcome: str = "ACCEPT"
    rationale: str
    scope: Scope
    actor: Actor
    idempotency_key: str
    policy_hash: str | None = None
    decision_hash: str = ""

    @field_validator("decision_id", "conflict_id", "idempotency_key")
    @classmethod
    def validate_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("selected_candidate_id")
    @classmethod
    def validate_selected_candidate(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "selected_candidate_id")

    @field_validator("selected_claim_refs")
    @classmethod
    def validate_selected_claims(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "selected_claim_ref") for item in value)

    @field_validator("resolution", "outcome", "rationale")
    @classmethod
    def validate_decision_text(cls, value: str | None, info: Any) -> str | None:
        if value is None and info.field_name == "resolution":
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return value

    @field_validator("policy_hash")
    @classmethod
    def validate_policy_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value, "policy_hash")

    @model_validator(mode="before")
    @classmethod
    def calculate_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in (
            "decision_id", "conflict_id", "selected_candidate_id", "selected_claim_refs",
            "resolution", "outcome", "rationale", "scope", "actor", "idempotency_key",
            "policy_hash",
        )}
        expected = _stable_hash(payload)
        supplied = result.get("decision_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("decision_hash does not match canonical decision")
        result["decision_hash"] = expected
        return result


class ResolutionRef(ContractModel):
    resolution_id: str
    conflict_id: str
    decision_id: str
    selected_candidate_id: str
    selected_claim_refs: tuple[str, ...]
    approval_id: str
    actor: Actor
    decision_hash: str
    status: str = "APPROVED"
    idempotent_replay: bool = False
    resolution_hash: str = ""

    @field_validator("resolution_id", "conflict_id", "decision_id", "selected_candidate_id", "approval_id")
    @classmethod
    def validate_resolution_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("selected_claim_refs")
    @classmethod
    def validate_resolution_claims(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "selected_claim_ref") for item in value)

    @field_validator("decision_hash")
    @classmethod
    def validate_resolution_hash(cls, value: str) -> str:
        return _hash(value, "decision_hash")

    @model_validator(mode="before")
    @classmethod
    def calculate_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in (
            "resolution_id", "conflict_id", "decision_id", "selected_candidate_id",
            "selected_claim_refs", "approval_id", "actor", "decision_hash", "status",
            "idempotent_replay",
        )}
        expected = _stable_hash(payload)
        supplied = result.get("resolution_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("resolution_hash does not match canonical resolution")
        result["resolution_hash"] = expected
        return result


class ConflictService:
    def __init__(
        self,
        *,
        policy: Mapping[str, Any] | None = None,
        conflicts: dict[str, ConflictSet] | None = None,
        resolutions: dict[str, ResolutionRef] | None = None,
    ) -> None:
        self._policy = dict(policy or {})
        self._conflicts = conflicts if conflicts is not None else {}
        self._resolutions = resolutions if resolutions is not None else {}
        self._consumed_approvals: set[str] = set()

    @staticmethod
    def _claim_key(claim: Claim) -> tuple[Any, ...]:
        return (
            claim.scope.work_id,
            claim.scope.branch_id,
            claim.scope.as_of,
            claim.subject_ref,
            claim.predicate,
            claim.object_type_uri,
        )

    def detect(self, claims: Sequence[Claim]) -> tuple[ConflictSet, ...]:
        if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
            raise ConflictError("claims must be a sequence")
        grouped: dict[tuple[Any, ...], list[Claim]] = {}
        for claim in claims:
            if not isinstance(claim, Claim):
                raise ConflictError("claims must contain Claim instances")
            if claim.status.upper() in {"RETRACTED", "REJECTED", "RESOLVED"}:
                continue
            grouped.setdefault(self._claim_key(claim), []).append(claim)

        result: list[ConflictSet] = []
        for key, members in sorted(grouped.items(), key=lambda item: _stable_hash(item[0])):
            by_value: dict[str, list[Claim]] = {}
            for claim in members:
                by_value.setdefault(_stable_hash(claim.value), []).append(claim)
            if len(by_value) < 2:
                continue
            refs = tuple(sorted(claim.claim_id for claim in members))
            conflict_id = "conflict-" + _stable_hash({"key": key, "claim_refs": refs})
            evidence = tuple(sorted(
                {ref.evidence_id or ref.source_snapshot_ref or ref.source_id: ref for claim in members for ref in claim.evidence_refs}.values(),
                key=lambda ref: (ref.evidence_id or "", ref.source_id, ref.document_id, ref.start),
            ))
            candidates = tuple(
                ConflictCandidate(
                    candidate_id="candidate-" + _stable_hash({"claim_refs": [claim.claim_id for claim in value_members]})[:32],
                    claim_refs=tuple(claim.claim_id for claim in value_members),
                    description="retain assertion candidate",
                    evidence_refs=tuple(value_members[0].evidence_refs),
                )
                for _, value_members in sorted(by_value.items())
            )
            policy = {
                **self._policy,
                "priority_is_policy_input_only": True,
                "claim_order": refs,
            }
            conflict = ConflictSet(
                conflict_id=conflict_id,
                claim_refs=refs,
                conflict_type="ASSERTION_VALUE_MISMATCH",
                candidate_solutions=candidates,
                evidence_refs=evidence,
                scope=members[0].scope,
                policy=policy,
            )
            prior = self._conflicts.get(conflict_id)
            if prior is not None:
                conflict = prior
            else:
                self._conflicts[conflict_id] = conflict
            result.append(_copy_conflict(conflict))
        return tuple(result)

    def get(self, conflict_id: str) -> ConflictSet:
        try:
            return _copy_conflict(self._conflicts[_token(conflict_id, "conflict_id")])
        except KeyError as exc:
            raise ConflictError("conflict not found", code="NOT_FOUND") from exc

    def resolve(self, conflict_id: str, decision: ConflictDecision, approval: ApprovalRef) -> ResolutionRef:
        try:
            conflict = self._conflicts[_token(conflict_id, "conflict_id")]
        except KeyError as exc:
            raise ConflictError("conflict not found", code="NOT_FOUND") from exc
        if not isinstance(decision, ConflictDecision) or not isinstance(approval, ApprovalRef):
            raise ConflictError("decision and approval must use public contracts", code="APPROVAL_REQUIRED")
        if decision.conflict_id != conflict.conflict_id:
            raise ConflictError("decision conflict_id does not match", code="INVALID_SCOPE")
        if decision.actor.actor_id != decision.scope.actor:
            raise ConflictError("decision actor does not match scope", code="INVALID_SCOPE")
        if decision.actor.scope is None or approval.actor.scope is None:
            raise ConflictError("approval and decision require explicit actor scope", code="APPROVAL_REQUIRED")
        if approval.actor.actor_id != decision.actor.actor_id:
            raise ConflictError("approval actor does not match decision actor", code="AUTHORIZATION_FAILED")
        for scope in (decision.scope, decision.actor.scope, approval.actor.scope):
            if scope.work_id != conflict.scope.work_id or scope.branch_id != conflict.scope.branch_id:
                raise ConflictError("resolution scope is outside conflict", code="INVALID_SCOPE")
            try:
                if compare_as_of(scope.as_of, conflict.scope.as_of) != 0:
                    raise ConflictError("resolution as_of does not match conflict", code="INVALID_SCOPE")
            except AsOfError as exc:
                raise ConflictError(str(exc), code="INVALID_SCOPE") from exc
        if conflict.status == "RESOLVED":
            prior = self._resolutions.get(conflict.conflict_id)
            if (
                prior is not None
                and prior.decision_hash == decision.decision_hash
                and prior.approval_id == approval.approval_id
            ):
                return ResolutionRef.model_validate({
                    **prior.model_dump(mode="python"),
                    "idempotent_replay": True,
                    "resolution_hash": "",
                })
            raise ConflictError("conflict already resolved with another decision", code="IDEMPOTENCY_CONFLICT")
        if approval.consumed or approval.approval_id in self._consumed_approvals:
            raise ConflictError("approval has already been consumed", code="APPROVAL_REQUIRED")

        candidate = self._select_candidate(conflict, decision)
        resolution_id = "resolution-" + _stable_hash({
            "conflict_id": conflict.conflict_id,
            "decision_hash": decision.decision_hash,
            "approval_id": approval.approval_id,
        })
        resolution = ResolutionRef(
            resolution_id=resolution_id,
            conflict_id=conflict.conflict_id,
            decision_id=decision.decision_id,
            selected_candidate_id=candidate.candidate_id,
            selected_claim_refs=candidate.claim_refs,
            approval_id=approval.approval_id,
            actor=decision.actor,
            decision_hash=decision.decision_hash,
        )
        self._resolutions[conflict.conflict_id] = resolution
        self._consumed_approvals.add(approval.approval_id)
        self._conflicts[conflict.conflict_id] = ConflictSet.model_validate({
            **conflict.model_dump(mode="python"),
            "status": "RESOLVED",
            "actor": decision.actor,
            "decision_hash": decision.decision_hash,
            "conflict_hash": "",
        })
        return _copy_resolution(resolution)

    @staticmethod
    def _select_candidate(conflict: ConflictSet, decision: ConflictDecision) -> ConflictCandidate:
        if decision.selected_candidate_id is not None:
            for candidate in conflict.candidate_solutions:
                if candidate.candidate_id == decision.selected_candidate_id:
                    return candidate
        if decision.selected_claim_refs:
            selected = set(decision.selected_claim_refs)
            for candidate in conflict.candidate_solutions:
                if set(candidate.claim_refs) == selected:
                    return candidate
        if decision.resolution is not None:
            for candidate in conflict.candidate_solutions:
                if decision.resolution in {candidate.candidate_id, *candidate.claim_refs}:
                    return candidate
        raise ConflictError("decision must select an existing candidate", code="CONFLICTING_ASSERTIONS")


def _copy_conflict(value: ConflictSet) -> ConflictSet:
    return value.model_copy(deep=True)


def _copy_resolution(value: ResolutionRef) -> ResolutionRef:
    return value.model_copy(deep=True)


__all__ = [
    "ConflictCandidate",
    "ConflictDecision",
    "ConflictError",
    "ConflictService",
    "ConflictSet",
    "ResolutionRef",
]
