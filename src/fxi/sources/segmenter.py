"""原始来源文本的确定性场景切片。"""

import re
from dataclasses import dataclass
from typing import List

from fxi.core.constants import SCENE_CHUNK_MAX_CHARS, SCENE_CHUNK_MIN_CHARS
from fxi.core.identifiers import validate_segment
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
        if min_chars < 0:
            raise ValueError("min_chars 不能小于零")
        if max_chars <= 0:
            raise ValueError("max_chars 必须大于零")
        if min_chars > max_chars:
            raise ValueError("min_chars 不能大于 max_chars")
        self.min_chars = min_chars
        self.max_chars = max_chars

    def split_into_scenes(self, text: str, source_id: str, chapter_index: int) -> List[SceneChunk]:
        """按段落边界聚合切片，并始终保证每片不超过 ``max_chars``。

        长段落会先按最大长度拆成多个内容片段，再继续处理后续段落。场景
        标识使用最终正文作为内容锚点，因此不会因为章节号或片段序号变化而
        改变；``scene_seq`` 仅保留给下游展示和兼容旧数据。
        """
        validate_segment(source_id, "source_id")
        if not isinstance(text, str):
            raise TypeError("text 必须是字符串")

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks: List[SceneChunk] = []

        current_buf: List[str] = []
        current_len = 0
        scene_seq = 1

        def flush_current() -> None:
            nonlocal current_buf, current_len, scene_seq
            if not current_buf:
                return
            merged_content = "\n\n".join(current_buf)
            chunks.append(SceneChunk(
                scene_uuid=generate_scene_uuid(
                    source_id,
                    chapter_index,
                    scene_seq,
                    content=merged_content,
                ),
                source_id=source_id,
                chapter_index=chapter_index,
                scene_seq=scene_seq,
                content=merged_content,
                char_count=len(merged_content),
            ))
            scene_seq += 1
            current_buf = []
            current_len = 0

        for paragraph in paragraphs:
            remaining = paragraph
            while len(remaining) > self.max_chars:
                flush_current()
                # 直接切分超长段落，避免单段绕过最大长度限制。
                piece = remaining[: self.max_chars]
                remaining = remaining[self.max_chars :]
                current_buf = [piece]
                current_len = len(piece)
                flush_current()

            if not remaining:
                continue
            extra_len = len(remaining) + (2 if current_buf else 0)
            if current_buf and current_len + extra_len > self.max_chars:
                flush_current()
            current_buf.append(remaining)
            current_len += len(remaining) + (2 if len(current_buf) > 1 else 0)

        flush_current()

        return chunks
