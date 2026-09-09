"""
fxi.core.config - 配置加载与工作区路径解析
"""

import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field

from .constants import DEFAULT_CONTEXT_BUDGET, MAX_CONTEXT_BUDGET, VRAM_SAFE_LIMIT_MB
from .exceptions import ValidationError


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class ContextConfig(BaseModel):
    default_budget: int = DEFAULT_CONTEXT_BUDGET
    max_budget: int = MAX_CONTEXT_BUDGET
    vram_safe_limit_mb: int = VRAM_SAFE_LIMIT_MB


class FxiConfig(BaseModel):
    workspace_root: Path
    data_dir: Path
    projects_dir: Path
    skills_dir: Path
    sources_dir: Path
    materials_dir: Path
    sqlite_path: Path
    cache_db_path: Path
    jieba_custom_dict_path: Path
    server: ServerConfig = Field(default_factory=ServerConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)

    def ensure_directories(self) -> None:
        """确保所有关键运行时目录存在"""
        for p in [self.data_dir, self.projects_dir, self.skills_dir, self.sources_dir, self.materials_dir]:
            p.mkdir(parents=True, exist_ok=True)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.jieba_custom_dict_path.parent.mkdir(parents=True, exist_ok=True)


def _resolve_workspace_path(workspace_root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} 必须是非空相对路径")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValidationError(f"{label} 不允许使用绝对路径")
    resolved = (workspace_root / candidate).resolve()
    root = workspace_root.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValidationError(f"{label} 越出 workspace_root")
    return resolved


def load_config(
    config_path: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
    *,
    initialize: bool = False,
) -> FxiConfig:
    """加载配置；默认只解析，不创建目录或数据库。"""
    if workspace_root is None:
        env_root = os.getenv("FXI_WORKSPACE_ROOT")
        if env_root:
            workspace_root = Path(env_root).resolve()
        else:
            # 默认当前工作目录或代码根目录 (d:\Code\Fxi)
            workspace_root = Path(__file__).resolve().parent.parent.parent.parent

    if config_path is None:
        config_path = workspace_root / "config" / "config.yaml"

    cfg_dict = {}
    if config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg_dict = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValidationError(f"无法解析配置文件 {config_path}: {e}")

    storage_cfg = cfg_dict.get("storage", {})
    server_cfg = cfg_dict.get("server", {})
    context_cfg = cfg_dict.get("context", {})

    data_dir = _resolve_workspace_path(workspace_root, storage_cfg.get("data_dir", "data"), "data_dir")
    projects_dir = _resolve_workspace_path(workspace_root, storage_cfg.get("projects_dir", "projects"), "projects_dir")
    skills_dir = _resolve_workspace_path(workspace_root, storage_cfg.get("skills_dir", "skills"), "skills_dir")
    sources_dir = _resolve_workspace_path(workspace_root, storage_cfg.get("sources_dir", "sources"), "sources_dir")
    materials_dir = _resolve_workspace_path(workspace_root, storage_cfg.get("materials_dir", "materials"), "materials_dir")

    sqlite_path = _resolve_workspace_path(workspace_root, storage_cfg.get("sqlite_path", "data/manifest.sqlite"), "sqlite_path")
    cache_db_path = _resolve_workspace_path(workspace_root, storage_cfg.get("cache_db_path", "data/cache.sqlite"), "cache_db_path")
    jieba_custom_dict_path = _resolve_workspace_path(
        workspace_root,
        storage_cfg.get("jieba_custom_dict_path", "data/project_lexicon.txt"),
        "jieba_custom_dict_path",
    )

    config = FxiConfig(
        workspace_root=workspace_root,
        data_dir=data_dir,
        projects_dir=projects_dir,
        skills_dir=skills_dir,
        sources_dir=sources_dir,
        materials_dir=materials_dir,
        sqlite_path=sqlite_path,
        cache_db_path=cache_db_path,
        jieba_custom_dict_path=jieba_custom_dict_path,
        server=ServerConfig(**server_cfg),
        context=ContextConfig(**context_cfg),
    )
    if initialize:
        config.ensure_directories()
    return config
