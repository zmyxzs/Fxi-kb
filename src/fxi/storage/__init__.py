"""
fxi.storage - 存储引擎与纯文本单真理源底座
"""

from .backup import BackupManager
from .sqlite_client import DatabaseClient
from .text_io import (
    calculate_content_hash,
    read_markdown_frontmatter,
    write_markdown_frontmatter,
)

__all__ = [
    "DatabaseClient",
    "RebuildReport",
    "RebuildService",
    "Rebuilder",
    "BackupManager",
    "calculate_content_hash",
    "read_markdown_frontmatter",
    "write_markdown_frontmatter",
]


def __getattr__(name: str):
    """Load rebuild lazily so storage and index modules can import independently."""
    if name in {"Rebuilder", "RebuildReport", "RebuildService"}:
        from .rebuild import Rebuilder, RebuildReport

        from .rebuild import RebuildService

        return {
            "Rebuilder": Rebuilder,
            "RebuildReport": RebuildReport,
            "RebuildService": RebuildService,
        }[name]
    raise AttributeError(name)
