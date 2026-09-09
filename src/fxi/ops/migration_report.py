"""Small, hash-bound reports shared by recovery, rebuild and retirement gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import CONTRACT_REVISION, SCHEMA_HASH, SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """Structured outcome for an operational migration or rebuild step."""

    operation: str = "unknown"
    status: str = "PASSED"
    schema_version: str = SCHEMA_VERSION
    contract_revision: str = CONTRACT_REVISION
    schema_hash: str = SCHEMA_HASH
    applied: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    source_root_hash: str | None = None
    target_root_hash: str | None = None
    report_hash: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"PASSED", "INCOMPLETE", "BLOCKED", "FAILED"}:
            raise ValueError("invalid migration report status")
        if not self.report_hash:
            payload = {
                "operation": self.operation,
                "status": self.status,
                "schema_version": self.schema_version,
                "contract_revision": self.contract_revision,
                "schema_hash": self.schema_hash,
                "applied": self.applied,
                "blockers": self.blockers,
                "warnings": self.warnings,
                "errors": self.errors,
                "source_root_hash": self.source_root_hash,
                "target_root_hash": self.target_root_hash,
            }
            object.__setattr__(self, "report_hash", sha256_hex(payload))

    @property
    def success(self) -> bool:
        return self.status == "PASSED" and not self.blockers and not self.errors

    @property
    def blocked(self) -> bool:
        return self.status == "BLOCKED" or bool(self.blockers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "status": self.status,
            "schema_version": self.schema_version,
            "contract_revision": self.contract_revision,
            "schema_hash": self.schema_hash,
            "applied": list(self.applied),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "source_root_hash": self.source_root_hash,
            "target_root_hash": self.target_root_hash,
            "report_hash": self.report_hash,
        }


__all__ = ["MigrationReport"]
