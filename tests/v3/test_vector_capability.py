"""Vector/RRF capability tests; no fake embedding fallback is permitted."""

from __future__ import annotations

import pytest

from fxi.knowledge.contracts import Scope
from fxi.knowledge.objects import KnowledgeVersion
from fxi.knowledge.projection_runner import ProjectionRunner
from fxi.index_retrieval.vector_adapter import (
    VectorCapabilityAdapter,
    VectorCapabilityError,
    VectorProjectionProvider,
)


def _version() -> KnowledgeVersion:
    scope = Scope(work_id="work-vector", branch_id="branch-main", as_of=1, purpose="synthetic-test", actor="actor-test")
    return KnowledgeVersion(
        knowledge_version="knowledge-vector-v1",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        source_snapshot_refs=("snapshot-source-synthetic",),
        actor=scope.actor,
    )


def test_vector_operations_are_explicitly_unsupported() -> None:
    adapter = VectorCapabilityAdapter()
    assert adapter.capabilities().status == "UNSUPPORTED"
    for operation in ("embed", "vector_search", "rrf", "enqueue_offline", "failover"):
        with pytest.raises(VectorCapabilityError) as caught:
            getattr(adapter, operation)("synthetic")
        assert caught.value.code == "CAPABILITY_UNSUPPORTED"


def test_vector_projection_returns_capability_failure_without_fallback() -> None:
    version = _version()
    provider = VectorProjectionProvider()
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))

    manifest = runner.run("vector", version)
    assert manifest.status == "FAILED"
    assert manifest.error_code == "CAPABILITY_UNSUPPORTED"
    assert manifest.dead_letter is True
    assert runner.health("vector", version).ready is False
