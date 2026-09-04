"""
fxi.index_retrieval - 检索与分词模块
"""

from .jieba_fts import ChineseFTS
from .project_lexicon import LexiconManager

__all__ = [
    "ChineseFTS",
    "LexiconManager",
]
