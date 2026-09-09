"""
fxi.storage.backup - 数据库与关键资产快照备份管理器
"""

import json
import os
import sqlite3
import shutil
import stat
import time
from dataclasses import dataclass
from uuid import uuid4
from pathlib import Path
from typing import Any, Mapping, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.storage.sqlite_client import DatabaseClient


@dataclass(frozen=True, slots=True)
class SnapshotValidation:
    """Validated, content-addressed view of one backup directory."""

    snapshot_dir: Path
    root_hash: str
    manifest_hash: str
    file_count: int
    files: Mapping[str, Mapping[str, Any]]
    sqlite_integrity: str

    @property
    def valid(self) -> bool:
        return self.sqlite_integrity == "ok"


class BackupManager:
    """快照备份管理器"""

    MANIFEST_NAME = "snapshot.manifest.json"

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def _allocate_snapshot_dir(self) -> Path:
        """Create a unique timestamped directory with a bounded collision guard."""
        backup_root = self.config.data_dir / "backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        stem = f"snapshot_{timestamp}_{time.time_ns()}"
        for suffix in range(1000):
            name = stem if suffix == 0 else f"{stem}_{suffix:03d}"
            candidate = backup_root / name
            try:
                candidate.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return candidate
        raise FileExistsError(f"无法分配唯一备份路径: {backup_root}")

    @staticmethod
    def _prepare_explicit_dir(target_dir: Path) -> Path:
        if target_dir.exists() or target_dir.is_symlink():
            raise FileExistsError(f"备份目录已存在: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=False)
        return target_dir

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        """Detect symlinks and Windows junction/reparse points without following them."""

        try:
            if path.is_symlink():
                return True
            attributes = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
        except OSError as exc:
            raise OSError(f"无法检查备份路径安全属性: {path}") from exc
        return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT

    @classmethod
    def _assert_safe_root(cls, root: Path) -> None:
        if not root.exists() or not root.is_dir():
            raise ValueError(f"备份目录不存在或不是目录: {root}")
        if cls._is_reparse_point(root):
            raise OSError(f"备份目录不能是 symlink/junction/reparse point: {root}")
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if cls._is_reparse_point(path):
                raise OSError(f"备份目录包含不允许的 symlink/junction/reparse point: {path}")

    @classmethod
    def _assert_no_symlinks(cls, source_dir: Path) -> None:
        """拒绝跟随工作区内的符号链接、junction 和 reparse point。"""

        cls._assert_safe_root(source_dir)

    @staticmethod
    def _assert_existing_parent_safe(path: Path) -> None:
        current = path
        while current != current.parent:
            if current.exists() and BackupManager._is_reparse_point(current):
                raise OSError(f"路径父级包含不允许的 reparse point: {current}")
            current = current.parent

    @staticmethod
    def _sha256(path: Path) -> str:
        return sha256_hex(path.read_bytes())

    @classmethod
    def _file_records(cls, root: Path) -> dict[str, dict[str, Any]]:
        cls._assert_safe_root(root)
        records: dict[str, dict[str, Any]] = {}
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative == cls.MANIFEST_NAME:
                continue
            metadata = path.stat()
            records[relative] = {
                "sha256": cls._sha256(path),
                "size": metadata.st_size,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        return records

    @staticmethod
    def _root_hash(files: Mapping[str, Mapping[str, Any]]) -> str:
        return sha256_hex({"files": dict(sorted(files.items()))})

    @classmethod
    def _write_manifest(cls, target_dir: Path) -> Path:
        """写入快照文件清单，清单本身不纳入自身 hash。"""
        files = cls._file_records(target_dir)
        manifest = {
            "schema_version": 1,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files": files,
            "file_count": len(files),
            "root_hash": cls._root_hash(files),
            "sqlite_integrity": "ok" if cls._sqlite_integrity(target_dir / "manifest.sqlite") else "failed",
        }
        manifest_path = target_dir / cls.MANIFEST_NAME
        temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, manifest_path)
        return manifest_path

    @classmethod
    def _read_manifest(cls, snapshot_dir: Path) -> dict[str, Any]:
        manifest_path = snapshot_dir / cls.MANIFEST_NAME
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise ValueError(f"快照清单不可读取: {manifest_path}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("快照清单版本不受支持")
        files = payload.get("files")
        if not isinstance(files, dict):
            raise ValueError("快照清单缺少 files")
        for relative, metadata in files.items():
            relative_path = Path(str(relative))
            if relative_path.is_absolute() or ".." in relative_path.parts or not str(relative).strip():
                raise ValueError(f"快照清单包含越界路径: {relative}")
            if not isinstance(metadata, dict) or not isinstance(metadata.get("sha256"), str):
                raise ValueError(f"快照清单文件项无效: {relative}")
        return payload

    @staticmethod
    def _sqlite_integrity(path: Path) -> bool:
        if not path.is_file() or BackupManager._is_reparse_point(path):
            return False
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
            result = connection.execute("PRAGMA integrity_check").fetchone()
            return bool(result and str(result[0]).lower() == "ok")
        except (OSError, sqlite3.Error):
            return False
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def validate_snapshot(cls, snapshot_dir: Path | str) -> SnapshotValidation:
        """Validate exact file membership, hashes, root hash and SQLite integrity."""

        supplied = Path(snapshot_dir)
        if supplied.is_symlink():
            raise OSError(f"快照目录不能是 symlink: {supplied}")
        snapshot = supplied.resolve()
        cls._assert_safe_root(snapshot)
        manifest_path = snapshot / cls.MANIFEST_NAME
        manifest = cls._read_manifest(snapshot)
        declared = dict(manifest["files"])
        actual = cls._file_records(snapshot)
        if set(declared) != set(actual):
            missing = sorted(set(declared) - set(actual))
            extra = sorted(set(actual) - set(declared))
            raise ValueError(f"快照文件集合校验失败: missing={missing[:5]}, extra={extra[:5]}")
        folded = [name.casefold() for name in declared]
        if len(folded) != len(set(folded)):
            raise ValueError("快照清单包含大小写别名路径")
        for relative, metadata in declared.items():
            observed = actual[relative]
            if metadata.get("sha256") != observed["sha256"] or metadata.get("size") != observed["size"]:
                raise ValueError(f"快照文件校验失败: {relative}")
            if "mode" in metadata and int(metadata["mode"]) != int(observed["mode"]):
                raise ValueError(f"快照文件权限校验失败: {relative}")
        expected_root = cls._root_hash(actual)
        if manifest.get("root_hash") != expected_root:
            raise ValueError("快照 root hash 校验失败")
        database = snapshot / "manifest.sqlite"
        if "manifest.sqlite" not in declared or not cls._sqlite_integrity(database):
            raise ValueError("快照 SQLite integrity 校验失败")
        return SnapshotValidation(
            snapshot_dir=snapshot,
            root_hash=expected_root,
            manifest_hash=sha256_hex(manifest_path.read_bytes()),
            file_count=len(actual),
            files=declared,
            sqlite_integrity="ok",
        )

    def create_snapshot(self, target_dir: Optional[Path] = None) -> Path:
        """
        执行 SQLite 在线安全快照 (VACUUM INTO) 并归档
        """
        if not self.config.sqlite_path.is_file():
            raise FileNotFoundError(f"数据库不存在，无法创建备份: {self.config.sqlite_path}")

        if target_dir is None:
            target_dir = self._allocate_snapshot_dir()
        else:
            target_dir = self._prepare_explicit_dir(Path(target_dir))

        try:
            # 1. 数据库在线热备份 (VACUUM INTO)
            client = DatabaseClient(self.config.sqlite_path)
            backup_db_path = target_dir / "manifest.sqlite"
            with client.get_connection() as conn:
                escaped = backup_db_path.resolve().as_posix().replace("'", "''")
                conn.execute(f"VACUUM INTO '{escaped}';")

            # 2. 拷贝作品和技能的纯文本文件元数据 (若存在)
            if self.config.projects_dir.is_dir():
                self._assert_no_symlinks(self.config.projects_dir)
                target_projects = target_dir / "projects"
                shutil.copytree(self.config.projects_dir, target_projects, dirs_exist_ok=True)

            if self.config.skills_dir.is_dir():
                self._assert_no_symlinks(self.config.skills_dir)
                target_skills = target_dir / "skills"
                shutil.copytree(self.config.skills_dir, target_skills, dirs_exist_ok=True)

            if self.config.sources_dir.is_dir():
                self._assert_no_symlinks(self.config.sources_dir)
                target_sources = target_dir / "sources"
                shutil.copytree(self.config.sources_dir, target_sources, dirs_exist_ok=True)

            if self.config.materials_dir.is_dir():
                self._assert_no_symlinks(self.config.materials_dir)
                target_materials = target_dir / "materials"
                shutil.copytree(self.config.materials_dir, target_materials, dirs_exist_ok=True)

            self._write_manifest(target_dir)
            self.validate_snapshot(target_dir)
            return target_dir
        except BaseException as exc:
            # The directory was created by this call and must never look like a
            # valid snapshot after a later archive step failed.
            try:
                shutil.rmtree(target_dir)
            except OSError as cleanup_error:
                exc.add_note(f"清理不完整备份失败 {target_dir}: {cleanup_error}")
            raise

    def restore_snapshot(
        self,
        snapshot_dir: Path,
        target_dir: Path,
        *,
        allow_workspace_target: bool = False,
    ) -> Path:
        """校验快照后恢复到一个新的隔离目录。

        默认禁止恢复到当前 workspace，避免把恢复演练误当成在线覆盖。
        目标必须不存在，恢复过程先写入同父目录临时目录，再一次性切换。
        """
        supplied_snapshot = Path(snapshot_dir)
        if supplied_snapshot.is_symlink():
            raise OSError(f"快照目录不能是 symlink: {supplied_snapshot}")
        snapshot = supplied_snapshot.resolve()
        supplied_target = Path(target_dir)
        if supplied_target.exists() and supplied_target.is_symlink():
            raise OSError(f"恢复目标不能是 symlink: {supplied_target}")
        target = supplied_target.resolve()
        if not snapshot.is_dir():
            raise FileNotFoundError(f"快照目录不存在: {snapshot}")
        if target == snapshot or snapshot in target.parents:
            raise ValueError("恢复目标不能位于快照目录内")
        workspace = self.config.workspace_root.resolve()
        if not allow_workspace_target and (target == workspace or workspace in target.parents):
            raise ValueError("默认只允许恢复到 workspace 外的隔离目录")
        if target.exists():
            raise FileExistsError(f"恢复目标已存在: {target}")

        validation = self.validate_snapshot(snapshot)

        self._assert_existing_parent_safe(target.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / f".{target.name}.restore-{uuid4().hex}.tmp"
        try:
            staging.mkdir(parents=False)
            for relative in validation.files:
                relative_path = Path(relative)
                source = snapshot / relative_path
                destination = staging / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination, follow_symlinks=False)
            shutil.copy2(snapshot / self.MANIFEST_NAME, staging / self.MANIFEST_NAME)
            self.validate_snapshot(staging)
            os.replace(staging, target)
            self.validate_snapshot(target)
        except BaseException as exc:
            try:
                shutil.rmtree(staging)
            except OSError as cleanup_error:
                exc.add_note(f"清理不完整恢复目录失败 {staging}: {cleanup_error}")
            if target.exists():
                try:
                    shutil.rmtree(target)
                except OSError as cleanup_error:
                    exc.add_note(f"清理不完整恢复目标失败 {target}: {cleanup_error}")
            raise
        return target


__all__ = ["BackupManager", "SnapshotValidation"]
