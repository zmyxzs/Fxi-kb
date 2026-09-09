"""重建、备份与恢复边界的回归测试。"""

from pathlib import Path

import pytest

from fxi.core.config import FxiConfig
from fxi.sources.evidence_store import EvidenceStore
from fxi.storage import backup as backup_module
from fxi.storage.backup import BackupManager
from fxi.storage.rebuild import Rebuilder
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


def _document(text: str = "第一章\n不可变正文") -> SourceDocumentInput:
    return SourceDocumentInput(
        document_id="ch001",
        chapter_index=1,
        raw_bytes=text.encode("utf-8"),
        text=text,
        relative_path="chapters/ch001.md",
    )


def test_rebuild_preserves_immutable_source_and_commit_records(temp_workspace: FxiConfig):
    """重建只能刷新派生投影，不能抹掉来源快照和提交日志。"""

    source_store = EvidenceStore(temp_workspace.sources_dir)
    source_snapshot = source_store.create_snapshot("book", [_document()], version="v1")
    client = DatabaseClient(temp_workspace.sqlite_path)
    with client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO v2_source_snapshots
            (snapshot_id, work_id, source_id, version, manifest_hash, documents_json, object_root, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot-1",
                "work_a",
                "book",
                source_snapshot.version,
                source_snapshot.manifest_hash,
                "[]",
                "objects/book/v1",
                "snapshot-request-1",
                "2026-01-01T00:00:00Z",
            ),
        )
        cur.execute(
            """
            INSERT INTO v2_commits
            (commit_id, work_id, proposal_id, text_hash, chapter_version, payload_hash,
             source_version, actor_id, receipt_json, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "commit-1",
                "work_a",
                "proposal-1",
                "a" * 64,
                "chapter-1",
                "b" * 64,
                source_snapshot.version,
                "writer-1",
                "{}",
                "commit-request-1",
                "2026-01-01T00:00:00Z",
            ),
        )

    report = Rebuilder(temp_workspace).rebuild_all()

    assert report.success
    with client.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM v2_source_snapshots WHERE snapshot_id = 'snapshot-1'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM v2_commits WHERE commit_id = 'commit-1'"
        ).fetchone()[0] == 1


def test_rebuild_reports_unreadable_source_file(temp_workspace: FxiConfig):
    """单个坏来源文件应成为可定位告警，而不是抛出无上下文解码异常。"""

    bad_file = temp_workspace.sources_dir / "book" / "scenes" / "bad.md"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_bytes(b"\xff\xfe\xff")

    report = Rebuilder(temp_workspace).rebuild_all()

    assert not report.success
    assert any(str(bad_file) in warning for warning in report.warnings)


def test_rebuild_refuses_non_replayable_projection_without_mutation(
    temp_workspace: FxiConfig,
):
    """不可完整重放的投影存在时，重建必须在清理前失败并保持原数据。"""

    client = DatabaseClient(temp_workspace.sqlite_path)
    with client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO causal_events
            (event_id, work_id, timeline_id, scene_uuid, narrative_order,
             physical_time, summary, is_canon, status)
            VALUES ('event-stale-guard', 'work_a', 'main', 'scene-guard',
                    1, 'unknown', '不可安全重放的事件', 0, 'mutated')
            """
        )

    report = Rebuilder(temp_workspace).rebuild_all()

    assert not report.success
    assert report.cleared_tables == []
    assert any("causal_events(1)" in warning for warning in report.warnings)
    with client.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM causal_events WHERE event_id = 'event-stale-guard'"
        ).fetchone()[0] == 1


def test_backup_copies_immutable_objects_and_manifests(
    temp_workspace: FxiConfig, tmp_path: Path
):
    """备份同时保留 source object 内容、对象清单和兼容来源清单。"""

    store = VersionedStore(temp_workspace.sources_dir)
    store.create_snapshot("book", [_document()], version="v1")
    source_manifest = temp_workspace.sources_dir / "book" / "source.yaml"
    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    source_manifest.write_text("source_id: book\nversion: v1\n", encoding="utf-8")

    result = BackupManager(temp_workspace).create_snapshot(tmp_path / "backup")

    object_root = result / "sources" / "objects" / "book" / "v1"
    assert (object_root / "snapshot.yaml").is_file()
    assert (object_root / "ch001" / "raw.bin").read_bytes() == "第一章\n不可变正文".encode("utf-8")
    assert (result / "sources" / "book" / "source.yaml").read_text(encoding="utf-8") == source_manifest.read_text(
        encoding="utf-8"
    )


def test_backup_failure_does_not_leave_publishable_partial_snapshot(
    temp_workspace: FxiConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """任一归档阶段失败时，不留下可被误认为成功的目标目录。"""

    original_copytree = backup_module.shutil.copytree

    def fail_sources(source, destination, *args, **kwargs):
        if Path(source) == temp_workspace.sources_dir:
            raise OSError("source archive failed")
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(backup_module.shutil, "copytree", fail_sources)
    target = tmp_path / "partial-backup"

    with pytest.raises(OSError, match="source archive failed"):
        BackupManager(temp_workspace).create_snapshot(target)

    assert not target.exists()


def test_backup_missing_database_is_visible_and_creates_no_target(
    temp_workspace: FxiConfig, tmp_path: Path
):
    missing_config = temp_workspace.model_copy(
        update={"sqlite_path": temp_workspace.data_dir / "missing.sqlite"}
    )
    target = tmp_path / "missing-db-backup"

    with pytest.raises(FileNotFoundError, match="数据库"):
        BackupManager(missing_config).create_snapshot(target)

    assert not target.exists()
