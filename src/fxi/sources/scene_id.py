"""来源场景的稳定标识生成器。"""

from typing import Optional

from fxi.core.canonical import sha256_hex
from fxi.core.identifiers import validate_segment


def generate_scene_uuid(
    source_id: str,
    chapter_num: int,
    scene_seq: int,
    semantic_slug: Optional[str] = None,
    *,
    content: Optional[str] = None,
    content_anchor: Optional[str] = None,
) -> str:
    """根据来源和内容锚点生成不依赖章节顺序的场景标识。

    ``chapter_num`` 与 ``scene_seq`` 保留在公开签名中，供旧调用方继续传入，
    但不会参与新标识的计算。切片器应传入 ``content``；外部已经有稳定语义
    锚点时可以传入 ``content_anchor``。没有内容锚点的旧调用只能退回到语义
    slug（或旧参数组合），因此无法保证重复场景的消歧，版本化导入不应依赖
    这种退回路径。
    """
    validated_source_id = validate_segment(source_id, "source_id")
    if content is not None and content_anchor is not None:
        raise ValueError("content 与 content_anchor 只能传入一个")

    anchor = content_anchor if content_anchor is not None else content
    if anchor is None:
        anchor = semantic_slug if semantic_slug else f"legacy:{chapter_num}:{scene_seq}"
    if not isinstance(anchor, str):
        raise ValueError("场景内容锚点必须是字符串")
    if not anchor.strip():
        anchor = semantic_slug or "scene"

    digest = sha256_hex(
        {
            "source_id": validated_source_id,
            "content_anchor": anchor,
        }
    )[:16]

    slug = semantic_slug.strip().lower() if semantic_slug else "scene"
    safe_slug = "".join(c if c.isalnum() or c in {"_", "-"} else "_" for c in slug)
    safe_slug = safe_slug[:64] or "scene"
    return f"sc_{digest}_{safe_slug}"
