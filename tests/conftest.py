"""
Pytest 全局配置与临时测试工作区 Fixtures
"""

import tempfile
from pathlib import Path
import pytest

from fxi.core.config import FxiConfig
from fxi.storage.sqlite_client import DatabaseClient


@pytest.fixture
def temp_workspace(tmp_path: Path) -> FxiConfig:
    """提供完全隔离的临时测试工作区与数据库"""
    data_dir = tmp_path / "data"
    projects_dir = tmp_path / "projects"
    skills_dir = tmp_path / "skills"
    sources_dir = tmp_path / "sources"
    materials_dir = tmp_path / "materials"
    sqlite_path = data_dir / "manifest.sqlite"
    cache_db_path = data_dir / "cache.sqlite"
    jieba_dict = data_dir / "project_lexicon.txt"

    cfg = FxiConfig(
        workspace_root=tmp_path,
        data_dir=data_dir,
        projects_dir=projects_dir,
        skills_dir=skills_dir,
        sources_dir=sources_dir,
        materials_dir=materials_dir,
        sqlite_path=sqlite_path,
        cache_db_path=cache_db_path,
        jieba_custom_dict_path=jieba_dict,
    )
    cfg.ensure_directories()
    # 初始化空库
    client = DatabaseClient(sqlite_path)
    client.init_db()
    from fxi.storage.sqlite_client import ensure_work
    with client.transaction() as cur:
        for wid in ["work_a", "work_b", "work_loop", "work_rebuild"]:
            ensure_work(cur, wid)
    return cfg
