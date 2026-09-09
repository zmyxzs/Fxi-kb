"""回归测试：公开入口不能把部分失败呈现为成功。"""

from pathlib import Path
import gc

import pytest
import typer

from fxi.cli import commands_ops
from fxi.core.config import FxiConfig
from fxi.core.exceptions import CorruptedDataError, FileLockedError, StorageError
from fxi.ops import operation_manifest as operation_manifest_module
from fxi.ops.operation_manifest import OperationManifest, OperationManifestError
from fxi.storage.rebuild import RebuildReport
from fxi.storage import text_io as text_io_module
from fxi.storage import versioned_store as versioned_store_module
from fxi.storage.text_io import read_markdown_frontmatter, write_markdown_frontmatter
from fxi.timeline.continuity import ContinuityManager


@pytest.fixture
def failure_visibility_config(tmp_path: Path):
    config = FxiConfig(
        workspace_root=tmp_path,
        data_dir=tmp_path / "data",
        projects_dir=tmp_path / "projects",
        skills_dir=tmp_path / "skills",
        sources_dir=tmp_path / "sources",
        materials_dir=tmp_path / "materials",
        sqlite_path=tmp_path / "data" / "manifest.sqlite",
        cache_db_path=tmp_path / "data" / "cache.sqlite",
        jieba_custom_dict_path=tmp_path / "data" / "project_lexicon.txt",
    )
    config.ensure_directories()
    yield config
    gc.collect()


def test_rebuild_cli_does_not_claim_success_when_report_has_warnings(monkeypatch):
    output: list[str] = []

    class StubConsole:
        def print(self, *values, **_kwargs):
            output.append(" ".join(str(value) for value in values))

    class StubRebuilder:
        def rebuild_all(self):
            return RebuildReport(elapsed_seconds=0.01, warnings=["实体文件解析失败"])

    monkeypatch.setattr(commands_ops, "console", StubConsole())
    monkeypatch.setattr(commands_ops, "Rebuilder", StubRebuilder)

    with pytest.raises(typer.Exit) as exc_info:
        commands_ops.rebuild_all()

    assert exc_info.value.exit_code == 1
    assert any("告警" in line for line in output)
    assert not any("重建完成" in line for line in output)


def _write_legacy_ledger(config, content: str) -> Path:
    ledger_path = config.projects_dir / "legacy_work" / "timeline" / "continuity_ledger.yaml"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(content, encoding="utf-8")
    return ledger_path


def test_legacy_continuity_yaml_parse_failure_is_visible(failure_visibility_config):
    _write_legacy_ledger(failure_visibility_config, "chapters:\n  1: [未闭合\n")

    with pytest.raises(CorruptedDataError):
        ContinuityManager(failure_visibility_config).get_continuity("legacy_work", 1)


def test_legacy_continuity_yaml_invalid_chapter_is_visible(failure_visibility_config):
    _write_legacy_ledger(failure_visibility_config, "chapters:\n  1: invalid\n")

    with pytest.raises(CorruptedDataError):
        ContinuityManager(failure_visibility_config).load_ledger("legacy_work")


def test_missing_markdown_file_is_not_reported_as_empty_document(failure_visibility_config):
    with pytest.raises(CorruptedDataError):
        read_markdown_frontmatter(failure_visibility_config.workspace_root / "missing.md")


def test_unclosed_markdown_frontmatter_is_visible(failure_visibility_config):
    path = failure_visibility_config.workspace_root / "broken.md"
    path.write_text("---\nname: broken\n正文未闭合\n", encoding="utf-8")

    with pytest.raises(CorruptedDataError):
        read_markdown_frontmatter(path)


def test_non_mapping_markdown_frontmatter_is_visible(failure_visibility_config):
    path = failure_visibility_config.workspace_root / "broken.md"
    path.write_text("---\n- not-a-mapping\n---\n正文\n", encoding="utf-8")

    with pytest.raises(CorruptedDataError):
        read_markdown_frontmatter(path)


def test_operation_manifest_temp_cleanup_failure_is_visible(tmp_path, monkeypatch):
    def fail_replace(_source, _target):
        raise OSError("replace denied")

    def fail_unlink(_path):
        raise OSError("cleanup denied")

    monkeypatch.setattr(operation_manifest_module.os, "replace", fail_replace)
    monkeypatch.setattr(operation_manifest_module.os, "unlink", fail_unlink)

    with pytest.raises(OperationManifestError) as exc_info:
        OperationManifest(tmp_path / "operation.json")

    assert any("temporary cleanup failed" in note for note in (exc_info.value.__notes__ or ()))


def test_versioned_store_temp_cleanup_failure_is_visible(tmp_path, monkeypatch):
    def fail_replace(_source, _target):
        raise OSError("replace denied")

    def fail_unlink(_path):
        raise OSError("cleanup denied")

    monkeypatch.setattr(versioned_store_module.os, "replace", fail_replace)
    monkeypatch.setattr(versioned_store_module.os, "unlink", fail_unlink)

    with pytest.raises(StorageError) as exc_info:
        versioned_store_module._atomic_write(tmp_path / "object.bin", b"synthetic")

    assert any("临时文件清理失败" in note for note in (exc_info.value.__notes__ or ()))


def test_markdown_temp_cleanup_failure_is_visible(tmp_path, monkeypatch):
    def fail_replace(_source, _target):
        raise OSError("replace denied")

    def fail_remove(_path):
        raise OSError("cleanup denied")

    monkeypatch.setattr(text_io_module.os, "replace", fail_replace)
    monkeypatch.setattr(text_io_module.os, "remove", fail_remove)

    with pytest.raises(FileLockedError, match="临时文件清理失败"):
        write_markdown_frontmatter(tmp_path / "document.md", {"kind": "synthetic"}, "body")
