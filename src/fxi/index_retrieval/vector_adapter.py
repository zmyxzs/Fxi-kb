"""Explicit vector/RRF capability boundary.

No embedding model, vector database, offline queue, or failover policy is
implemented here.  The adapter is intentionally useful as a capability probe
and raises a stable public error instead of pretending that configuration is
an implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fxi.knowledge.capabilities import Capability
from fxi.knowledge.contracts import CONTRACT_REVISION, ErrorCode, SCHEMA_HASH, SCHEMA_VERSION
from fxi.knowledge.objects import KnowledgeVersion
from fxi.knowledge.projection_contracts import (
    CapabilityUnsupportedError,
    ProjectionHealth,
    ProjectionManifest,
    ProjectionStatus,
)


class VectorCapabilityError(CapabilityUnsupportedError):
    """Stable ``CAPABILITY_UNSUPPORTED`` error for every unimplemented port."""


class VectorCapabilityAdapter:
    capability_id = "vector"
    capability_version = "v3-vector-capability-1"

    def capabilities(self) -> Capability:
        return Capability(
            capability_id=self.capability_id,
            status="UNSUPPORTED",
            version=self.capability_version,
            details={
                "contract_revision": CONTRACT_REVISION,
                "schema_version": SCHEMA_VERSION,
                "schema_hash": SCHEMA_HASH,
                "implemented": False,
                "operations": ("embedding", "vector_search", "rrf", "offline", "failover"),
            },
        )

    def _unsupported(self, operation: str) -> None:
        raise VectorCapabilityError(operation)

    def embed(self, *args: Any, **kwargs: Any) -> list[float]:
        self._unsupported("embedding")

    def vector_search(self, *args: Any, **kwargs: Any) -> list[Mapping[str, Any]]:
        self._unsupported("vector_search")

    def rrf(self, *args: Any, **kwargs: Any) -> list[Mapping[str, Any]]:
        self._unsupported("rrf")

    def enqueue_offline(self, *args: Any, **kwargs: Any) -> None:
        self._unsupported("offline_queue")

    def failover(self, *args: Any, **kwargs: Any) -> str:
        self._unsupported("failover")


class VectorProjectionProvider(VectorCapabilityAdapter):
    """A projection provider that records unsupported capability explicitly."""

    projection_id = "vector"
    projection_version = "v3-vector-capability-1"

    def build(self, snapshot: KnowledgeVersion) -> ProjectionManifest:
        self._unsupported("embedding")

    def drop(self, manifest: ProjectionManifest) -> None:
        return None

    def health(self, manifest: ProjectionManifest | None) -> ProjectionHealth:
        if manifest is None:
            return ProjectionHealth(
                projection_id=self.projection_id,
                projection_version=self.projection_version,
                status=ProjectionStatus.DROPPED.value,
            )
        return ProjectionHealth(
            projection_id=manifest.projection_id,
            projection_version=manifest.projection_version,
            status=manifest.status,
            ready=False,
            stale=manifest.status == ProjectionStatus.STALE.value,
            knowledge_version=manifest.knowledge_version,
            projection_hash=manifest.projection_hash,
            error_code=manifest.error_code,
            error_message=manifest.error_message,
            dead_letter=manifest.dead_letter,
            readiness=manifest.readiness,
            record_count=manifest.record_count,
            duration_ms=manifest.duration_ms,
        )


VectorAdapter = VectorCapabilityAdapter
VectorProvider = VectorProjectionProvider
RRFAdapter = VectorCapabilityAdapter


__all__ = [
    "RRFAdapter",
    "VectorAdapter",
    "VectorCapabilityAdapter",
    "VectorCapabilityError",
    "VectorProjectionProvider",
    "VectorProvider",
]
