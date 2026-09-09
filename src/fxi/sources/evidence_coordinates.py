"""Exact, half-open evidence coordinates over published source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import NotFoundError, ValidationError
from fxi.knowledge.contracts import EvidenceRef
from fxi.storage.versioned_store import VersionedStore

from .snapshot_service import SnapshotService


class EvidenceValidationError(ValidationError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "INVALID_EVIDENCE"


@dataclass(frozen=True, slots=True)
class ValidatedEvidence:
    ref: EvidenceRef
    text: str
    document_hash: str
    raw_hash: str
    normalization_version: str
    chapter_index: int


class EvidenceService:
    """Validate only; it never persists excerpt text or creates knowledge."""

    def __init__(
        self,
        root: Path | str,
        *,
        snapshot_service: SnapshotService | None = None,
        store: VersionedStore | None = None,
    ) -> None:
        self.store = store or VersionedStore(Path(root))
        self.snapshot_service = snapshot_service

    def _validate_snapshot(self, ref: EvidenceRef) -> None:
        if ref.source_snapshot_ref is None:
            return
        if self.snapshot_service is None:
            raise EvidenceValidationError("source_snapshot_ref 需要 SnapshotService 校验")
        snapshot = self.snapshot_service.get(ref.source_snapshot_ref)
        if snapshot.status != "PUBLISHED":
            raise EvidenceValidationError("证据只能引用已发布来源快照")
        if snapshot.source_id != ref.source_id or snapshot.source_version != ref.source_version:
            raise EvidenceValidationError("EvidenceRef 与 SourceSnapshotRef 身份不匹配")
        if ref.scope is not None:
            if ref.scope.work_id != snapshot.work_id or ref.scope.branch_id != self.snapshot_service.binding_for(snapshot.snapshot_id).branch_id:
                raise EvidenceValidationError("EvidenceRef scope 与来源快照 work/branch 不匹配")
        if ref.license is not None and ref.license != self.snapshot_service.binding_for(snapshot.snapshot_id).license:
            raise EvidenceValidationError("EvidenceRef license 与来源绑定不匹配")

    def validate(
        self, ref: EvidenceRef | Mapping[str, Any], *, expected_text: str | None = None
    ) -> ValidatedEvidence:
        try:
            evidence_ref = ref if isinstance(ref, EvidenceRef) else EvidenceRef.model_validate(ref)
        except Exception as exc:
            raise EvidenceValidationError("EvidenceRef schema 无效") from exc
        self._validate_snapshot(evidence_ref)
        try:
            document = self.store.read_document(
                evidence_ref.source_id,
                evidence_ref.source_version,
                evidence_ref.document_id,
                start_char=evidence_ref.start,
                end_char=evidence_ref.end,
            )
        except (NotFoundError, ValidationError) as exc:
            raise EvidenceValidationError(
                "EvidenceRef 无法绑定到指定的来源版本、文档或坐标"
            ) from exc
        if document.end_char != evidence_ref.end:
            raise EvidenceValidationError("EvidenceRef 必须使用正文内半开区间 [start, end)")
        actual_hash = sha256_hex(document.content)
        if actual_hash != evidence_ref.excerpt_hash:
            raise EvidenceValidationError(
                f"excerpt_hash 不匹配: expected={evidence_ref.excerpt_hash}, actual={actual_hash}"
            )
        if document.normalization_version != evidence_ref.normalization_version:
            raise EvidenceValidationError("normalization_version 与来源快照不匹配")
        if expected_text is not None:
            if not isinstance(expected_text, str):
                raise EvidenceValidationError("expected_text 必须是字符串")
            if expected_text != document.content:
                raise EvidenceValidationError("expected_text 与不可变来源片段不一致")
            if sha256_hex(expected_text) != evidence_ref.excerpt_hash:
                raise EvidenceValidationError("expected_text 哈希与 EvidenceRef 不一致")
        return ValidatedEvidence(
            ref=evidence_ref,
            text=document.content,
            document_hash=document.document_hash,
            raw_hash=document.raw_hash,
            normalization_version=document.normalization_version,
            chapter_index=document.chapter_index,
        )


__all__ = ["EvidenceService", "EvidenceValidationError", "ValidatedEvidence"]
