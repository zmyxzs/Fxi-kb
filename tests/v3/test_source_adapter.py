from __future__ import annotations

from pathlib import Path

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import Validity
from fxi.sources.adapters import (
    LocalTextAdapter,
    SourceAdapterError,
    SourceEncodingError,
    SourcePathError,
    SourceResource,
)
from fxi.sources.snapshot_service import SnapshotError, SnapshotService
from fxi.sources.source_bindings import SourceBinding, SourceBindingService


def _binding(*, work_id: str = "work-a", source_id: str = "source-a", priority: int = 0):
    return SourceBinding(
        work_id=work_id,
        source_id=source_id,
        role="reference",
        branch_id="main",
        validity=Validity(),
        license="synthetic",
        access="local",
        allowed_purposes=("extraction",),
        priority=priority,
    )


def test_local_text_adapter_normalizes_and_reports_revision(tmp_path: Path):
    source_file = tmp_path / "synthetic.txt"
    source_file.write_bytes(b"\xef\xbb\xbfalpha\r\nbeta\r")

    revision = LocalTextAdapter(tmp_path, source_id="source-a").fetch(
        SourceResource(path="synthetic.txt", work_id="work-a")
    )

    assert revision.documents[0].text == "alpha\nbeta\n"
    assert revision.documents[0].encoding == "utf-8"
    assert revision.source_version == sha256_hex("alpha\nbeta\n")
    assert revision.content_hash == revision.source_version
    assert LocalTextAdapter(tmp_path).capabilities().read_only is True


def test_local_text_adapter_supports_gb18030_and_rejects_undecodable_bytes(tmp_path: Path):
    source_file = tmp_path / "synthetic.md"
    source_file.write_bytes("合成文本".encode("gb18030"))
    revision = LocalTextAdapter(tmp_path, source_id="source-a").fetch("synthetic.md")
    assert revision.documents[0].encoding == "gb18030"
    assert revision.documents[0].text == "合成文本"

    (tmp_path / "invalid.txt").write_bytes(b"\x81")
    with pytest.raises(SourceEncodingError):
        LocalTextAdapter(tmp_path, source_id="source-a").fetch("invalid.txt")


def test_local_text_adapter_rejects_escape_and_reparse_path(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic", encoding="utf-8")
    with pytest.raises(SourcePathError):
        LocalTextAdapter(root, source_id="source-a").fetch(
            SourceResource(path="..\\outside.txt")
        )

    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 环境不允许测试 symlink")
    with pytest.raises(SourcePathError):
        LocalTextAdapter(root, source_id="source-a").fetch("link.txt")


def test_binding_service_isolated_and_priority_sorted():
    service = SourceBindingService()
    low = service.bind(_binding(source_id="source-low", priority=1))
    high = service.bind(_binding(source_id="source-high", priority=5))
    other = service.bind(_binding(work_id="work-b", source_id="source-other"))

    assert [item.source_id for item in service.list("work-a")] == ["source-high", "source-low"]
    assert service.list("work-b") == (other,)
    assert service.bind(high) == high
    assert service.get(low.binding_id) == low


def test_snapshot_is_immutable_and_reimport_creates_new_active_revision(tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir()
    source_file = input_root / "source.txt"
    source_file.write_text("first", encoding="utf-8")
    adapter = LocalTextAdapter(input_root, source_id="source-a")
    binding = SourceBindingService().bind(_binding())
    snapshots = SnapshotService(tmp_path / "store")

    first_revision = adapter.fetch("source.txt")
    first = snapshots.create(first_revision, binding)
    assert first.status == "PUBLISHED"
    assert snapshots.active("work-a", "source-a", "main") == first

    source_file.write_text("second", encoding="utf-8")
    second = snapshots.create(adapter.fetch("source.txt"), binding)
    assert second.source_version != first.source_version
    assert snapshots.active("work-a", "source-a", "main") == second
    assert snapshots.get(first.snapshot_id) == first


def test_snapshot_failure_keeps_staging_error_and_active_snapshot(tmp_path: Path):
    input_root = tmp_path / "input"
    input_root.mkdir()
    source_file = input_root / "source.txt"
    source_file.write_text("stable", encoding="utf-8")
    adapter = LocalTextAdapter(input_root, source_id="source-a")
    binding = SourceBindingService().bind(_binding())
    snapshots = SnapshotService(tmp_path / "store")
    first = snapshots.create(adapter.fetch("source.txt"), binding)

    mismatched = LocalTextAdapter(input_root, source_id="source-b").fetch("source.txt")
    with pytest.raises(SnapshotError):
        snapshots.create(mismatched, binding)

    assert snapshots.active("work-a", "source-a", "main") == first
    error_files = list((tmp_path / "store" / ".staging").rglob("error.json"))
    assert len(error_files) == 1
    assert "INVALID_SCOPE" in error_files[0].read_text(encoding="utf-8")


def test_source_revision_size_limit_is_explicit(tmp_path: Path):
    (tmp_path / "large.txt").write_text("0123456789", encoding="utf-8")
    with pytest.raises(SourceAdapterError):
        LocalTextAdapter(tmp_path, source_id="source-a", max_bytes=3).fetch("large.txt")
