"""清理、快照和恢复边界的回归覆盖。"""

from pathlib import Path

import pytest

from fxi.cli.commands_project import clear_parsing_work
from fxi.core.exceptions import ValidationError
from fxi.storage.backup import BackupManager


def _prepare_work(config, work_id: str = "work_a") -> tuple[Path, Path, Path]:
    raw_dir = config.sources_dir / work_id / "chapters"
    style_dir = config.projects_dir / work_id / "style"
    entities_dir = config.projects_dir / work_id / "entities" / "characters"
    raw_dir.mkdir(parents=True, exist_ok=True)
    style_dir.mkdir(parents=True, exist_ok=True)
    entities_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "001.md").write_text("正文底本", encoding="utf-8")
    (style_dir / "style.yaml").write_text("rules: []\n", encoding="utf-8")
    (entities_dir / "char.md").write_text("candidate", encoding="utf-8")
    (config.data_dir / f"{work_id}.luna-extraction.json").write_text("{}", encoding="utf-8")
    return raw_dir, style_dir, entities_dir


def test_clear_rejects_path_traversal(temp_workspace):
    with pytest.raises(ValidationError):
        clear_parsing_work(temp_workspace, "../outside", dry_run=True)


def test_clear_preserves_protected_assets_and_creates_verified_backup(temp_workspace):
    raw_dir, style_dir, entities_dir = _prepare_work(temp_workspace)

    report = clear_parsing_work(temp_workspace, "work_a")

    assert report["status"] == "SUCCESS"
    assert Path(report["backup_dir"]).joinpath(BackupManager.MANIFEST_NAME).is_file()
    assert (raw_dir / "001.md").read_text(encoding="utf-8") == "正文底本"
    assert (style_dir / "style.yaml").is_file()
    assert not (entities_dir / "char.md").exists()
    assert not (temp_workspace.data_dir / "work_a.luna-extraction.json").exists()


def test_restore_validates_manifest_and_uses_isolated_target(temp_workspace, tmp_path: Path):
    _prepare_work(temp_workspace)
    snapshot = BackupManager(temp_workspace).create_snapshot(tmp_path / "snapshot")
    target = temp_workspace.workspace_root.parent / "fxi-restore-target"

    try:
        restored = BackupManager(temp_workspace).restore_snapshot(snapshot, target)
        assert (restored / "snapshot.manifest.json").is_file()
        assert (restored / "sources" / "work_a" / "chapters" / "001.md").is_file()
    finally:
        if target.exists():
            import shutil

            shutil.rmtree(target)


def test_restore_rejects_tampered_snapshot(temp_workspace, tmp_path: Path):
    _prepare_work(temp_workspace)
    snapshot = BackupManager(temp_workspace).create_snapshot(tmp_path / "snapshot")
    (snapshot / "manifest.sqlite").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="校验失败"):
        BackupManager(temp_workspace).restore_snapshot(
            snapshot,
            temp_workspace.workspace_root.parent / "fxi-restore-tampered",
        )
