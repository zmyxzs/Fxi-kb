"""Publication of adapter revisions as immutable, published snapshot refs."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from fxi.core.canonical import canonical_json, sha256_hex
from fxi.core.exceptions import NotFoundError, ValidationError
from fxi.core.identifiers import validate_segment
from fxi.knowledge.contracts import SourceBindingRef, SourceSnapshotRef
from fxi.storage.versioned_store import SourceSnapshot, VersionedStore

from .adapters import SourceRevision


class SnapshotError(ValidationError):
    def __init__(self, message: str, *, code: str = "INVALID_SCHEMA") -> None:
        super().__init__(message)
        self.code = code


class SnapshotService:
    """Create once, publish once; failed attempts remain inspectable in staging."""

    def __init__(self, root: Path | str, *, store: VersionedStore | None = None) -> None:
        self.root = Path(root)
        self.store = store or VersionedStore(self.root)
        self._refs: dict[str, SourceSnapshotRef] = {}
        self._bindings: dict[str, SourceBindingRef] = {}
        self._active: dict[tuple[str, str, str], str] = {}

    @property
    def staging_root(self) -> Path:
        return self.root / ".staging"

    def _validate_root(self) -> None:
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise SnapshotError("快照根目录必须是非 symlink 的目录", code="INVALID_SCOPE")

    def _stage(self, revision: SourceRevision, binding: SourceBindingRef) -> Path:
        stage = self.staging_root / binding.work_id / binding.source_id / uuid4().hex
        stage.mkdir(parents=True, exist_ok=False)
        manifest = {
            "work_id": binding.work_id,
            "source_id": revision.source_id,
            "binding_id": binding.binding_id,
            "branch_id": binding.branch_id,
            "source_version": revision.source_version,
            "content_hash": revision.content_hash,
            "document_ids": [doc.document_id for doc in revision.documents],
            "status": "STAGING",
        }
        self._write_json(stage / "manifest.json", manifest)
        return stage

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        serialized = canonical_json(payload)
        path.write_text(serialized, encoding="utf-8")
        if path.read_text(encoding="utf-8") != serialized:
            raise SnapshotError(f"staging 清单落盘校验失败: {path}")

    @classmethod
    def _record_error(cls, stage: Path, error: BaseException) -> None:
        details = {
            "error_type": type(error).__name__,
            "error_code": getattr(error, "code", "FXI_ERROR"),
            "message": str(error),
        }
        cls._write_json(stage / "error.json", details)

    @staticmethod
    def _validate_revision(revision: SourceRevision, binding: SourceBindingRef) -> None:
        if not isinstance(revision, SourceRevision):
            raise SnapshotError("revision 必须是 SourceRevision")
        if revision.source_id != binding.source_id:
            raise SnapshotError("source_id 与 binding 不匹配", code="INVALID_SCOPE")
        if revision.work_id is not None and revision.work_id != binding.work_id:
            raise SnapshotError("work_id 与 binding 不匹配", code="INVALID_SCOPE")
        if not revision.documents:
            raise SnapshotError("revision 必须至少包含一个文档")
        validate_segment(revision.source_version, "source_version")
        validate_segment(revision.source_id, "source_id")
        if len(revision.content_hash) != 64:
            raise SnapshotError("content_hash 必须是 SHA-256")
        expected_hash = (
            sha256_hex(revision.documents[0].text)
            if len(revision.documents) == 1
            else sha256_hex([document.text for document in revision.documents])
        )
        if revision.content_hash != expected_hash:
            raise SnapshotError("revision content_hash 与规范文档内容不一致")
        if any(doc.document_id == "" for doc in revision.documents):
            raise SnapshotError("文档必须有 document_id")

    def create(self, revision: SourceRevision, binding: SourceBindingRef) -> SourceSnapshotRef:
        if not isinstance(binding, SourceBindingRef):
            raise SnapshotError("binding 必须是 SourceBindingRef")
        self._validate_root()
        stage = self._stage(revision, binding)
        try:
            self._validate_revision(revision, binding)
            snapshot: SourceSnapshot = self.store.create_snapshot(
                revision.source_id,
                revision.documents,
                version=revision.source_version,
                metadata={
                    **dict(revision.metadata),
                    "work_id": binding.work_id,
                    "binding_id": binding.binding_id,
                    "branch_id": binding.branch_id,
                    "adapter_revision": revision.normalization_version,
                },
            )
            snapshot_id = "snapshot-" + sha256_hex(
                {
                    "work_id": binding.work_id,
                    "source_id": revision.source_id,
                    "source_version": revision.source_version,
                    "binding_id": binding.binding_id,
                }
            )
            ref = SourceSnapshotRef(
                snapshot_id=snapshot_id,
                work_id=binding.work_id,
                source_id=revision.source_id,
                source_version=snapshot.version,
                content_hash=revision.content_hash,
                binding_id=binding.binding_id,
                status="PUBLISHED",
                document_refs=tuple(doc.document_id for doc in snapshot.documents),
            )
            prior = self._refs.get(snapshot_id)
            if prior is not None and prior != ref:
                raise SnapshotError("snapshot_id 内容冲突", code="STALE_VERSION")
            self._refs[snapshot_id] = ref
            self._bindings[binding.binding_id] = binding
            self._active[(binding.work_id, binding.source_id, binding.branch_id)] = snapshot_id
            self._write_json(stage / "published.json", ref.model_dump(mode="json"))
            return ref
        except Exception as error:
            try:
                self._record_error(stage, error)
            except Exception as record_error:
                error.add_note(f"无法写入 staging error.json: {record_error}")
            raise

    def get(self, snapshot_id: str) -> SourceSnapshotRef:
        validate_segment(snapshot_id, "snapshot_id")
        try:
            return self._refs[snapshot_id]
        except KeyError as exc:
            raise NotFoundError(f"未找到来源快照引用: {snapshot_id}") from exc

    def active(self, work_id: str, source_id: str, branch_id: str) -> SourceSnapshotRef | None:
        key = (work_id, source_id, branch_id)
        snapshot_id = self._active.get(key)
        return self._refs.get(snapshot_id) if snapshot_id is not None else None

    def binding_for(self, snapshot_id: str) -> SourceBindingRef:
        ref = self.get(snapshot_id)
        try:
            return self._bindings[ref.binding_id]
        except KeyError as exc:
            raise NotFoundError(f"快照绑定不可用: {snapshot_id}") from exc


__all__ = ["SnapshotError", "SnapshotService"]
