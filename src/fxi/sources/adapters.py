"""Pull-only source adapters for the v3 knowledge boundary.

Adapters return immutable-input records.  They do not publish facts or write
to the knowledge store; :mod:`snapshot_service` owns publication.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.core.identifiers import validate_segment
from fxi.storage.versioned_store import (
    NORMALIZATION_VERSION,
    SourceDocumentInput,
    SourceEncodingError,
    decode_source_bytes,
)


class SourceAdapterError(ValidationError):
    """An adapter could not safely fetch a source revision."""

    def __init__(self, message: str, *, code: str = "INVALID_SCHEMA") -> None:
        super().__init__(message)
        self.code = code


class SourcePathError(SourceAdapterError):
    """A resource escaped its configured root or used a reparse point."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_SCOPE")


@dataclass(frozen=True, slots=True)
class SourceResource:
    """A source location supplied to an adapter.

    ``path`` may be absolute or relative to the adapter root.  The optional
    identifiers are metadata only; the adapter never infers business facts.
    """

    path: Path | str
    source_id: str | None = None
    work_id: str | None = None
    document_id: str | None = None
    resource_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceCapability:
    adapter_id: str
    adapter_version: str
    read_only: bool = True
    pull: bool = True
    media_types: tuple[str, ...] = ("text/plain", "text/markdown")
    max_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceRevision:
    """One fetched revision, suitable for a later immutable snapshot."""

    source_id: str
    source_version: str
    documents: tuple[SourceDocumentInput, ...]
    content_hash: str
    normalization_version: str = NORMALIZATION_VERSION
    work_id: str | None = None
    resource_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    adapter_id: str
    adapter_version: str

    def fetch(
        self, resource: SourceResource, *, cursor: str | None = None
    ) -> SourceRevision: ...

    def capabilities(self) -> SourceCapability: ...


def _is_reparse_point(path: Path) -> bool:
    """Return whether ``path`` is a symlink or Windows reparse point."""

    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        return bool(path.stat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def _reject_reparse_components(root: Path, target: Path) -> None:
    current = root
    if _is_reparse_point(current):
        raise SourcePathError(f"来源根目录不能是 symlink/junction: {root}")
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise SourcePathError(f"来源路径越界: {target}") from exc
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise SourcePathError(f"来源路径不能经过 symlink/junction: {current}")


def _resource_value(resource: SourceResource | Path | str) -> SourceResource:
    if isinstance(resource, SourceResource):
        return resource
    return SourceResource(path=resource)


class LocalTextAdapter:
    """Strict, read-only TXT/Markdown adapter rooted at a configured folder."""

    adapter_id = "local-text"
    adapter_version = "3.0.0"

    def __init__(
        self,
        root: Path | str,
        *,
        source_id: str | None = None,
        max_bytes: int = 8 * 1024 * 1024,
        max_chars: int = 2_000_000,
    ) -> None:
        self.root = Path(root)
        self.source_id = source_id
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")
        self.max_bytes = max_bytes
        self.max_chars = max_chars

    def capabilities(self) -> SourceCapability:
        return SourceCapability(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            max_bytes=self.max_bytes,
        )

    def _resolve_path(self, resource: SourceResource) -> Path:
        root_input = self.root
        if not root_input.exists() or not root_input.is_dir():
            raise SourcePathError(f"来源根目录不存在或不是目录: {root_input}")
        root = root_input.absolute()
        if _is_reparse_point(root):
            raise SourcePathError(f"来源根目录不能是 symlink/junction: {root}")
        supplied = Path(resource.path)
        if any(part == ".." for part in supplied.parts):
            raise SourcePathError(f"来源路径禁止包含 ..: {supplied}")
        candidate = supplied if supplied.is_absolute() else root / supplied
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
        except (OSError, RuntimeError, ValueError) as exc:
            raise SourcePathError(f"来源路径越界或不可解析: {candidate}") from exc
        _reject_reparse_components(root.resolve(strict=True), resolved)
        if not resolved.is_file():
            raise SourcePathError(f"来源路径不是普通文件: {resolved}")
        if resolved.suffix.lower() not in {".txt", ".md", ".markdown"}:
            raise SourceAdapterError("本地文本适配器只支持 .txt、.md、.markdown")
        return resolved

    def fetch(
        self,
        resource: SourceResource | Path | str,
        *,
        cursor: str | None = None,
    ) -> SourceRevision:
        del cursor  # Local files have no remote cursor; callers compare versions.
        requested = _resource_value(resource)
        path = self._resolve_path(requested)
        resolved_source_id = requested.source_id or self.source_id
        if resolved_source_id is None:
            raise SourceAdapterError("SourceResource 或 LocalTextAdapter 必须提供 source_id")
        validate_segment(resolved_source_id, "source_id")
        document_id = requested.document_id or "document-1"
        validate_segment(document_id, "document_id")
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise SourceAdapterError(f"读取来源文件失败: {path}") from exc
        if len(raw_bytes) > self.max_bytes:
            raise SourceAdapterError(
                f"来源文件超过大小上限: {len(raw_bytes)} > {self.max_bytes}"
            )
        try:
            text, encoding = decode_source_bytes(raw_bytes, path)
        except SourceEncodingError:
            raise
        if len(text) > self.max_chars:
            raise SourceAdapterError(
                f"来源文本超过字符上限: {len(text)} > {self.max_chars}"
            )
        content_hash = sha256_hex(text)
        document = SourceDocumentInput(
            document_id=document_id,
            chapter_index=1,
            raw_bytes=raw_bytes,
            text=text,
            relative_path=path.name,
            encoding=encoding,
            expected_content_hash=content_hash,
        )
        return SourceRevision(
            source_id=resolved_source_id,
            source_version=content_hash,
            documents=(document,),
            content_hash=content_hash,
            normalization_version=NORMALIZATION_VERSION,
            work_id=requested.work_id,
            resource_id=requested.resource_id or path.name,
            metadata=dict(requested.metadata),
        )


# Descriptive aliases keep the public adapter boundary discoverable without
# introducing separate implementations.
TextFileSourceAdapter = LocalTextAdapter
LocalFileSourceAdapter = LocalTextAdapter


__all__ = [
    "LocalTextAdapter",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceCapability",
    "SourceEncodingError",
    "SourcePathError",
    "SourceResource",
    "SourceRevision",
    "TextFileSourceAdapter",
    "LocalFileSourceAdapter",
]
