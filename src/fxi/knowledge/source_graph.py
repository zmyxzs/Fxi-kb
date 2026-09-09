"""Domain-neutral composition of published source snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import Field, field_validator, model_validator

from fxi.core.exceptions import NotFoundError, ValidationError

from .as_of import AsOf, AsOfError, canonical_as_of, coerce_as_of, require_validity
from .contracts import (
    ContractModel,
    SourceBindingRef,
    SourceSnapshotRef,
    Validity,
    _hash,
    _stable_hash,
    _token,
)


class SourceGraphError(ValidationError):
    """A fail-closed source graph selection error."""

    def __init__(self, message: str, *, code: str = "INVALID_SCOPE") -> None:
        super().__init__(message)
        self.code = code


class SourceContributionRef(ContractModel):
    """The auditable contribution of one binding to a composite snapshot."""

    binding_id: str
    snapshot_id: str
    source_id: str
    source_version: str
    role: str
    priority: int = Field(ge=0)
    license: str
    branch_id: str
    content_hash: str
    validity: Validity
    contribution_hash: str = ""

    @field_validator("binding_id", "snapshot_id", "source_id", "source_version", "branch_id")
    @classmethod
    def validate_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("role", "license")
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return value

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _hash(value, "content_hash")

    @model_validator(mode="before")
    @classmethod
    def calculate_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in (
            "binding_id", "snapshot_id", "source_id", "source_version", "role",
            "priority", "license", "branch_id", "content_hash", "validity",
        )}
        expected = _stable_hash(payload)
        supplied = result.get("contribution_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("contribution_hash does not match canonical contribution")
        result["contribution_hash"] = expected
        return result


class SourceCompositeRef(ContractModel):
    """Immutable, deterministic metadata for a cross-source snapshot."""

    composite_id: str
    work_id: str
    branch_id: str
    as_of: int | str
    binding_ids: tuple[str, ...]
    source_snapshot_refs: tuple[str, ...]
    source_versions: tuple[str, ...]
    priorities: tuple[int, ...]
    licenses: tuple[str, ...]
    contributions: tuple[SourceContributionRef, ...]
    policy: Mapping[str, Any] = Field(default_factory=dict)
    policy_hash: str = ""
    composite_hash: str = ""

    @field_validator("composite_id", "work_id", "branch_id")
    @classmethod
    def validate_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("binding_ids", "source_snapshot_refs", "source_versions")
    @classmethod
    def validate_ref_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "composite_ref") for item in value)

    @field_validator("licenses")
    @classmethod
    def validate_licenses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item if isinstance(item, str) and item.strip() else _token(item, "license") for item in value)

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: int | str) -> int | str:
        return canonical_as_of(value)

    @field_validator("policy_hash", "composite_hash")
    @classmethod
    def validate_hashes(cls, value: str, info: Any) -> str:
        return _hash(value, info.field_name)

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
            raise ValueError("policy_hash does not match canonical source policy")
        result["policy_hash"] = expected_policy
        payload = {key: result.get(key) for key in (
            "composite_id", "work_id", "branch_id", "as_of", "binding_ids",
            "source_snapshot_refs", "source_versions", "priorities", "licenses",
            "contributions", "policy", "policy_hash",
        )}
        expected_composite = _stable_hash(payload)
        supplied_composite = result.get("composite_hash")
        if supplied_composite not in (None, "") and supplied_composite != expected_composite:
            raise ValueError("composite_hash does not match canonical composite")
        result["composite_hash"] = expected_composite
        return result

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return self.source_snapshot_refs


class SourceGraph:
    """Compose only published references; source text is never read here."""

    def __init__(
        self,
        snapshots: Mapping[str, SourceSnapshotRef] | None = None,
        *,
        snapshot_resolver: Callable[[str], SourceSnapshotRef] | Any | None = None,
        policy: Mapping[str, Any] | None = None,
    ) -> None:
        if snapshots is not None and snapshot_resolver is not None:
            raise SourceGraphError("只能配置一个 snapshot resolver")
        self._snapshots = snapshots
        self._resolver = snapshot_resolver
        self._policy = dict(policy or {})

    def _snapshot(self, snapshot_id: str) -> SourceSnapshotRef:
        try:
            if self._resolver is not None:
                value = self._resolver(snapshot_id) if callable(self._resolver) else self._resolver.get(snapshot_id)
            elif self._snapshots is not None:
                value = self._snapshots.get(snapshot_id)
            else:
                value = None
        except (KeyError, NotFoundError) as exc:
            raise SourceGraphError(f"snapshot not found: {snapshot_id}", code="NOT_FOUND") from exc
        if not isinstance(value, SourceSnapshotRef):
            raise SourceGraphError(f"snapshot not found: {snapshot_id}", code="NOT_FOUND")
        return value

    def compose_snapshot(
        self,
        work_id: str,
        bindings: Sequence[SourceBindingRef],
        *,
        as_of: AsOf | int | str,
    ) -> SourceCompositeRef:
        try:
            work_id = _token(work_id, "work_id")
        except ValueError as exc:
            raise SourceGraphError("invalid work_id") from exc
        selected_as_of = coerce_as_of(as_of)
        if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)) or not bindings:
            raise SourceGraphError("at least one source binding is required")

        seen: set[str] = set()
        contributions: list[SourceContributionRef] = []
        for binding in bindings:
            if not isinstance(binding, SourceBindingRef):
                raise SourceGraphError("bindings must be SourceBindingRef instances")
            if binding.binding_id in seen:
                raise SourceGraphError("duplicate source binding", code="INVALID_SCOPE")
            seen.add(binding.binding_id)
            if binding.work_id != work_id:
                raise SourceGraphError("binding work_id does not match composition", code="INVALID_SCOPE")
            try:
                require_validity(binding.validity, selected_as_of)
            except AsOfError as exc:
                raise SourceGraphError(str(exc), code=exc.code) from exc
            snapshot = self._snapshot_for_binding(binding)
            contributions.append(SourceContributionRef(
                binding_id=binding.binding_id,
                snapshot_id=snapshot.snapshot_id,
                source_id=binding.source_id,
                source_version=snapshot.source_version,
                role=binding.role,
                priority=binding.priority,
                license=binding.license,
                branch_id=binding.branch_id,
                content_hash=snapshot.content_hash,
                validity=binding.validity,
            ))

        branches = {item.branch_id for item in contributions}
        if len(branches) != 1:
            raise SourceGraphError("all source bindings must target one branch", code="INVALID_SCOPE")
        contributions.sort(key=lambda item: (-item.priority, item.binding_id, item.source_id, item.source_version))
        policy = {
            **self._policy,
            "priority_is_policy_input_only": True,
            "ordering": [
                {"binding_id": item.binding_id, "priority": item.priority}
                for item in contributions
            ],
            "source_roles": {item.binding_id: item.role for item in contributions},
            "source_versions": {item.binding_id: item.source_version for item in contributions},
            "licenses": {item.binding_id: item.license for item in contributions},
        }
        payload = {
            "work_id": work_id,
            "branch_id": next(iter(branches)),
            "as_of": selected_as_of.value,
            "binding_ids": [item.binding_id for item in contributions],
            "source_snapshot_refs": [item.snapshot_id for item in contributions],
            "source_versions": [item.source_version for item in contributions],
            "priorities": [item.priority for item in contributions],
            "licenses": [item.license for item in contributions],
            "contributions": contributions,
            "policy": policy,
        }
        composite_id = "composite-" + _stable_hash(payload)
        return SourceCompositeRef(composite_id=composite_id, **payload)

    def _snapshot_for_binding(self, binding: SourceBindingRef) -> SourceSnapshotRef:
        snapshot = None
        if self._resolver is not None and hasattr(self._resolver, "active"):
            snapshot = self._resolver.active(binding.work_id, binding.source_id, binding.branch_id)
        if snapshot is None:
            snapshot = self._snapshot(binding.binding_id)
        if snapshot.work_id != binding.work_id or snapshot.source_id != binding.source_id:
            raise SourceGraphError("snapshot and binding scope do not match", code="INVALID_SCOPE")
        if snapshot.binding_id != binding.binding_id:
            raise SourceGraphError("snapshot binding_id does not match", code="INVALID_SCOPE")
        if snapshot.status.upper() != "PUBLISHED":
            raise SourceGraphError("snapshot is not published", code="STALE_VERSION")
        return snapshot


__all__ = ["SourceContributionRef", "SourceCompositeRef", "SourceGraph", "SourceGraphError"]
