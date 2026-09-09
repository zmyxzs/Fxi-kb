"""Synthetic F5 tests: source composition and as-of fail-closed checks."""

from hashlib import sha256

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.as_of import AsOf, AsOfError, validity_contains
from fxi.knowledge.contracts import SourceBindingRef, SourceSnapshotRef, Validity
from fxi.knowledge.source_graph import SourceGraph, SourceGraphError


def _binding(binding_id: str, source_id: str, priority: int) -> SourceBindingRef:
    return SourceBindingRef(
        binding_id=binding_id,
        work_id="work-synthetic",
        source_id=source_id,
        role="reference",
        priority=priority,
        branch_id="branch-main",
        validity=Validity(valid_from=0, valid_to=10),
        license="synthetic-license",
        access="fixture",
    )


def _snapshot(binding: SourceBindingRef, version: str) -> SourceSnapshotRef:
    return SourceSnapshotRef(
        snapshot_id="snapshot-" + binding.source_id,
        work_id=binding.work_id,
        source_id=binding.source_id,
        source_version=version,
        content_hash=sha256_hex(binding.source_id),
        binding_id=binding.binding_id,
        document_refs=("document-" + binding.source_id,),
    )


def test_composite_is_deterministic_and_priority_is_policy_input() -> None:
    first = _binding("binding-a", "source-a", 1)
    second = _binding("binding-b", "source-b", 5)
    graph = SourceGraph({first.binding_id: _snapshot(first, "version-a"), second.binding_id: _snapshot(second, "version-b")})

    composed = graph.compose_snapshot("work-synthetic", (first, second), as_of=AsOf(value=3))
    repeated = graph.compose_snapshot("work-synthetic", (second, first), as_of=3)

    assert composed.composite_hash == repeated.composite_hash
    assert composed.binding_ids == ("binding-b", "binding-a")
    assert composed.source_versions == ("version-b", "version-a")
    assert composed.policy["priority_is_policy_input_only"] is True
    assert len(composed.policy_hash) == 64
    assert len(composed.composite_hash) == 64


def test_composite_rejects_missing_snapshot_and_expired_as_of() -> None:
    binding = _binding("binding-a", "source-a", 1)
    graph = SourceGraph({})
    with pytest.raises(SourceGraphError) as missing:
        graph.compose_snapshot("work-synthetic", (binding,), as_of=3)
    assert missing.value.code == "NOT_FOUND"

    graph = SourceGraph({binding.binding_id: _snapshot(binding, "version-a")})
    with pytest.raises(SourceGraphError) as stale:
        graph.compose_snapshot("work-synthetic", (binding,), as_of=10)
    assert stale.value.code == "STALE_VERSION"


def test_as_of_accepts_iso_time_and_rejects_incomparable_values() -> None:
    selected = AsOf(value="2026-01-01T00:00:00Z")
    assert validity_contains(Validity(valid_from="2025-01-01", valid_to="2027-01-01"), selected)
    with pytest.raises(AsOfError):
        AsOf(value="not-a-time")
    with pytest.raises(AsOfError):
        validity_contains(Validity(valid_from=1, valid_to=4), selected)
