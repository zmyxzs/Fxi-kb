"""
Tests for SQLite schema initialization, migrations, transaction rollback, and backup.
"""

import sqlite3
from pathlib import Path

import pytest

from fxi.core.config import FxiConfig
from fxi.storage.backup import BackupManager
from fxi.storage.sqlite_client import DatabaseClient, ensure_entity, ensure_work


def test_fresh_database_schema_initialization(temp_workspace: FxiConfig):
    """验证全新数据库初始化包含了所有预期的 v2 完整性表与字段。"""
    client = DatabaseClient(temp_workspace.sqlite_path)
    with client.get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        expected_tables = {
            "works",
            "entities",
            "entity_phases",
            "causal_events",
            "causal_links",
            "state_events",
            "state_event_receipts",
            "timeline_event_mappings",
            "v2_source_snapshots",
            "v2_reviews",
            "v2_proposals",
            "v2_approvals",
            "v2_commits",
            "v2_work_heads",
            "v2_commit_documents",
            "v2_commit_events",
            "v2_commit_state_changes",
            "v2_commit_knowledge",
            "v2_commit_index",
            "v2_projection_tasks",
        }
        missing = expected_tables - tables
        assert not missing, f"Fresh DB missing tables: {missing}"

        # 检查关键新增列
        approval_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(v2_approvals)").fetchall()
        }
        assert {"work_id", "actor_id", "role", "expires_at"}.issubset(approval_cols)

        commit_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(v2_commits)").fetchall()
        }
        assert {"source_version", "actor_id"}.issubset(commit_cols)


def test_legacy_schema_migration(tmp_path: Path):
    """验证旧版本缺少新增列的 SQLite 数据库能平滑迁移，且既有数据不丢失。"""
    legacy_db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(legacy_db_path))
    # 模拟旧版无 work_id/actor_id 的 v2_approvals 与旧版 v2_commits
    conn.execute(
        """
        CREATE TABLE v2_approvals (
            approval_id TEXT PRIMARY KEY NOT NULL,
            action TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_hash TEXT NOT NULL,
            expected_version TEXT NOT NULL,
            validity TEXT NOT NULL,
            consumed_by TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO v2_approvals
        (approval_id, action, target_id, target_hash, expected_version, validity, created_at)
        VALUES ('app_old_1', 'chapter_commit', 'prop_1', 'hash_1', 'v0', 'VALID', '2026-01-01')
        """
    )
    conn.commit()
    conn.close()

    # 使用 DatabaseClient 打开此 legacy DB，触发自动迁移
    client = DatabaseClient(legacy_db_path)
    with client.get_connection() as check_conn:
        cols = {
            row["name"]
            for row in check_conn.execute("PRAGMA table_info(v2_approvals)").fetchall()
        }
        assert {"work_id", "actor_id", "role", "expires_at"}.issubset(cols)

        # 验证旧数据保留
        row = check_conn.execute(
            "SELECT * FROM v2_approvals WHERE approval_id = 'app_old_1'"
        ).fetchone()
        assert row is not None
        assert row["action"] == "chapter_commit"
        assert row["work_id"] is None  # 新增列默认为 NULL


def test_legacy_fts_schema_migration(tmp_path: Path):
    """旧 FTS 表升级后保留 legacy 行，并具备来源版本绑定列。"""
    legacy_db_path = tmp_path / "legacy-fts.sqlite"
    conn = sqlite3.connect(str(legacy_db_path))
    conn.execute(
        """CREATE VIRTUAL TABLE fts_scenes USING fts5(
            scene_uuid UNINDEXED,
            work_id UNINDEXED,
            chapter_index UNINDEXED,
            segmented_content,
            tokenize = 'unicode61'
        )"""
    )
    conn.execute(
        "INSERT INTO fts_scenes VALUES (?, ?, ?, ?)",
        ("legacy-scene", "work-a", 1, "旧索引词"),
    )
    conn.commit()
    conn.close()

    DatabaseClient(legacy_db_path)
    with sqlite3.connect(str(legacy_db_path)) as migrated:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(fts_scenes)")}
        row = migrated.execute(
            "SELECT scene_uuid, work_id, source_id, source_version, segmented_content FROM fts_scenes"
        ).fetchone()

    assert {"source_id", "source_version"}.issubset(columns)
    assert row == ("legacy-scene", "work-a", None, None, "旧索引词")


def test_transaction_rollback_on_error(temp_workspace: FxiConfig):
    """验证事务块遇到异常时完整回滚，不留下半写状态。"""
    client = DatabaseClient(temp_workspace.sqlite_path)

    # 预先确保 work 存在
    with client.transaction() as cur:
        ensure_work(cur, "work_tx")

    with pytest.raises(sqlite3.IntegrityError):
        with client.transaction() as cur:
            cur.execute(
                "INSERT INTO entities (entity_id, work_id, category, name, file_path, updated_at) VALUES ('e_temp', 'work_tx', 'character', 'Temp', '', datetime('now'))"
            )
            # 故意触发外键或主键约束冲突
            cur.execute(
                "INSERT INTO entities (entity_id, work_id, category, name, file_path, updated_at) VALUES ('e_temp', 'work_tx', 'character', 'Duplicate', '', datetime('now'))"
            )

    with client.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM entities WHERE work_id = 'work_tx' AND entity_id = 'e_temp'"
        ).fetchone()
        assert row is None, "异常后实体记录未被回滚"


def test_backup_and_snapshot_integrity(temp_workspace: FxiConfig, tmp_path: Path):
    """验证 BackupManager 的热快照生成与数据一致性。"""
    client = DatabaseClient(temp_workspace.sqlite_path)
    with client.transaction() as cur:
        ensure_work(cur, "work_backup")
        ensure_entity(cur, "work_backup", "hero_1", name="勇者")

    backup_mgr = BackupManager(temp_workspace)
    snapshot_dir = tmp_path / "backup_out"
    result_path = backup_mgr.create_snapshot(snapshot_dir)

    assert result_path.exists()
    backup_db = result_path / "manifest.sqlite"
    assert backup_db.exists()

    # 打开备份的 SQLite 检查数据一致性
    backup_client = DatabaseClient(backup_db)
    with backup_client.get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM entities WHERE work_id = 'work_backup' AND entity_id = 'hero_1'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "勇者"
