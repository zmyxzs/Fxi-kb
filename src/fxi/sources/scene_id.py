"""
fxi.sources.scene_id - 语义场景确定性 UUID 生成器
"""

import hashlib
from typing import Optional


def generate_scene_uuid(
    source_id: str,
    chapter_num: int,
    scene_seq: int,
    semantic_slug: Optional[str] = None
) -> str:
    """
    生成确定性且解耦物理章节重排的场景 UUID
    格式: sc_<hash_8>_<slug>_<seq:02d>
    """
    raw_seed = f"{source_id}:{chapter_num}:{scene_seq}"
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest()[:8]

    slug = semantic_slug.strip().lower() if semantic_slug else "scene"
    # 清理非法字符
    safe_slug = "".join([c if c.isalnum() or c == "_" else "_" for c in slug])
    return f"sc_{digest}_{safe_slug}_{scene_seq:02d}"
