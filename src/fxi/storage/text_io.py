"""
fxi.storage.text_io - 纯文本 YAML Frontmatter 原子读写与冲突守卫
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple
import yaml

from fxi.core.exceptions import CorruptedDataError, FileLockedError
from fxi.core.canonical import sha256_hex


def calculate_content_hash(text: str) -> str:
    """计算文本内容的 SHA-256 哈希值"""
    return sha256_hex(text)


def read_markdown_frontmatter(file_path: Path) -> Tuple[dict[str, Any], str]:
    """
    读取带 YAML Frontmatter 的 Markdown 文件
    返回: (metadata_dict, body_text)
    """
    if not file_path.is_file():
        raise CorruptedDataError(f"文件不存在或不是普通文件: {file_path}")

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        raise CorruptedDataError(f"读取文件失败 {file_path}: {e}")

    if not content.startswith("---"):
        return {}, content

    # 寻找结束分隔符
    end_idx = content.find("\n---", 3)
    if end_idx == -1:
        raise CorruptedDataError(f"YAML Frontmatter 缺少结束分隔符: {file_path}")

    yaml_block = content[3:end_idx].strip()
    body_text = content[end_idx + 4:].lstrip("\r\n")

    try:
        metadata = yaml.safe_load(yaml_block)
    except Exception as e:
        raise CorruptedDataError(f"YAML Frontmatter 解析错误 {file_path}: {e}")

    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise CorruptedDataError(f"YAML Frontmatter 必须是对象: {file_path}")

    return metadata, body_text


def write_markdown_frontmatter(
    file_path: Path,
    metadata: dict[str, Any],
    body: str,
    guard_hash: Optional[str] = None
) -> str:
    """
    原子写入带 YAML Frontmatter 的 Markdown 文件。
    若传入 guard_hash，且目标文件存在，则校验当前哈希是否匹配，不匹配则抛出 FileLockedError。
    返回: 写入内容的 SHA-256 哈希值。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if guard_hash is not None and file_path.is_file():
        current_content = file_path.read_text(encoding="utf-8")
        current_hash = calculate_content_hash(current_content)
        if current_hash != guard_hash:
            raise FileLockedError(
                f"文件已被外部程序或并行会话修改，拒绝覆盖: {file_path} "
                f"(预期哈希: {guard_hash}, 实际哈希: {current_hash})"
            )

    yaml_str = yaml.dump(metadata, allow_unicode=True, sort_keys=False).strip()
    full_content = f"---\n{yaml_str}\n---\n\n{body.lstrip()}"
    new_hash = calculate_content_hash(full_content)

    # 原子写入临时文件后重命名替换
    temp_dir = file_path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        tf.write(full_content)
        temp_name = tf.name

    try:
        os.replace(temp_name, file_path)
    except Exception as e:
        cleanup_error: Optional[OSError] = None
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        message = f"原子覆写文件失败 {file_path}: {e}"
        if cleanup_error is not None:
            message += f"；临时文件清理失败: {cleanup_error}"
        raise FileLockedError(message) from e

    # 立即回读自检
    verified_content = file_path.read_text(encoding="utf-8")
    if calculate_content_hash(verified_content) != new_hash:
        raise FileLockedError(f"文件落盘校验失败，实际读取内容与预期哈希不一致: {file_path}")

    return new_hash
