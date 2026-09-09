"""来源快照与证据坐标的领域接口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from fxi.core.exceptions import NotFoundError, ValidationError
from fxi.storage.versioned_store import (
    SourceDocumentInput,
    SourceSnapshot,
    StoredDocument,
    VersionedStore,
    validate_object_segment,
)
from fxi.storage.text_io import calculate_content_hash


class EvidenceMismatchError(ValidationError):
    """EvidenceRef 与不可变来源正文不匹配。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = "EVIDENCE_MISMATCH"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """绑定不可变来源版本和规范文本半开区间 [start_char, end_char)。"""

    source_id: str
    source_version: str
    document_id: str
    start_char: int
    end_char: Optional[int]
    excerpt_hash: str
    quote: Optional[str] = None

    @property
    def source_version_id(self) -> str:
        """兼容知识库文档中使用的 source_version_id 命名。"""

        return self.source_version

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvidenceRef":
        version = value.get("source_version", value.get("source_version_id"))
        if version is None:
            raise EvidenceMismatchError("EvidenceRef 缺少 source_version")
        try:
            start_char = value.get("start_char", 0)
            end_char = value.get("end_char")
            ref = cls(
                source_id=value["source_id"],
                source_version=version,
                document_id=value["document_id"],
                start_char=start_char,
                end_char=end_char,
                excerpt_hash=value["excerpt_hash"],
                quote=(None if value.get("quote") is None else str(value["quote"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceMismatchError("EvidenceRef 字段无效") from exc
        ref._validate_shape()
        return ref

    def _validate_shape(self) -> None:
        for value, label in (
            (self.source_id, "source_id"),
            (self.source_version, "source_version"),
            (self.document_id, "document_id"),
        ):
            if not isinstance(value, str):
                raise EvidenceMismatchError(f"EvidenceRef {label} 必须是字符串")
            try:
                validate_object_segment(value, label)
            except ValidationError as exc:
                raise EvidenceMismatchError(f"EvidenceRef {label} 无效") from exc
        if (
            isinstance(self.start_char, bool)
            or not isinstance(self.start_char, int)
            or self.start_char < 0
        ):
            raise EvidenceMismatchError(
                f"EvidenceRef 区间无效: [{self.start_char}, {self.end_char})"
            )
        if self.end_char is not None and (
            isinstance(self.end_char, bool)
            or not isinstance(self.end_char, int)
            or self.end_char < self.start_char
        ):
            raise EvidenceMismatchError(
                f"EvidenceRef 区间无效: [{self.start_char}, {self.end_char})"
            )
        if (
            not isinstance(self.excerpt_hash, str)
            or len(self.excerpt_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.excerpt_hash)
        ):
            raise EvidenceMismatchError("EvidenceRef excerpt_hash 必须是 SHA-256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_version": self.source_version,
            "document_id": self.document_id,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "excerpt_hash": self.excerpt_hash,
            "quote": self.quote,
        }


@dataclass(frozen=True, slots=True)
class ValidatedEvidence:
    ref: EvidenceRef
    text: str
    document_hash: str
    raw_hash: str
    chapter_index: int
    normalization_version: str

    @property
    def excerpt_hash(self) -> str:
        return calculate_content_hash(self.text)


class EvidenceStore:
    """EvidenceRef 的唯一校验边界，正文读取委托给 VersionedStore。"""

    def __init__(self, sources_root: Path, versioned_store: Optional[VersionedStore] = None):
        self.versioned = versioned_store or VersionedStore(Path(sources_root))

    def create_snapshot(
        self,
        source_id: str,
        documents: Iterable[SourceDocumentInput],
        *,
        version: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> SourceSnapshot:
        return self.versioned.create_snapshot(
            source_id,
            documents,
            version=version,
            metadata=metadata,
        )

    def read_snapshot(self, source_id: str, source_version: str) -> SourceSnapshot:
        return self.versioned.read_snapshot(source_id, source_version)

    def read_document(
        self,
        source_id: str,
        source_version: str,
        document_id: str,
        *,
        start_char: int = 0,
        end_char: Optional[int] = None,
    ) -> StoredDocument:
        return self.versioned.read_document(
            source_id,
            source_version,
            document_id,
            start_char=start_char,
            end_char=end_char,
        )

    def validate_ref(
        self,
        ref: EvidenceRef | Mapping[str, Any],
        *,
        expected_text: Optional[str] = None,
    ) -> ValidatedEvidence:
        evidence_ref = EvidenceRef.from_mapping(ref) if isinstance(ref, Mapping) else ref
        if not isinstance(evidence_ref, EvidenceRef):
            raise EvidenceMismatchError("ref 必须是 EvidenceRef 或映射")
        evidence_ref._validate_shape()
        try:
            document = self.read_document(
                evidence_ref.source_id,
                evidence_ref.source_version,
                evidence_ref.document_id,
                start_char=evidence_ref.start_char,
                end_char=evidence_ref.end_char,
            )
        except (NotFoundError, ValidationError) as exc:
            raise EvidenceMismatchError(
                "EvidenceRef 无法绑定到指定的来源版本、文档或坐标"
            ) from exc
        if (
            document.source_id != evidence_ref.source_id
            or document.source_version != evidence_ref.source_version
            or document.document_id != evidence_ref.document_id
            or document.start_char != evidence_ref.start_char
            or (evidence_ref.end_char is not None and document.end_char != evidence_ref.end_char)
        ):
            raise EvidenceMismatchError("EvidenceRef 身份或坐标未被不可变来源确认")
        if document.excerpt_hash != evidence_ref.excerpt_hash:
            raise EvidenceMismatchError(
                f"证据片段哈希不一致: expected={evidence_ref.excerpt_hash}, "
                f"actual={document.excerpt_hash}"
            )
        if evidence_ref.quote is not None and evidence_ref.quote != document.content:
            raise EvidenceMismatchError("EvidenceRef quote 与不可变来源片段不一致")
        if expected_text is not None and expected_text != document.content:
            raise EvidenceMismatchError("调用方正文与不可变来源片段不一致")
        return ValidatedEvidence(
            ref=evidence_ref,
            text=document.content,
            document_hash=document.document_hash,
            raw_hash=document.raw_hash,
            chapter_index=document.chapter_index,
            normalization_version=document.normalization_version,
        )


class EvidenceReferenceValidator:
    """Candidate-store adapter backed by the immutable source object store.

    Evidence references are never treated as opaque labels at the candidate
    boundary.  Learning/evaluation references remain external artifact IDs,
    so this adapter validates their shape while the owning evaluation service
    remains responsible for resolving their existence.
    """

    def __init__(self, sources_root: Path):
        self.store = EvidenceStore(Path(sources_root))

    def validate_evidence(self, references: Iterable[Any]) -> None:
        for reference in references:
            if isinstance(reference, str):
                raise EvidenceMismatchError("evidence_refs 必须使用结构化 EvidenceRef")
            self.store.validate_ref(reference)

    @staticmethod
    def _validate_external_reference(reference: str, label: str) -> None:
        if not isinstance(reference, str) or not reference.strip():
            raise EvidenceMismatchError(f"{label} 必须是非空外部引用")

    def validate_learning_run(self, reference: str) -> None:
        self._validate_external_reference(reference, "learning_run_ref")

    def validate_evaluation(self, reference: str) -> None:
        self._validate_external_reference(reference, "evaluation_ref")


__all__ = [
    "EvidenceMismatchError",
    "EvidenceRef",
    "EvidenceStore",
    "EvidenceReferenceValidator",
    "ValidatedEvidence",
]
