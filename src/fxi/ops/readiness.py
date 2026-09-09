"""Fail-closed service and projection readiness checks."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fxi.knowledge.contracts import CONTRACT_REVISION, SCHEMA_HASH


class ReadinessService:
    """Compute liveness/readiness without treating projections as authority."""

    def __init__(
        self,
        *,
        registry: Any,
        operation_manifest: Any,
        projection_runner: Any = None,
        domain_registry: Any = None,
        contract_revision: str = CONTRACT_REVISION,
        schema_hash: str = SCHEMA_HASH,
        openapi_hash: str | None = None,
    ) -> None:
        self.registry = registry
        self.operation_manifest = operation_manifest
        self.projection_runner = projection_runner
        self.domain_registry = domain_registry
        self.contract_revision = contract_revision
        self.schema_hash = schema_hash
        self.openapi_hash = openapi_hash

    def check(
        self,
        *,
        work_id: str | None = None,
        branch_id: str | None = None,
        knowledge_version: str | None = None,
    ) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        critical_failure = False

        contract_ok = self.contract_revision == CONTRACT_REVISION and self.schema_hash == SCHEMA_HASH
        checks["contract"] = {
            "status": "OK" if contract_ok else "FAILED",
            "contract_revision": self.contract_revision,
            "schema_hash": self.schema_hash,
        }
        critical_failure = critical_failure or not contract_ok

        registry_ok = self.registry is not None
        if registry_ok and work_id is not None:
            try:
                self.registry.require_work(work_id)
            except Exception as exc:
                registry_ok = False
                checks["registry"] = {"status": "FAILED", "code": getattr(exc, "code", "WORK_NOT_FOUND")}
        if "registry" not in checks:
            checks["registry"] = {"status": "OK" if registry_ok else "FAILED"}
        critical_failure = critical_failure or not registry_ok

        manifest_ok = self.operation_manifest is not None
        manifest_path = getattr(self.operation_manifest, "path", None)
        if manifest_ok and isinstance(manifest_path, Path):
            manifest_ok = manifest_path.is_file()
        checks["operation_manifest"] = {
            "status": "OK" if manifest_ok else "FAILED",
            "path_configured": isinstance(manifest_path, Path),
        }
        critical_failure = critical_failure or not manifest_ok

        domain_ok = self.domain_registry is not None
        checks["domain_registry"] = {"status": "OK" if domain_ok else "FAILED"}
        critical_failure = critical_failure or not domain_ok

        projections: dict[str, Any] = {}
        degraded = False
        if self.projection_runner is not None:
            runner = self.projection_runner
            providers = getattr(runner, "providers", {})
            for projection_id in sorted(providers):
                health = runner.health(projection_id, knowledge_version)
                if hasattr(health, "model_dump"):
                    health_payload = health.model_dump(mode="json")
                elif isinstance(health, Mapping):
                    health_payload = dict(health)
                else:
                    health_payload = {"status": str(health)}
                if work_id is not None and health_payload.get("knowledge_version") not in {None, knowledge_version}:
                    continue
                projections[projection_id] = health_payload
                if health_payload.get("status") in {"FAILED", "STALE"} or health_payload.get("dead_letter"):
                    degraded = True
                if health_payload.get("error_code") == "CAPABILITY_UNSUPPORTED":
                    degraded = True

        checks["openapi"] = {
            "status": "OK" if self.openapi_hash else "NOT_COMPUTED",
            "hash": self.openapi_hash,
        }
        if critical_failure:
            status = "NOT_READY"
        elif degraded:
            status = "DEGRADED"
        else:
            status = "READY"
        return {
            "status": status,
            "liveness": True,
            "contract_revision": self.contract_revision,
            "schema_hash": self.schema_hash,
            "checks": checks,
            "projections": projections,
        }


__all__ = ["ReadinessService"]
