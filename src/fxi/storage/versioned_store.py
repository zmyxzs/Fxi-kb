"""
不可变来源对象存储。

对象布局：

    <sources_root>/objects/<source_id>/<version>/<document_id>/
        raw.bin
        normalized.txt
        metadata.yaml
    <sources_root>/objects/<source_id>/<version>/snapshot.yaml

版本目录一经创建不得覆写。读取来源正文只访问上述对象目录，不回读导入时的
原始路径，因此旧版本不会随着当前来源文件变化而失效。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

import yaml

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import CorruptedDataError, NotFoundError, StorageError, ValidationError
from fxi.core.identifiers import validate_segment
from fxi.storage.text_io import calculate_content_hash


NORMALIZATION_VERSION = "newline-bom-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset(
    {"schema_version", "source_id", "version", "manifest_hash", "normalization_version", "documents"}
)


class InvalidObjectPathError(ValidationError):
    """来源对象的 ID 或相对路径不安全。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "INVALID_OBJECT_PATH"


class SourceEncodingError(CorruptedDataError):
    """来源文件无法用支持的严格编码解码。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "SOURCE_ENCODING_ERROR"


class HashMismatchError(CorruptedDataError):
    """原始 bytes 或规范文本哈希与声明不一致。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "SOURCE_HASH_MISMATCH"


class ImmutableObjectError(StorageError):
    """试图用不同内容覆写已有不可变来源对象。"""

    def __init__(self, message: str):
        super().__init__(message, code="IMMUTABLE_OBJECT_CONFLICT")


class DuplicateDocumentError(ValidationError):
    """一个快照中出现重复文档或章节。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "DUPLICATE_DOCUMENT"


class MissingChapterError(ValidationError):
    """章节序号不连续或来源目录为空。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "MISSING_CHAPTER"


def validate_object_segment(value: str, label: str) -> str:
    """校验会进入对象路径的单段标识，拒绝路径穿越和隐式目录。"""

    try:
        return validate_segment(value, label)
    except ValidationError as exc:
        raise InvalidObjectPathError(f"非法 {label}: {value!r}") from exc


def validate_relative_path(value: str, label: str = "relative_path") -> str:
    """校验只作为元数据保存的相对路径，拒绝绝对路径和 ..。"""

    if not isinstance(value, str) or not value.strip():
        raise InvalidObjectPathError(f"非法 {label}: {value!r}")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidObjectPathError(f"非法 {label}: {value!r}")
    return path.as_posix()


def normalize_text(text: str) -> str:
    """统一 BOM 和换行；字符坐标均基于这个规范文本。"""

    if not isinstance(text, str):
        raise SourceEncodingError("来源正文必须是文本")
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def decode_source_bytes(raw_bytes: bytes, source_path: Optional[Path] = None) -> tuple[str, str]:
    """严格解码 UTF-8/GB18030，不使用 errors='replace' 静默损坏正文。"""

    if not isinstance(raw_bytes, bytes):
        raise SourceEncodingError("来源原始内容必须是 bytes")
    try:
        text = raw_bytes.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError as utf8_error:
        try:
            text = raw_bytes.decode("gb18030")
            encoding = "gb18030"
        except UnicodeDecodeError as gb18030_error:
            location = f" {source_path}" if source_path else ""
            raise SourceEncodingError(
                f"来源文件{location}无法严格解码为 UTF-8 或 GB18030"
            ) from gb18030_error
    return normalize_text(text), encoding


def _validate_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CorruptedDataError(f"{label} 必须是小写 SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class SourceDocumentInput:
    """待写入快照的一个文档，raw_bytes 保留原始输入，text 是规范文本。"""

    document_id: str
    chapter_index: int
    raw_bytes: bytes
    text: str
    relative_path: str
    encoding: str = "utf-8"
    expected_raw_sha256: Optional[str] = None
    expected_content_hash: Optional[str] = None

    @classmethod
    def from_file(
        cls,
        file_path: Path,
        *,
        document_id: str,
        chapter_index: int,
        relative_path: str,
    ) -> "SourceDocumentInput":
        path = Path(file_path)
        if not path.is_file():
            raise NotFoundError(f"来源文件不存在或不是普通文件: {path}")
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise StorageError(f"读取来源文件失败: {path}: {exc}") from exc
        text, encoding = decode_source_bytes(raw_bytes, path)
        return cls(
            document_id=document_id,
            chapter_index=chapter_index,
            raw_bytes=raw_bytes,
            text=text,
            relative_path=relative_path,
            encoding=encoding,
        )


@dataclass(frozen=True, slots=True)
class SourceDocumentMetadata:
    document_id: str
    chapter_index: int
    relative_path: str
    raw_sha256: str
    content_hash: str
    char_count: int
    encoding: str
    normalization_version: str = NORMALIZATION_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chapter_index": self.chapter_index,
            "relative_path": self.relative_path,
            "raw_sha256": self.raw_sha256,
            "content_hash": self.content_hash,
            "char_count": self.char_count,
            "encoding": self.encoding,
            "normalization_version": self.normalization_version,
        }


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    version: str
    manifest_hash: str
    documents: tuple[SourceDocumentMetadata, ...]
    normalization_version: str = NORMALIZATION_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, Mapping):
            raise ValidationError("来源快照 metadata 必须是映射")
        # 防止调用方在创建快照后通过原始 dict 修改清单元数据。
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "source_id": self.source_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "normalization_version": self.normalization_version,
            "documents": [document.to_dict() for document in self.documents],
        }
        payload.update(
            {
                key: value
                for key, value in self.metadata.items()
                if key not in _MANIFEST_FIELDS
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class StoredDocument:
    source_id: str
    source_version: str
    document_id: str
    chapter_index: int
    content: str
    document_hash: str
    raw_hash: str
    excerpt_hash: str
    start_char: int
    end_char: int
    encoding: str
    normalization_version: str
    relative_path: str


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return sha256_hex(value)


def _manifest_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """从落盘清单还原创建时参与哈希的不可变字段。"""

    result = {
        "source_id": payload.get("source_id"),
        "normalization_version": payload.get("normalization_version", NORMALIZATION_VERSION),
        "documents": payload.get("documents"),
    }
    result.update({key: value for key, value in payload.items() if key not in _MANIFEST_FIELDS})
    return result


def _yaml_bytes(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(dict(value), allow_unicode=True, sort_keys=False).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: Optional[str] = None
    storage_error: StorageError | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
        temp_name = None
        if path.read_bytes() != data:
            raise StorageError(f"来源对象落盘校验失败: {path}")
    except Exception as exc:
        if isinstance(exc, StorageError):
            storage_error = exc
            raise
        storage_error = StorageError(f"写入来源对象失败: {path}: {exc}")
        raise storage_error from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError as cleanup_error:
                message = f"来源对象临时文件清理失败: {cleanup_error}"
                if storage_error is not None:
                    storage_error.add_note(message)
                else:
                    raise StorageError(message) from cleanup_error


class VersionedStore:
    """只追加的来源对象存储，不依赖 SQLite。"""

    def __init__(self, sources_root: Path):
        self.sources_root = Path(sources_root)
        self.objects_root = self.sources_root / "objects"

    def _snapshot_dir(self, source_id: str, version: str) -> Path:
        validate_object_segment(source_id, "source_id")
        validate_object_segment(version, "version")
        root = self.objects_root.resolve()
        target = (root / source_id / version).resolve()
        if root not in target.parents or target == root:
            raise InvalidObjectPathError("来源对象路径越界")
        return target

    def _document_dir(self, source_id: str, version: str, document_id: str) -> Path:
        validate_object_segment(document_id, "document_id")
        target = self._snapshot_dir(source_id, version) / document_id
        root = self._snapshot_dir(source_id, version)
        if root not in target.parents:
            raise InvalidObjectPathError("文档对象路径越界")
        return target

    @staticmethod
    def _prepare_document(document: SourceDocumentInput) -> tuple[SourceDocumentMetadata, str, bytes]:
        if not isinstance(document, SourceDocumentInput):
            raise ValidationError("快照文档必须是 SourceDocumentInput")
        validate_object_segment(document.document_id, "document_id")
        if isinstance(document.chapter_index, bool) or not isinstance(document.chapter_index, int):
            raise ValidationError(f"非法 chapter_index: {document.chapter_index!r}")
        if document.chapter_index < 1:
            raise ValidationError(f"chapter_index 必须从 1 开始: {document.chapter_index}")
        relative_path = validate_relative_path(document.relative_path)
        if not isinstance(document.raw_bytes, bytes):
            raise SourceEncodingError(f"文档 {document.document_id} 的原始内容不是 bytes")
        if not isinstance(document.encoding, str) or not document.encoding.strip():
            raise SourceEncodingError(f"文档 {document.document_id} 缺少有效编码")
        decoded, detected_encoding = decode_source_bytes(document.raw_bytes)
        normalized = normalize_text(document.text)
        if decoded != normalized:
            raise SourceEncodingError(
                f"文档 {document.document_id} 的 raw bytes 与规范正文不一致"
            )
        if document.encoding.lower().replace("_", "-") != detected_encoding:
            raise SourceEncodingError(
                f"文档 {document.document_id} 编码声明不一致: "
                f"declared={document.encoding!r}, detected={detected_encoding!r}"
            )
        raw_hash = sha256_hex(document.raw_bytes)
        content_hash = calculate_content_hash(normalized)
        if document.expected_raw_sha256 and document.expected_raw_sha256 != raw_hash:
            raise HashMismatchError(
                f"文档 {document.document_id} 原始 bytes 哈希不一致: "
                f"expected={document.expected_raw_sha256}, actual={raw_hash}"
            )
        if document.expected_content_hash and document.expected_content_hash != content_hash:
            raise HashMismatchError(
                f"文档 {document.document_id} 规范文本哈希不一致: "
                f"expected={document.expected_content_hash}, actual={content_hash}"
            )
        metadata = SourceDocumentMetadata(
            document_id=document.document_id,
            chapter_index=document.chapter_index,
            relative_path=relative_path,
            raw_sha256=raw_hash,
            content_hash=content_hash,
            char_count=len(normalized),
            encoding=document.encoding,
        )
        return metadata, normalized, document.raw_bytes

    def _read_manifest(self, source_id: str, version: str) -> dict[str, Any]:
        snapshot_dir = self._snapshot_dir(source_id, version)
        manifest_path = snapshot_dir / "snapshot.yaml"
        if not manifest_path.is_file():
            raise NotFoundError(f"未找到来源快照: {source_id}/{version}")
        try:
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise CorruptedDataError(f"来源快照清单不可读: {manifest_path}") from exc
        if not isinstance(payload, dict):
            raise CorruptedDataError(f"来源快照清单格式无效: {manifest_path}")
        if payload.get("source_id") != source_id or payload.get("version") != version:
            raise CorruptedDataError(f"来源快照清单身份不一致: {manifest_path}")
        return payload

    @staticmethod
    def _snapshot_from_payload(payload: Mapping[str, Any]) -> SourceSnapshot:
        if payload.get("schema_version", 1) != 1:
            raise CorruptedDataError("来源快照清单 schema_version 不受支持")
        try:
            source_id = str(payload["source_id"])
            version = str(payload["version"])
            manifest_hash = _validate_sha256(payload["manifest_hash"], "manifest_hash")
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptedDataError("来源快照清单身份或哈希字段无效") from exc
        validate_object_segment(source_id, "source_id")
        validate_object_segment(version, "version")
        if _canonical_hash(_manifest_payload(payload)) != manifest_hash:
            raise HashMismatchError("来源快照清单 manifest_hash 不一致")
        raw_documents = payload.get("documents")
        if not isinstance(raw_documents, list) or not raw_documents:
            raise CorruptedDataError("来源快照缺少文档清单")
        documents: list[SourceDocumentMetadata] = []
        seen_document_ids: set[str] = set()
        seen_chapters: set[int] = set()
        for raw in raw_documents:
            if not isinstance(raw, dict):
                raise CorruptedDataError("来源快照文档元数据格式无效")
            try:
                document = SourceDocumentMetadata(**raw)
            except (TypeError, ValueError) as exc:
                raise CorruptedDataError("来源快照文档元数据字段无效") from exc
            validate_object_segment(document.document_id, "document_id")
            if isinstance(document.chapter_index, bool) or not isinstance(document.chapter_index, int):
                raise CorruptedDataError("来源快照 chapter_index 无效")
            if document.chapter_index < 1:
                raise CorruptedDataError("来源快照 chapter_index 必须为正整数")
            validate_relative_path(document.relative_path)
            _validate_sha256(document.raw_sha256, "raw_sha256")
            _validate_sha256(document.content_hash, "content_hash")
            if isinstance(document.char_count, bool) or not isinstance(document.char_count, int) or document.char_count < 0:
                raise CorruptedDataError("来源快照 char_count 无效")
            if not isinstance(document.encoding, str) or not document.encoding.strip():
                raise CorruptedDataError("来源快照 encoding 无效")
            if not isinstance(document.normalization_version, str) or not document.normalization_version.strip():
                raise CorruptedDataError("来源快照 normalization_version 无效")
            if document.document_id in seen_document_ids or document.chapter_index in seen_chapters:
                raise CorruptedDataError("来源快照包含重复文档或章节")
            seen_document_ids.add(document.document_id)
            seen_chapters.add(document.chapter_index)
            documents.append(document)
        ordered_chapters = sorted(seen_chapters)
        if ordered_chapters != list(range(ordered_chapters[0], ordered_chapters[0] + len(ordered_chapters))):
            raise CorruptedDataError("来源快照章节序号不连续")
        normalization_version = payload.get("normalization_version", NORMALIZATION_VERSION)
        if not isinstance(normalization_version, str) or not normalization_version.strip():
            raise CorruptedDataError("来源快照 normalization_version 无效")
        metadata = {key: value for key, value in payload.items() if key not in _MANIFEST_FIELDS}
        return SourceSnapshot(
            source_id=source_id,
            version=version,
            manifest_hash=manifest_hash,
            documents=tuple(documents),
            normalization_version=normalization_version,
            metadata=metadata,
        )

    def read_snapshot(self, source_id: str, version: str) -> SourceSnapshot:
        return self._snapshot_from_payload(self._read_manifest(source_id, version))

    def create_snapshot(
        self,
        source_id: str,
        documents: Iterable[SourceDocumentInput],
        *,
        version: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> SourceSnapshot:
        validate_object_segment(source_id, "source_id")
        prepared = [self._prepare_document(document) for document in documents]
        if not prepared:
            raise MissingChapterError(f"来源 {source_id} 没有可保存文档")

        seen_document_ids: set[str] = set()
        seen_chapters: set[int] = set()
        for document, _, _ in prepared:
            if document.document_id in seen_document_ids:
                raise DuplicateDocumentError(f"重复 document_id: {document.document_id}")
            if document.chapter_index in seen_chapters:
                raise DuplicateDocumentError(f"重复 chapter_index: {document.chapter_index}")
            seen_document_ids.add(document.document_id)
            seen_chapters.add(document.chapter_index)
        start_chapter = min(seen_chapters)
        if start_chapter < 1:
            raise MissingChapterError(f"章节序号必须为正整数: {start_chapter}")
        expected_chapters = set(range(start_chapter, start_chapter + len(prepared)))
        if seen_chapters != expected_chapters:
            missing = sorted(expected_chapters - seen_chapters)
            unexpected = sorted(seen_chapters - expected_chapters)
            raise MissingChapterError(
                f"章节序号不连续: missing={missing}, unexpected={unexpected}"
            )

        prepared.sort(key=lambda item: (item[0].chapter_index, item[0].document_id))
        document_metadata = [item[0] for item in prepared]
        manifest_payload: dict[str, Any] = {
            "source_id": source_id,
            "normalization_version": NORMALIZATION_VERSION,
            "documents": [document.to_dict() for document in document_metadata],
        }
        snapshot_metadata: dict[str, Any] = {}
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise ValidationError("来源快照 metadata 必须是映射")
            # 额外元数据只能作为清单信息，不能覆盖身份、版本或文档清单。
            for key, value in metadata.items():
                if not isinstance(key, str):
                    raise ValidationError("来源快照 metadata 的键必须是字符串")
                if key not in _MANIFEST_FIELDS:
                    manifest_payload[key] = value
                    snapshot_metadata[key] = value
        manifest_hash = _canonical_hash(manifest_payload)
        resolved_version = version or manifest_hash
        validate_object_segment(resolved_version, "version")
        snapshot_dir = self._snapshot_dir(source_id, resolved_version)
        snapshot = SourceSnapshot(
            source_id=source_id,
            version=resolved_version,
            manifest_hash=manifest_hash,
            documents=tuple(document_metadata),
            metadata=snapshot_metadata,
        )

        if snapshot_dir.exists():
            existing = self.read_snapshot(source_id, resolved_version)
            if existing.manifest_hash != manifest_hash or existing.documents != snapshot.documents:
                raise ImmutableObjectError(
                    f"不可变来源版本内容冲突: {source_id}/{resolved_version}"
                )
            return existing

        snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            snapshot_dir.mkdir(exist_ok=False)
        except FileExistsError:
            existing = self.read_snapshot(source_id, resolved_version)
            if (
                existing.manifest_hash != manifest_hash
                or existing.documents != snapshot.documents
                or dict(existing.metadata) != snapshot_metadata
            ):
                raise ImmutableObjectError(
                    f"不可变来源版本内容冲突: {source_id}/{resolved_version}"
                )
            return existing

        operation_error: Optional[BaseException] = None
        try:
            for document, normalized, raw_bytes in prepared:
                document_dir = snapshot_dir / document.document_id
                document_dir.mkdir()
                _atomic_write(document_dir / "raw.bin", raw_bytes)
                _atomic_write(document_dir / "normalized.txt", normalized.encode("utf-8"))
                _atomic_write(document_dir / "metadata.yaml", _yaml_bytes(document.to_dict()))
            # 清单最后写入；在此之前该版本不会被视为可读快照。
            _atomic_write(snapshot_dir / "snapshot.yaml", _yaml_bytes(snapshot.to_dict()))
            return snapshot
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            if operation_error is not None and snapshot_dir.exists():
                try:
                    shutil.rmtree(snapshot_dir)
                except OSError as cleanup_error:
                    operation_error.add_note(
                        f"清理不完整来源快照失败: {snapshot_dir}: {cleanup_error}"
                    )

    def read_document(
        self,
        source_id: str,
        source_version: str,
        document_id: str,
        *,
        start_char: int = 0,
        end_char: Optional[int] = None,
    ) -> StoredDocument:
        snapshot = self.read_snapshot(source_id, source_version)
        metadata = next((item for item in snapshot.documents if item.document_id == document_id), None)
        if metadata is None:
            raise NotFoundError(
                f"未找到快照文档: {source_id}/{source_version}/{document_id}"
            )
        document_dir = self._document_dir(source_id, source_version, document_id)
        raw_path = document_dir / "raw.bin"
        normalized_path = document_dir / "normalized.txt"
        metadata_path = document_dir / "metadata.yaml"
        if not raw_path.is_file() or not normalized_path.is_file() or not metadata_path.is_file():
            raise CorruptedDataError(f"来源对象不完整: {document_dir}")
        try:
            raw_bytes = raw_path.read_bytes()
            content = normalized_path.read_text(encoding="utf-8")
            stored_metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise CorruptedDataError(f"来源对象不可读: {document_dir}") from exc
        if stored_metadata != metadata.to_dict():
            raise CorruptedDataError(f"来源对象元数据与快照清单不一致: {document_dir}")
        actual_raw_hash = sha256_hex(raw_bytes)
        actual_content_hash = calculate_content_hash(content)
        if actual_raw_hash != metadata.raw_sha256:
            raise HashMismatchError(f"来源对象 raw.bin 哈希不一致: {raw_path}")
        if actual_content_hash != metadata.content_hash or len(content) != metadata.char_count:
            raise HashMismatchError(f"来源对象 normalized.txt 哈希或长度不一致: {normalized_path}")
        if isinstance(start_char, bool) or not isinstance(start_char, int):
            raise ValidationError(f"start_char 必须是整数: {start_char!r}")
        if end_char is not None and (isinstance(end_char, bool) or not isinstance(end_char, int)):
            raise ValidationError(f"end_char 必须是整数或 None: {end_char!r}")
        resolved_end = len(content) if end_char is None else end_char
        if start_char < 0 or resolved_end < start_char or resolved_end > len(content):
            raise ValidationError(
                f"证据区间越界: [{start_char}, {resolved_end})，正文长度={len(content)}"
            )
        excerpt = content[start_char:resolved_end]
        return StoredDocument(
            source_id=source_id,
            source_version=source_version,
            document_id=document_id,
            chapter_index=metadata.chapter_index,
            content=excerpt,
            document_hash=metadata.content_hash,
            raw_hash=metadata.raw_sha256,
            excerpt_hash=calculate_content_hash(excerpt),
            start_char=start_char,
            end_char=resolved_end,
            encoding=metadata.encoding,
            normalization_version=metadata.normalization_version,
            relative_path=metadata.relative_path,
        )


# 便于调用方按语义导入；VersionedStore 是权威实现。
VersionedSourceStore = VersionedStore


__all__ = [
    "NORMALIZATION_VERSION",
    "SourceDocumentInput",
    "SourceDocumentMetadata",
    "SourceSnapshot",
    "StoredDocument",
    "VersionedStore",
    "VersionedSourceStore",
    "DuplicateDocumentError",
    "HashMismatchError",
    "ImmutableObjectError",
    "InvalidObjectPathError",
    "MissingChapterError",
    "SourceEncodingError",
    "decode_source_bytes",
    "normalize_text",
    "validate_object_segment",
    "validate_relative_path",
]
