"""Synthetic F10 clean-break retirement evidence."""

from __future__ import annotations

from pathlib import Path

from fxi.ops.recovery_probe import RecoveryReport
from fxi.ops.retire_manifest import RetireConfirmation, RetireManifest


def test_retire_requires_recovery_and_preserves_sentinel(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    disposable = root / "projection-cache"
    disposable.mkdir(parents=True)
    (disposable / "record.txt").write_text("derived", encoding="utf-8")
    sentinel = root / "authority.txt"
    sentinel.write_text("authoritative", encoding="utf-8")

    plan = RetireManifest(targets=("projection-cache",)).dry_run(root)
    assert plan.status == "READY"
    assert plan.executable is False
    blocked = RetireManifest(targets=("projection-cache",)).execute(
        plan,
        confirm_hashes=RetireConfirmation(plan.root_hash, plan.target_set_hash, plan.sentinel_hash),
    )
    assert blocked.status == "BLOCKED"
    assert "RECOVERY_REQUIRED" in blocked.blockers
    assert disposable.exists()
    assert sentinel.read_text(encoding="utf-8") == "authoritative"


def test_retire_executes_only_confirmed_target_after_recovery(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    disposable = root / "projection-cache"
    disposable.mkdir(parents=True)
    (disposable / "record.txt").write_text("derived", encoding="utf-8")
    (root / "authority.txt").write_text("authoritative", encoding="utf-8")
    recovery = RecoveryReport(
        status="PASSED",
        backup_dir=str(tmp_path / "backup"),
        target_dir=str(tmp_path / "restored"),
        backup_root_hash="a" * 64,
        target_root_hash="a" * 64,
        manifest_hash="b" * 64,
        file_count=1,
        sqlite_integrity="ok",
    )
    manifest = RetireManifest(targets=("projection-cache",), recovery_report=recovery)
    plan = manifest.dry_run(root)
    confirmation = RetireConfirmation(
        root_hash=plan.root_hash,
        target_set_hash=plan.target_set_hash,
        sentinel_hash=plan.sentinel_hash,
        recovery_report_hash=recovery.report_hash,
    )

    report = manifest.execute(plan, confirm_hashes=confirmation)

    assert plan.executable is True
    assert report.success is True
    assert report.status == "EXECUTED"
    assert report.removed == ("projection-cache",)
    assert not disposable.exists()
    assert (root / "authority.txt").is_file()


def test_retire_stale_hash_and_path_traversal_are_blocked(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    disposable = root / "projection-cache"
    disposable.mkdir(parents=True)
    (disposable / "record.txt").write_text("derived", encoding="utf-8")
    (root / "authority.txt").write_text("authoritative", encoding="utf-8")
    manifest = RetireManifest(targets=("projection-cache",), recovery_verified=True)
    plan = manifest.dry_run(root)
    (root / "authority.txt").write_text("changed", encoding="utf-8")
    stale = manifest.execute(
        plan,
        confirm_hashes=RetireConfirmation(plan.root_hash, plan.target_set_hash, plan.sentinel_hash),
    )
    assert stale.status == "BLOCKED"
    assert "RETIRE_PLAN_STALE" in stale.blockers
    assert disposable.exists()

    traversal = RetireManifest(targets=("../outside",)).dry_run(root)
    assert traversal.status == "BLOCKED"
    assert traversal.executable is False
