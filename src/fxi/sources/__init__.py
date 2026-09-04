"""
fxi.sources - 原始小说参考资料底本导入与切片
"""

from .importer import SourceImporter, SourceManifest
from .scene_id import generate_scene_uuid
from .segmenter import SceneChunk, TextSegmenter

__all__ = [
    "SourceImporter",
    "SourceManifest",
    "generate_scene_uuid",
    "SceneChunk",
    "TextSegmenter",
]
