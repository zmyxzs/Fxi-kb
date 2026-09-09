"""Source-to-work bindings for the v3 public contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import MutableMapping

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.core.identifiers import validate_segment
from fxi.knowledge.contracts import SourceBindingRef, Validity


class SourceBindingConflictError(ValidationError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "CAS_CONFLICT"


@dataclass(frozen=True, slots=True)
class SourceBinding:
    work_id: str
    source_id: str
    role: str
    branch_id: str
    validity: Validity
    license: str
    access: str
    priority: int = 0
    allowed_purposes: tuple[str, ...] = ()
    sync_direction: str = "pull"
    binding_id: str | None = None

    def to_ref(self) -> SourceBindingRef:
        binding_id = self.binding_id
        if binding_id is None:
            binding_id = "binding-" + sha256_hex(
                {
                    "work_id": self.work_id,
                    "source_id": self.source_id,
                    "role": self.role,
                    "priority": self.priority,
                    "branch_id": self.branch_id,
                    "validity": self.validity.model_dump(mode="json"),
                    "license": self.license,
                    "access": self.access,
                    "allowed_purposes": self.allowed_purposes,
                    "sync_direction": self.sync_direction,
                }
            )
        return SourceBindingRef(
            binding_id=binding_id,
            work_id=self.work_id,
            source_id=self.source_id,
            role=self.role,
            priority=self.priority,
            branch_id=self.branch_id,
            validity=self.validity,
            license=self.license,
            access=self.access,
            allowed_purposes=self.allowed_purposes,
            sync_direction=self.sync_direction,
        )


def _validate_binding(ref: SourceBindingRef) -> SourceBindingRef:
    if not isinstance(ref, SourceBindingRef):
        raise ValidationError("binding 必须是 SourceBinding 或 SourceBindingRef")
    # F1 validates identifiers and strings; these explicit checks keep the
    # service safe when called with objects constructed by older callers.
    for value, label in (
        (ref.binding_id, "binding_id"),
        (ref.work_id, "work_id"),
        (ref.source_id, "source_id"),
        (ref.branch_id, "branch_id"),
    ):
        validate_segment(value, label)
    if ref.priority < 0:
        raise ValidationError("priority 不能为负数")
    return ref


class SourceBindingService:
    """Explicitly stored bindings; no hidden database or domain registry."""

    def __init__(self, store: MutableMapping[str, SourceBindingRef] | None = None) -> None:
        self._store = store if store is not None else {}

    def bind(self, binding: SourceBinding | SourceBindingRef) -> SourceBindingRef:
        ref = _validate_binding(binding.to_ref() if isinstance(binding, SourceBinding) else binding)
        existing = self._store.get(ref.binding_id)
        if existing is not None and existing != ref:
            raise SourceBindingConflictError(
                f"binding_id 已绑定不同来源: {ref.binding_id}"
            )
        self._store[ref.binding_id] = ref
        return ref

    def list(self, work_id: str) -> tuple[SourceBindingRef, ...]:
        validate_segment(work_id, "work_id")
        return tuple(
            sorted(
                (ref for ref in self._store.values() if ref.work_id == work_id),
                key=lambda ref: (-ref.priority, ref.binding_id),
            )
        )

    def get(self, binding_id: str) -> SourceBindingRef:
        validate_segment(binding_id, "binding_id")
        try:
            return self._store[binding_id]
        except KeyError as exc:
            raise ValidationError(f"未找到 source binding: {binding_id}") from exc


__all__ = ["SourceBinding", "SourceBindingConflictError", "SourceBindingService"]
