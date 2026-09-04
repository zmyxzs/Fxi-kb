"""
fxi.storage.backup - 数据库与关键资产快照备份管理器
"""

import shutil
import time
from pathlib import Path
from typing import Optional

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class BackupManager:
    """快照备份管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def create_snapshot(self, target_dir: Optional[Path] = None) -> Path:
        """
        执行 SQLite 在线安全快照 (VACUUM INTO) 并归档
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        if target_dir is None:
            target_dir = self.config.data_dir / "backups" / f"snapshot_{timestamp}"
        target_dir.mkdir(parents=True, exist_ok=True)

        # 1. 数据库在线热备份 (VACUUM INTO)
        client = DatabaseClient(self.config.sqlite_path)
        backup_db_path = target_dir / "manifest.sqlite"
        with client.get_connection() as conn:
            conn.execute(f"VACUUM INTO '{backup_db_path.as_posix()}';")

        # 2. 拷贝作品和技能的纯文本文件元数据 (若存在)
        if self.config.projects_dir.is_dir():
            target_projects = target_dir / "projects"
            shutil.copytree(self.config.projects_dir, target_projects, dirs_exist_ok=True)

        if self.config.skills_dir.is_dir():
            target_skills = target_dir / "skills"
            shutil.copytree(self.config.skills_dir, target_skills, dirs_exist_ok=True)

        return target_dir
