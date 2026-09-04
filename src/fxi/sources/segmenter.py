"""
fxi.sources.segmenter - 原始小说文本场景智能切片
"""

import re
from dataclasses import dataclass
from typing import List

from fxi.core.constants import SCENE_CHUNK_MAX_CHARS, SCENE_CHUNK_MIN_CHARS
from fxi.sources.scene_id import generate_scene_uuid


@dataclass
class SceneChunk:
    scene_uuid: str
    source_id: str
    chapter_index: int
    scene_seq: int
    content: str
    char_count: int


class TextSegmenter:
    """文本场景切片引擎"""

    def __init__(self, min_chars: int = SCENE_CHUNK_MIN_CHARS, max_chars: int = SCENE_CHUNK_MAX_CHARS):
        self.min_chars = min_chars
        self.max_chars = max_chars

    def split_into_scenes(self, text: str, source_id: str, chapter_index: int) -> List[SceneChunk]:
        """
        按段落或逻辑分割点将章节文本切分为 512~1024 字符的场景切片
        """
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks: List[SceneChunk] = []

        current_buf: List[str] = []
        current_len = 0
        scene_seq = 1

        for p in paragraphs:
            p_len = len(p)
            if current_len + p_len > self.max_chars and current_len >= self.min_chars:
                merged_content = "\n\n".join(current_buf)
                chunks.append(SceneChunk(
                    scene_uuid=generate_scene_uuid(source_id, chapter_index, scene_seq),
                    source_id=source_id,
                    chapter_index=chapter_index,
                    scene_seq=scene_seq,
                    content=merged_content,
                    char_count=len(merged_content),
                ))
                scene_seq += 1
                current_buf = [p]
                current_len = p_len
            else:
                current_buf.append(p)
                current_len += p_len

        if current_buf:
            merged_content = "\n\n".join(current_buf)
            chunks.append(SceneChunk(
                scene_uuid=generate_scene_uuid(source_id, chapter_index, scene_seq),
                source_id=source_id,
                chapter_index=chapter_index,
                scene_seq=scene_seq,
                content=merged_content,
                char_count=len(merged_content),
            ))

        return chunks
