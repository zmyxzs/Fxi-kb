from __future__ import annotations

from pathlib import Path

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import EvidenceRef, Scope, Validity
from fxi.sources.adapters import LocalTextAdapter
from fxi.sources.evidence_coordinates import EvidenceService, EvidenceValidationError
from fxi.sources.snapshot_service import SnapshotService
from fxi.sources.source_bindings import SourceBinding, SourceBindingService


def test_evidence_uses_half_open_coordinates_and_expected_text(tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "source.txt").write_text("abcdef", encoding="utf-8")
    revision = LocalTextAdapter(input_root, source_id="source-a").fetch("source.txt")
    binding = SourceBindingService().bind(
        SourceBinding(
            work_id="work-a",
            source_id="source-a",
            role="reference",
            branch_id="main",
            validity=Validity(),
            license="synthetic",
            access="local",
        )
    )
    snapshots = SnapshotService(tmp_path / "store")
    snapshot = snapshots.create(revision, binding)
    evidence = EvidenceRef(
        source_snapshot_ref=snapshot.snapshot_id,
        source_id="source-a",
        source_version=revision.source_version,
        document_id="document-1",
        start=1,
        end=4,
        excerpt_hash=sha256_hex("bcd"),
        normalization_version=revision.normalization_version,
        scope=Scope(work_id="work-a", branch_id="main", as_of=1, purpose="test", actor="tester"),
        license="synthetic",
    )

    result = EvidenceService(tmp_path / "store", snapshot_service=snapshots).validate(
        evidence, expected_text="bcd"
    )
    assert result.text == "bcd"
    assert result.ref.start == 1
    assert result.ref.end == 4


def test_evidence_rejects_hash_text_and_scope_mismatch(tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "source.txt").write_text("abcdef", encoding="utf-8")
    revision = LocalTextAdapter(input_root, source_id="source-a").fetch("source.txt")
    binding = SourceBindingService().bind(
        SourceBinding(
            work_id="work-a",
            source_id="source-a",
            role="reference",
            branch_id="main",
            validity=Validity(),
            license="synthetic",
            access="local",
        )
    )
    snapshots = SnapshotService(tmp_path / "store")
    snapshot = snapshots.create(revision, binding)
    service = EvidenceService(tmp_path / "store", snapshot_service=snapshots)
    base = {
        "source_snapshot_ref": snapshot.snapshot_id,
        "source_id": "source-a",
        "source_version": revision.source_version,
        "document_id": "document-1",
        "start": 0,
        "end": 3,
        "excerpt_hash": sha256_hex("abc"),
        "normalization_version": revision.normalization_version,
    }
    with pytest.raises(EvidenceValidationError):
        service.validate(EvidenceRef(**base), expected_text="abd")
    with pytest.raises(EvidenceValidationError):
        service.validate(
            EvidenceRef(
                **base,
                scope=Scope(work_id="other-work", branch_id="main", as_of=1, purpose="test", actor="tester"),
            )
        )


def test_evidence_requires_snapshot_service_for_snapshot_ref(tmp_path: Path):
    ref = EvidenceRef(
        source_snapshot_ref="snapshot-unknown",
        source_id="source-a",
        source_version="a" * 64,
        document_id="document-1",
        start=0,
        end=1,
        excerpt_hash=sha256_hex("a"),
        normalization_version="newline-bom-v1",
    )
    with pytest.raises(EvidenceValidationError):
        EvidenceService(tmp_path).validate(ref)
