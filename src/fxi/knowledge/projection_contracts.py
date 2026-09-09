"""Domain-neutral contracts for rebuildable knowledge projections.

Projection records are derived artifacts.  These contracts deliberately keep
the authority-side models out of the lifecycle and carry enough provenance to
make a projection disposable, auditable, and independently rebuildable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Mapping, Protocol, TYPE_CHECKING

from pydantic import Field, field_validator

from .contracts import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    ContractModel,
    ErrorCode,
    _hash,
    _non_empty,
    _stable_hash,
    _token,
)
from .objects import KnowledgeVersion

if TYPE_CHECKING:
    from .contracts import ContextView


class ProjectionStatus(str, Enum):
    PENDING = "PENDING"
    BUILT = "BUILT"
    DROPPED = "DROPPED"
    STALE = "STALE"
    FAILED = "FAILED"


class ProjectionError(ValueError):
    """A provider error that is safe to expose in a projection manifest."""

    def __init__(self, message: str, code: str = ErrorCode.PROJECTION_FAILED.value) -> None:
        super().__init__(message)
        self.code = code


class CapabilityUnsupportedError(ProjectionError):
    def __init__(self, capability: str) -> None:
        super().__init__(
            f"projection capability '{capability}' is unsupported",
            ErrorCode.CAPABILITY_UNSUPPORTED.value,
        )
        self.capability = capability


class ProjectionManifest(ContractModel):
    """The immutable receipt for one projection attempt or artifact."""

    contract_revision: str = CONTRACT_REVISION
    schema_hash: str = SCHEMA_HASH
    projection_id: str
    projection_version: str
    work_id: str
    branch_id: str
    knowledge_version: str
    knowledge_version_hash: str
    source_refs: tuple[str, ...] = ()
    source_id: str | None = None
    source_version: str | None = None
    context_view_id: str | None = None
    context_view_hash: str | None = None
    scope_hash: str
    projection_hash: str
    status: str = ProjectionStatus.BUILT.value
    record_count: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0.0, ge=0.0)
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = Field(default=0, ge=0)
    dead_letter: bool = False
    readiness: str = "READY"
    records: tuple[Mapping[str, Any], ...] = ()

    @field_validator(
        "contract_revision",
        "projection_id",
        "projection_version",
        "work_id",
        "branch_id",
        "knowledge_version",
        "readiness",
    )
    @classmethod
    def validate_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("schema_hash", "knowledge_version_hash", "scope_hash", "projection_hash")
    @classmethod
    def validate_hashes(cls, value: str, info: Any) -> str:
        return _hash(value, info.field_name)

    @field_validator("source_id", "source_version", "context_view_id")
    @classmethod
    def validate_optional_tokens(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @field_validator("context_view_hash")
    @classmethod
    def validate_optional_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value, "context_view_hash")

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "source_ref") for item in value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        value = _non_empty(value, "status")
        if value not in {item.value for item in ProjectionStatus}:
            raise ValueError("status is not a valid projection status")
        return value

    @field_validator("error_code")
    @classmethod
    def validate_error_code(cls, value: str | None) -> str | None:
        return None if value is None else _non_empty(value, "error_code")

    @classmethod
    def failed(
        cls,
        snapshot: KnowledgeVersion | None,
        *,
        projection_id: str,
        projection_version: str,
        code: str,
        message: str,
        retry_count: int = 0,
        dead_letter: bool = False,
        duration_ms: float = 0.0,
        context_view: ContextView | None = None,
    ) -> "ProjectionManifest":
        work_id = snapshot.work_id if snapshot is not None else "unknown-work"
        branch_id = snapshot.branch_id if snapshot is not None else "unknown-branch"
        knowledge_version = snapshot.knowledge_version if snapshot is not None else "unknown-version"
        version_hash = snapshot.version_hash if snapshot is not None else _stable_hash({"knowledge_version": knowledge_version})
        source_refs = snapshot.source_snapshot_refs if snapshot is not None else ()
        scope_hash = _stable_hash({"work_id": work_id, "branch_id": branch_id})
        projection_hash = _stable_hash(
            {
                "projection_id": projection_id,
                "projection_version": projection_version,
                "knowledge_version": knowledge_version,
                "code": code,
                "message": message,
            }
        )
        return cls(
            projection_id=projection_id,
            projection_version=projection_version,
            work_id=work_id,
            branch_id=branch_id,
            knowledge_version=knowledge_version,
            knowledge_version_hash=version_hash,
            source_refs=source_refs,
            context_view_id=context_view.view_id if context_view is not None else None,
            context_view_hash=context_view.view_hash if context_view is not None else None,
            scope_hash=scope_hash,
            projection_hash=projection_hash,
            status=ProjectionStatus.FAILED.value,
            duration_ms=duration_ms,
            error_code=code,
            error_message=message,
            retry_count=retry_count,
            dead_letter=dead_letter,
            readiness="DEAD_LETTER" if dead_letter else "NOT_READY",
        )


class ProjectionHealth(ContractModel):
    projection_id: str
    projection_version: str
    status: str
    ready: bool = False
    stale: bool = False
    knowledge_version: str | None = None
    projection_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    dead_letter: bool = False
    readiness: str = "NOT_READY"
    record_count: int = Field(default=0, ge=0)
    duration_ms: float = Field(default=0.0, ge=0.0)

    @field_validator("projection_id", "projection_version", "status", "readiness")
    @classmethod
    def validate_health_strings(cls, value: str, info: Any) -> str:
        value = _non_empty(value, info.field_name)
        if info.field_name == "status" and value not in {item.value for item in ProjectionStatus}:
            raise ValueError("status is not a valid projection status")
        return value

    @field_validator("knowledge_version")
    @classmethod
    def validate_health_version(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "knowledge_version")

    @field_validator("projection_hash")
    @classmethod
    def validate_health_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value, "projection_hash")


class ProjectionProvider(Protocol):
    projection_id: str
    projection_version: str

    def build(self, snapshot: KnowledgeVersion) -> ProjectionManifest: ...

    def drop(self, manifest: ProjectionManifest) -> None: ...

    def health(self, manifest: ProjectionManifest | None) -> ProjectionHealth: ...


__all__ = [
    "CONTRACT_REVISION",
    "SCHEMA_HASH",
    "CapabilityUnsupportedError",
    "ProjectionError",
    "ProjectionHealth",
    "ProjectionManifest",
    "ProjectionProvider",
    "ProjectionStatus",
]
