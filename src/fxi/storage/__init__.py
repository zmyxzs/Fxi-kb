"""
fxi.storage - 存储引擎与纯文本单真理源底座
"""

from .backup import BackupManager
from .rebuild import Rebuilder, RebuildReport
from .sqlite_client import DatabaseClient
from .text_io import (
    calculate_content_hash,
    read_markdown_frontmatter,
    write_markdown_frontmatter,
)

__all__ = [
    "DatabaseClient",
    "Rebuilder",
    "RebuildReport",
    "BackupManager",
    "calculate_content_hash",
    "read_markdown_frontmatter",
    "write_markdown_frontmatter",
]
