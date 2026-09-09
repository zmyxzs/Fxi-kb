"""Synthetic F10 backup and restore evidence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fxi.ops.recovery_probe import RecoveryProbe
from fxi.storage.backup import BackupManager
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


def test_backup_restore_probe_preserves_synthetic_authority(temp_workspace, tmp_path: Path) -> None:
    client = DatabaseClient(temp_workspace.sqlite_path)
    with client.transaction() as cur:
        ensure_work(cur, "synthetic-recovery")
        cur.execute(
            "INSERT INTO entities (entity_id, work_id, category, name, file_path, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("object-1", "synthetic-recovery", "generic", "Synthetic object", "", "2026-01-01T00:00:00Z"),
        )
    source_file = temp_workspace.sources_dir / "synthetic-source" / "document.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("synthetic source text", encoding="utf-8")

    snapshot = BackupManager(temp_workspace).create_snapshot(tmp_path / "backup")
    restored = BackupManager(temp_workspace).restore_snapshot(
        snapshot,
        temp_workspace.workspace_root.parent / f"{temp_workspace.workspace_root.name}-restored",
    )
    report = RecoveryProbe().verify(snapshot, restored)

    assert report.status == "PASSED"
    assert report.passed is True
    assert report.backup_root_hash == report.target_root_hash
    assert report.file_count > 0
    assert report.sqlite_integrity == "ok"
    with sqlite3.connect(restored / "manifest.sqlite") as conn:
        assert conn.execute(
            "SELECT name FROM entities WHERE work_id = ? AND entity_id = ?",
            ("synthetic-recovery", "object-1"),
        ).fetchone() == ("Synthetic object",)
    assert (restored / "sources" / "synthetic-source" / "document.txt").read_text(encoding="utf-8") == (
        "synthetic source text"
    )


def test_recovery_probe_exposes_tampered_restore_and_extra_file(tmp_path: Path, temp_workspace) -> None:
    source_file = temp_workspace.sources_dir / "synthetic" / "document.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("stable", encoding="utf-8")
    snapshot = BackupManager(temp_workspace).create_snapshot(tmp_path / "backup")
    restored = BackupManager(temp_workspace).restore_snapshot(
        snapshot,
        temp_workspace.workspace_root.parent / f"{temp_workspace.workspace_root.name}-tamper-restored",
    )

    tampered = restored / "sources" / "synthetic" / "document.txt"
    tampered.write_text("changed", encoding="utf-8")
    report = RecoveryProbe().verify(snapshot, restored)
    assert report.status == "FAILED"
    assert report.passed is False
    assert any("restore validation failed" in error for error in report.errors)

    tampered.write_text("stable", encoding="utf-8")
    (restored / "unexpected.txt").write_text("not in manifest", encoding="utf-8")
    extra_report = RecoveryProbe().verify(snapshot, restored)
    assert extra_report.status == "FAILED"
    assert any("restore validation failed" in error for error in extra_report.errors)


def test_restore_rejects_workspace_target_and_corrupt_manifest(temp_workspace, tmp_path: Path) -> None:
    snapshot = BackupManager(temp_workspace).create_snapshot(tmp_path / "backup")
    with pytest.raises(ValueError, match="workspace"):
        BackupManager(temp_workspace).restore_snapshot(snapshot, temp_workspace.workspace_root / "restored")

    (snapshot / BackupManager.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="清单|schema|files"):
        BackupManager(temp_workspace).restore_snapshot(
            snapshot,
            temp_workspace.workspace_root.parent / f"{temp_workspace.workspace_root.name}-corrupt",
        )
