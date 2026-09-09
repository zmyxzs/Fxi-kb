"""候选技法包的校验、暂存与显式晋升。

这个模块故意不依赖 API 请求模型或 SQLite schema。候选包先进入隔离的
``materials/candidates`` 目录，只有 ``promote`` 在通过人工审批和评测引用
校验后，才会写入正式的 ``skills`` 目录。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence
from uuid import uuid4

import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import canonical_json, sha256_hex
from fxi.core.exceptions import CorruptedDataError, StorageError, ValidationError
from fxi.core.identifiers import validate_segment
from fxi.sources.evidence_store import EvidenceReferenceValidator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def package_hash(value: Any) -> str:
    """返回候选包的稳定 SHA-256 指纹。"""

    return sha256_hex(value)


def _require_segment(value: Any, field: str) -> str:
    return validate_segment(value, field)


def _as_reference_list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError(f"{field} 必须是引用列表")
    result = list(value)
    for index, reference in enumerate(result):
        if isinstance(reference, str):
            if not reference.strip():
                raise ValidationError(f"{field}[{index}] 不能为空")
            continue
        if not isinstance(reference, Mapping) or not reference:
            raise ValidationError(f"{field}[{index}] 必须是非空字符串或对象")
        if not any(reference.get(key) for key in ("id", "ref", "reference", "evidence_id", "source_id", "scene_id")):
            raise ValidationError(f"{field}[{index}] 缺少可识别的引用标识")
    return result


class CandidateReferenceValidator(Protocol):
    """由调用方注入的证据、学习运行和评测存在性校验边界。"""

    def validate_evidence(self, references: Sequence[Any]) -> None: ...

    def validate_learning_run(self, reference: str) -> None: ...

    def validate_evaluation(self, reference: str) -> None: ...


class CandidateApprovalVerifier(Protocol):
    """校验服务端不可变审批记录的边界。"""

    def get_approval(self, approval_id: str) -> Mapping[str, Any]: ...


class SqliteCandidateApprovalVerifier:
    """从服务端审批表读取审批收据，拒绝调用方自报的审批内容。"""

    def __init__(self, sqlite_path: Path):
        self.sqlite_path = sqlite_path

    def get_approval(self, approval_id: str) -> Mapping[str, Any]:
        try:
            with sqlite3.connect(self.sqlite_path) as connection:
                row = connection.execute(
                    """
                    SELECT approval_id, action, target_id, target_hash, expected_version,
                           work_id, source_id, source_version, candidate_version,
                           evaluation_ref, actor_id, expires_at, validity, consumed_by, created_at
                    FROM v2_approvals
                    WHERE approval_id = ?
                    """,
                    (approval_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"无法读取服务端审批记录 {approval_id}: {exc}") from exc
        if row is None:
            raise ValidationError(f"未找到服务端审批记录: {approval_id}")
        fields = (
            "approval_id",
            "action",
            "target_id",
            "target_hash",
            "expected_version",
            "work_id",
            "source_id",
            "source_version",
            "candidate_version",
            "evaluation_ref",
            "actor_id",
            "expires_at",
            "validity",
            "consumed_by",
            "created_at",
        )
        return dict(zip(fields, row, strict=True))


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    slug: str
    version: str
    package_hash: str
    package: dict[str, Any]
    evidence_refs: tuple[Any, ...] = ()
    learning_run_ref: Optional[str] = None
    evaluation_ref: Optional[str] = None
    status: str = "EVALUATION_CANDIDATE"
    idempotency_key: str = ""
    created_at: str = ""
    promoted_at: Optional[str] = None
    approval_id: Optional[str] = None
    approved_by: Optional[str] = None
    approved_expected_version: Optional[str] = None
    approval_action: Optional[str] = None
    work_id: Optional[str] = None
    source_id: Optional[str] = None
    source_version: Optional[str] = None
    input_hash: Optional[str] = None
    submitted_by: Optional[str] = None
    binding_errors: tuple[str, ...] = ()
    approval_receipt: Optional[dict[str, Any]] = None
    approval_receipt_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_refs"] = list(self.evidence_refs)
        data["binding_errors"] = list(self.binding_errors)
        return data

    @property
    def binding_complete(self) -> bool:
        return (
            not self.binding_errors
            and all(
                isinstance(value, str) and bool(value.strip())
                for value in (
                    self.work_id,
                    self.source_id,
                    self.source_version,
                    self.input_hash,
                    self.evaluation_ref,
                    self.submitted_by,
                )
            )
            and bool(self.evidence_refs)
        )


class CandidateRepository(Protocol):
    """候选记录的最小持久化协议。"""

    def get(self, candidate_id: str) -> Optional[CandidateRecord]: ...

    def get_by_idempotency(self, idempotency_key: str) -> Optional[CandidateRecord]: ...

    def list_records(self) -> Sequence[CandidateRecord]: ...

    def save(self, record: CandidateRecord) -> None: ...


def _record_from_dict(data: Mapping[str, Any]) -> CandidateRecord:
    try:
        record = CandidateRecord(
            candidate_id=str(data["candidate_id"]),
            slug=str(data["slug"]),
            version=str(data["version"]),
            package_hash=str(data["package_hash"]),
            package=dict(data["package"]),
            evidence_refs=tuple(data.get("evidence_refs", [])),
            learning_run_ref=data.get("learning_run_ref"),
            evaluation_ref=data.get("evaluation_ref"),
            status=str(data.get("status", "EVALUATION_CANDIDATE")),
            idempotency_key=str(data.get("idempotency_key", "")),
            created_at=str(data.get("created_at", "")),
            promoted_at=data.get("promoted_at"),
            approval_id=data.get("approval_id"),
            approved_by=data.get("approved_by"),
            approved_expected_version=data.get("approved_expected_version"),
            approval_action=data.get("approval_action"),
            work_id=data.get("work_id"),
            source_id=data.get("source_id"),
            source_version=data.get("source_version"),
            input_hash=data.get("input_hash"),
            submitted_by=data.get("submitted_by"),
            binding_errors=tuple(data.get("binding_errors", [])),
            approval_receipt=(
                dict(data["approval_receipt"])
                if isinstance(data.get("approval_receipt"), Mapping)
                else None
            ),
            approval_receipt_hash=data.get("approval_receipt_hash"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CorruptedDataError(f"候选记录结构损坏: {exc}") from exc
    if record.package_hash != package_hash(record.package):
        raise CorruptedDataError(f"候选记录 {record.candidate_id} 的 package_hash 不匹配")
    return record


class JsonCandidateRepository:
    """默认的文件候选仓库；不写正式技法目录。"""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, candidate_id: str) -> Path:
        _require_segment(candidate_id, "candidate_id")
        return self.directory / f"{candidate_id}.json"

    def _read(self, path: Path) -> CandidateRecord:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorruptedDataError(f"无法读取候选记录 {path.name}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise CorruptedDataError(f"候选记录 {path.name} 不是对象")
        return _record_from_dict(data)

    def get(self, candidate_id: str) -> Optional[CandidateRecord]:
        path = self._path(candidate_id)
        return self._read(path) if path.is_file() else None

    def get_by_idempotency(self, idempotency_key: str) -> Optional[CandidateRecord]:
        for path in sorted(self.directory.glob("*.json")):
            record = self._read(path)
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def list_records(self) -> list[CandidateRecord]:
        return [self._read(path) for path in sorted(self.directory.glob("*.json"))]

    def save(self, record: CandidateRecord) -> None:
        path = self._path(record.candidate_id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(canonical_json(record.to_dict()), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise StorageError(
                    f"无法保存候选记录 {record.candidate_id}: {exc}; 临时文件清理失败: {cleanup_exc}"
                ) from exc
            raise StorageError(f"无法保存候选记录 {record.candidate_id}: {exc}") from exc


class CandidateStore:
    """候选包的结构校验、暂存和人工审批后晋升服务。"""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        *,
        repository: Optional[CandidateRepository] = None,
        reference_validator: Optional[CandidateReferenceValidator] = None,
        approval_verifier: Optional[CandidateApprovalVerifier] = None,
        clock: Callable[[], str] = _now,
        id_factory: Callable[[], str] = lambda: f"candidate-{uuid4().hex}",
        require_scope: bool = True,
    ):
        self.config = config or load_config()
        self.repository = repository or JsonCandidateRepository(self.config.materials_dir / "candidates")
        self.reference_validator = reference_validator or EvidenceReferenceValidator(self.config.sources_dir)
        self.approval_verifier = approval_verifier or SqliteCandidateApprovalVerifier(self.config.sqlite_path)
        self.clock = clock
        self.id_factory = id_factory
        self.require_scope = require_scope
        self._lock = threading.RLock()
        self._heads_dir = self.config.materials_dir / "candidate_heads"
        self._heads_dir.mkdir(parents=True, exist_ok=True)
        self._recovery_dir = self.config.materials_dir / "candidate_promotions"
        self._recovery_dir.mkdir(parents=True, exist_ok=True)

    def validate_package(
        self,
        payload: Mapping[str, Any],
        *,
        version: Optional[str] = None,
        package_hash_value: Optional[str] = None,
        evidence_refs: Optional[Sequence[Any]] = None,
        learning_run_ref: Optional[str] = None,
        evaluation_ref: Optional[str] = None,
    ) -> tuple[str, str, dict[str, Any], list[Any], Optional[str], Optional[str], str]:
        if not isinstance(payload, Mapping):
            raise ValidationError("候选包必须是对象")
        slug = _require_segment(payload.get("slug"), "slug")
        resolved_version = version or payload.get("version") or "candidate-v1"
        resolved_version = _require_segment(resolved_version, "version")

        nested_package = payload.get("package")
        if nested_package is None:
            package = {
                key: payload[key]
                for key in ("rules", "anti_patterns", "style_rules", "examples", "metadata")
                if key in payload
            }
        elif isinstance(nested_package, Mapping):
            package = dict(nested_package)
        else:
            raise ValidationError("package 必须是对象")
        if not package:
            raise ValidationError("候选 package 不能为空")
        for list_name in ("rules", "anti_patterns", "style_rules", "examples"):
            if list_name in package and not isinstance(package[list_name], list):
                raise ValidationError(f"package.{list_name} 必须是列表")

        refs = _as_reference_list(
            evidence_refs if evidence_refs is not None else payload.get("evidence_refs", payload.get("evidence")),
            "evidence_refs",
        )
        resolved_learning = learning_run_ref if learning_run_ref is not None else payload.get("learning_run_ref")
        resolved_evaluation = evaluation_ref if evaluation_ref is not None else payload.get("evaluation_ref")
        for field, value in (("learning_run_ref", resolved_learning), ("evaluation_ref", resolved_evaluation)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValidationError(f"{field} 必须是非空字符串")

        actual_hash = package_hash(package)
        supplied_hash = package_hash_value or payload.get("package_hash")
        if supplied_hash is not None and supplied_hash != actual_hash:
            raise ValidationError("package_hash 与 package 内容不匹配")
        if self.reference_validator:
            if refs:
                self.reference_validator.validate_evidence(refs)
            if resolved_learning:
                self.reference_validator.validate_learning_run(resolved_learning)
            if resolved_evaluation:
                self.reference_validator.validate_evaluation(resolved_evaluation)
        idempotency_key = payload.get("idempotency_key") or f"candidate:{slug}:{resolved_version}:{actual_hash}"
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValidationError("idempotency_key 必须是非空字符串")
        return slug, resolved_version, package, refs, resolved_learning, resolved_evaluation, actual_hash

    @staticmethod
    def _scope_value(
        payload: Mapping[str, Any],
        package: Mapping[str, Any],
        field: str,
        explicit: Optional[str],
    ) -> Optional[str]:
        values = [
            value
            for value in (explicit, payload.get(field), package.get(field))
            if value is not None and value != ""
        ]
        if not values:
            return None
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValidationError(f"{field} 必须是非空字符串")
        first = values[0]
        if any(value != first for value in values[1:]):
            raise ValidationError(f"候选包的 {field} 绑定不一致")
        return _require_segment(first, field)

    def _resolve_binding(
        self,
        payload: Mapping[str, Any],
        package: Mapping[str, Any],
        evidence_refs: Sequence[Any],
        *,
        work_id: Optional[str],
        source_id: Optional[str],
        source_version: Optional[str],
        input_hash: Optional[str],
        evaluation_ref: Optional[str],
        submitted_by: Optional[str],
        require_scope: Optional[bool],
    ) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], tuple[str, ...]]:
        resolved_work = self._scope_value(payload, package, "work_id", work_id)
        resolved_source = self._scope_value(payload, package, "source_id", source_id)
        resolved_source_version = self._scope_value(payload, package, "source_version", source_version)
        explicit_input_hash = input_hash or payload.get("input_hash") or payload.get("text_hash")
        package_input_hash = package.get("input_hash") or package.get("text_hash")
        if explicit_input_hash and package_input_hash and explicit_input_hash != package_input_hash:
            raise ValidationError("候选包的 input_hash 绑定不一致")
        resolved_input_hash = explicit_input_hash or package_input_hash
        if resolved_input_hash is not None and (
            not isinstance(resolved_input_hash, str) or not resolved_input_hash.strip()
        ):
            raise ValidationError("input_hash 必须是非空字符串")

        payload_actor = payload.get("submitted_by")
        if payload_actor is not None and submitted_by is None:
            raise ValidationError("submitted_by 必须通过受控参数提供，不能由 payload 自报")
        if payload_actor is not None and payload_actor != submitted_by:
            raise ValidationError("payload 的 submitted_by 与受控提交身份不匹配")
        resolved_actor = submitted_by
        if resolved_actor is not None and (not isinstance(resolved_actor, str) or not resolved_actor.strip()):
            raise ValidationError("submitted_by 必须是非空字符串")

        # Evidence refs may carry the source binding. Infer it only when every
        # structured reference agrees; conflicting evidence is a hard error.
        for field_name in ("source_id", "source_version"):
            inferred = {
                str(reference.get(field_name))
                for reference in evidence_refs
                if isinstance(reference, Mapping) and reference.get(field_name) is not None
            }
            if len(inferred) > 1:
                raise ValidationError(f"证据引用的 {field_name} 绑定冲突")
            if inferred:
                value = next(iter(inferred))
                current = resolved_source if field_name == "source_id" else resolved_source_version
                if current is not None and current != value:
                    raise ValidationError(f"证据引用的 {field_name} 与候选包不匹配")
                if field_name == "source_id":
                    resolved_source = _require_segment(value, field_name)
                else:
                    resolved_source_version = _require_segment(value, field_name)

        evidence_is_bound = any(
            isinstance(reference, Mapping)
            and reference.get("source_id") == resolved_source
            and reference.get("source_version") == resolved_source_version
            for reference in evidence_refs
        )
        errors = tuple(
            code
            for code, value in (
                ("MISSING_WORK_ID", resolved_work),
                ("MISSING_SOURCE_ID", resolved_source),
                ("MISSING_SOURCE_VERSION", resolved_source_version),
                ("MISSING_INPUT_HASH", resolved_input_hash),
                ("MISSING_EVIDENCE_REFS", evidence_refs),
                ("EVIDENCE_SCOPE_UNBOUND", evidence_is_bound),
                ("MISSING_EVALUATION_REF", evaluation_ref),
                ("MISSING_SUBMITTED_BY", resolved_actor),
            )
            if not value
        )
        enforce = require_scope if require_scope is not None else self.require_scope
        if enforce and errors:
            raise ValidationError(f"候选范围绑定不完整: {', '.join(errors)}")
        return resolved_work, resolved_source, resolved_source_version, resolved_input_hash, resolved_actor, errors

    def submit(
        self,
        payload: Mapping[str, Any],
        *,
        version: Optional[str] = None,
        package_hash_value: Optional[str] = None,
        evidence_refs: Optional[Sequence[Any]] = None,
        learning_run_ref: Optional[str] = None,
        evaluation_ref: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        work_id: Optional[str] = None,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        input_hash: Optional[str] = None,
        submitted_by: Optional[str] = None,
        require_scope: Optional[bool] = None,
    ) -> CandidateRecord:
        slug, resolved_version, package, refs, learning, evaluation, actual_hash = self.validate_package(
            payload,
            version=version,
            package_hash_value=package_hash_value,
            evidence_refs=evidence_refs,
            learning_run_ref=learning_run_ref,
            evaluation_ref=evaluation_ref,
        )
        payload_key = payload.get("idempotency_key")
        if idempotency_key is not None and payload_key is not None and idempotency_key != payload_key:
            raise ValidationError("受控 idempotency_key 与 payload 不匹配")
        resolved_work, resolved_source, resolved_source_version, resolved_input_hash, resolved_actor, binding_errors = self._resolve_binding(
            payload,
            package,
            refs,
            work_id=work_id,
            source_id=source_id,
            source_version=source_version,
            input_hash=input_hash,
            evaluation_ref=evaluation,
            submitted_by=submitted_by,
            require_scope=require_scope,
        )
        key = idempotency_key or payload.get("idempotency_key") or (
            f"candidate:{resolved_work or '-'}:{resolved_source or '-'}:{resolved_source_version or '-'}:"
            f"{slug}:{resolved_version}:{actual_hash}"
        )
        if not isinstance(key, str) or not key.strip():
            raise ValidationError("idempotency_key 必须是非空字符串")
        with self._lock:
            existing = self.repository.get_by_idempotency(key)
            if existing:
                if (
                    existing.slug != slug
                    or existing.version != resolved_version
                    or existing.package_hash != actual_hash
                    or existing.package != package
                    or existing.evidence_refs != tuple(refs)
                    or existing.learning_run_ref != learning
                    or existing.evaluation_ref != evaluation
                    or existing.work_id != resolved_work
                    or existing.source_id != resolved_source
                    or existing.source_version != resolved_source_version
                    or existing.input_hash != resolved_input_hash
                    or (resolved_actor is not None and existing.submitted_by != resolved_actor)
                ):
                    raise ValidationError("同幂等键绑定了不同候选包或范围")
                return existing
            record = CandidateRecord(
                candidate_id=self.id_factory(),
                slug=slug,
                version=resolved_version,
                package_hash=actual_hash,
                package=package,
                evidence_refs=tuple(refs),
                learning_run_ref=learning,
                evaluation_ref=evaluation,
                idempotency_key=key,
                created_at=self.clock(),
                work_id=resolved_work,
                source_id=resolved_source,
                source_version=resolved_source_version,
                input_hash=resolved_input_hash,
                submitted_by=resolved_actor,
                binding_errors=binding_errors,
            )
            self.repository.save(record)
            return record

    def get(self, candidate_id: str) -> CandidateRecord:
        record = self.repository.get(candidate_id)
        if not record:
            raise ValidationError(f"未找到候选包: {candidate_id}")
        return record

    def qualification_view(self, *, work_id: Optional[str] = None) -> tuple[CandidateRecord, ...]:
        """返回可供正式资格消费者读取的候选；待审核记录永不出现在此视图。"""

        if work_id is not None:
            work_id = _require_segment(work_id, "work_id")
        with self._lock:
            records = self.repository.list_records()
            return tuple(
                record
                for record in records
                if self.is_trusted_approved(record)
                and (work_id is None or record.work_id == work_id or record.package.get("work_id") == work_id)
            )

    def is_trusted_approved(self, record: CandidateRecord) -> bool:
        """只有可复验的服务端审批收据才能让候选进入正式消费者视图。"""

        if record.status != "APPROVED" or not record.binding_complete:
            return False
        receipt = record.approval_receipt
        if not isinstance(receipt, Mapping) or not isinstance(record.approval_receipt_hash, str):
            return False
        if sha256_hex(receipt) != record.approval_receipt_hash:
            return False
        binding = receipt.get("binding")
        server_record = receipt.get("server_record")
        if not isinstance(binding, Mapping) or not isinstance(server_record, Mapping):
            return False
        expected_binding = self._approval_binding(record)
        if canonical_json(binding) != canonical_json(expected_binding):
            return False
        approval_id = server_record.get("approval_id")
        if not isinstance(approval_id, str) or approval_id != record.approval_id:
            return False
        try:
            current_server_record = self.approval_verifier.get_approval(approval_id)
        except (StorageError, ValidationError):
            return False
        return canonical_json(current_server_record) == canonical_json(server_record)

    @staticmethod
    def _approval_binding(record: CandidateRecord) -> dict[str, str]:
        """生成必须写入审批收据的候选范围绑定。"""

        return {
            "candidate_id": record.candidate_id,
            "package_hash": record.package_hash,
            "work_id": record.work_id or "",
            "source_id": record.source_id or "",
            "source_version": record.source_version or "",
            "input_hash": record.input_hash or "",
            "evaluation_ref": record.evaluation_ref or "",
        }

    def _head_path(self, slug: str) -> Path:
        return self._heads_dir / f"{_require_segment(slug, 'slug')}.json"

    def _active_version(self, slug: str) -> str:
        path = self._head_path(slug)
        if not path.is_file():
            return "style-v0"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorruptedDataError(f"候选正式版本记录损坏: {exc}") from exc
        version = data.get("active_version") if isinstance(data, Mapping) else None
        if not isinstance(version, str) or not version:
            raise CorruptedDataError(f"候选正式版本记录缺少 active_version: {slug}")
        return version

    def _write_active_version(self, slug: str, version: str, candidate_id: str, actor_id: str) -> None:
        path = self._head_path(slug)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        data = {"slug": slug, "active_version": version, "candidate_id": candidate_id, "approved_by": actor_id, "updated_at": self.clock()}
        try:
            temporary.write_text(canonical_json(data), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise StorageError(
                    f"无法更新候选正式版本 {slug}: {exc}; 临时文件清理失败: {cleanup_exc}"
                ) from exc
            raise StorageError(f"无法更新候选正式版本 {slug}: {exc}") from exc

    def _recovery_path(self, candidate_id: str) -> Path:
        return self._recovery_dir / f"{_require_segment(candidate_id, 'candidate_id')}.json"

    def _write_recovery_state(self, payload: Mapping[str, Any]) -> None:
        path = self._recovery_path(str(payload["candidate_id"]))
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(canonical_json(dict(payload)), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise StorageError(
                    f"无法记录候选晋升恢复状态: {exc}; 临时文件清理失败: {cleanup_exc}"
                ) from cleanup_exc
            raise StorageError(f"无法记录候选晋升恢复状态: {exc}") from exc

    def _clear_recovery_state(self, candidate_id: str) -> None:
        try:
            self._recovery_path(candidate_id).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"无法清理候选晋升恢复状态: {candidate_id}") from exc

    def _recover_promotion(self, record: CandidateRecord, *, actor_id: str, approval_id: str) -> Optional[CandidateRecord]:
        """Finish a locally interrupted promotion after server approval consumption."""

        path = self._recovery_path(record.candidate_id)
        if not path.is_file():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError(f"候选晋升恢复状态损坏: {path.name}") from exc
        if not isinstance(state, Mapping) or state.get("approval_id") != approval_id:
            raise ValidationError("候选晋升存在未匹配的恢复状态")
        server_record = self.approval_verifier.get_approval(approval_id)
        if server_record.get("consumed_by") is None:
            return None
        receipt = {
            "binding": self._approval_binding(record),
            "server_record": dict(server_record),
        }
        promoted = replace(
            record,
            status="APPROVED",
            promoted_at=self.clock(),
            approval_id=approval_id,
            approved_by=actor_id,
            approved_expected_version=server_record.get("expected_version"),
            approval_action=server_record.get("action"),
            approval_receipt=receipt,
            approval_receipt_hash=sha256_hex(receipt),
        )
        self.repository.save(promoted)
        self._write_active_version(record.slug, record.version, record.candidate_id, actor_id)
        self._clear_recovery_state(record.candidate_id)
        return promoted

    @staticmethod
    def _approval_value(approval: Mapping[str, Any] | Any, name: str, default: Any = None) -> Any:
        if isinstance(approval, Mapping):
            return approval.get(name, default)
        return getattr(approval, name, default)

    def promote(
        self,
        candidate_id: str,
        approval: Mapping[str, Any] | Any,
        *,
        actor_id: str,
        expected_active_version: Optional[str] = None,
        approval_consumer: Optional[Callable[[str, str], None]] = None,
    ) -> CandidateRecord:
        with self._lock:
            return self._promote_locked(
                candidate_id,
                approval,
                actor_id=actor_id,
                expected_active_version=expected_active_version,
                approval_consumer=approval_consumer,
            )

    def _promote_locked(
        self,
        candidate_id: str,
        approval: Mapping[str, Any] | Any,
        *,
        actor_id: str,
        expected_active_version: Optional[str] = None,
        approval_consumer: Optional[Callable[[str, str], None]] = None,
    ) -> CandidateRecord:
        """在人工审批后发布候选包；没有评测引用或 actor 不匹配时拒绝。"""

        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValidationError("晋升必须提供 actor_id")
        record = self.get(candidate_id)
        approval_id_hint = self._approval_value(approval, "approval_id")
        if isinstance(approval_id_hint, str) and approval_id_hint.strip():
            recovered = self._recover_promotion(
                record,
                actor_id=actor_id,
                approval_id=approval_id_hint,
            )
            if recovered is not None:
                return recovered
        if record.status == "APPROVED":
            if not self.is_trusted_approved(record):
                raise ValidationError("候选记录的审批状态没有可验证服务端收据")
            if record.approved_by == actor_id and record.approval_id == self._approval_value(approval, "approval_id"):
                # All binding fields were already verified against the
                # current server receipt when the record was first promoted.
                # A replay is keyed by approval_id/actor only; caller-supplied
                # copies of the other fields are not trusted.
                return record
            raise ValidationError("候选包已被其他审批提交晋升")
        if record.status != "EVALUATION_CANDIDATE":
            raise ValidationError(f"候选包状态不可晋升: {record.status}")
        if not record.evaluation_ref:
            raise ValidationError("候选包缺少 evaluation_ref，不能晋升")

        approval_id = self._approval_value(approval, "approval_id")
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValidationError("审批记录缺少 approval_id")
        server_approval = self.approval_verifier.get_approval(approval_id)
        approved_actor = server_approval.get("actor_id")
        action = server_approval.get("action")
        target_id = server_approval.get("target_id")
        target_hash = server_approval.get("target_hash")
        approval_work_id = server_approval.get("work_id")
        # The database receipt is authoritative.  Values supplied by the
        # caller are only an optional consistency hint and never fill in a
        # missing server binding.
        approval_source_id = server_approval.get("source_id")
        approval_source_version = server_approval.get("source_version")
        approval_candidate_version = server_approval.get("candidate_version")
        expected_version = server_approval.get("expected_version")
        approval_evaluation_ref = server_approval.get("evaluation_ref")
        validity = server_approval.get("validity")
        consumed_by = server_approval.get("consumed_by")
        if not approval_id or approved_actor != actor_id:
            raise ValidationError("审批记录缺少匹配的 actor_id")
        if action not in {"style_promotion", "skill_promotion", "candidate_promotion"}:
            raise ValidationError("审批动作不是候选晋升")
        if target_id != record.candidate_id or target_hash != record.package_hash:
            raise ValidationError("审批目标或候选哈希不匹配")
        if not record.binding_complete:
            raise ValidationError(f"候选范围绑定不完整，不能晋升: {', '.join(record.binding_errors)}")
        if approval_work_id != record.work_id:
            raise ValidationError("审批 work_id 与候选作品不匹配")
        if not isinstance(approval_source_id, str) or not approval_source_id.strip():
            raise ValidationError("服务端审批记录缺少 source_id")
        if not isinstance(approval_source_version, str) or not approval_source_version.strip():
            raise ValidationError("服务端审批记录缺少 source_version")
        if approval_source_id != record.source_id or approval_source_version != record.source_version:
            raise ValidationError("审批 source 绑定与候选不匹配")
        if not isinstance(approval_candidate_version, str) or not approval_candidate_version.strip():
            raise ValidationError("审批记录缺少 candidate_version")
        if approval_candidate_version != record.version:
            raise ValidationError("审批 candidate_version 与候选不匹配")
        if not isinstance(approval_evaluation_ref, str) or not approval_evaluation_ref.strip():
            raise ValidationError("服务端审批记录缺少 evaluation_ref")
        if approval_evaluation_ref != record.evaluation_ref:
            raise ValidationError("审批 evaluation_ref 与候选不匹配")
        if not isinstance(expected_version, str) or not expected_version.strip():
            raise ValidationError("审批记录缺少 expected_version")
        current = self._active_version(record.slug)
        expected_current = expected_active_version or current
        if current != expected_current or expected_version != expected_current:
            raise ValidationError("候选正式版本已变化")
        if validity != "VALID" or consumed_by:
            raise ValidationError("审批无效或已被消费")
        expires_at = server_approval.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationError("审批 expires_at 格式非法") from exc
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                raise ValidationError("审批已过期")
        if self.reference_validator:
            self.reference_validator.validate_evaluation(record.evaluation_ref)

        if approval_consumer is None:
            raise ValidationError("晋升必须提供服务端审批消费器")
        previous_dir: Optional[Path] = None
        approval_consumed = False
        try:
            previous_dir = self._publish(record)
            # The marker is written before consuming the server receipt.  A
            # process crash after consumption can therefore be resumed by
            # _recover_promotion instead of being stranded as an invisible
            # half-publish.
            self._write_recovery_state(
                {
                    "candidate_id": record.candidate_id,
                    "approval_id": str(approval_id),
                    "slug": record.slug,
                    "version": record.version,
                    "phase": "PUBLISHED_PENDING_APPROVAL",
                }
            )
            approval_consumer(str(approval_id), record.candidate_id)
            approval_consumed = True
            consumed_approval = self.approval_verifier.get_approval(approval_id)
            if not consumed_approval.get("consumed_by"):
                raise ValidationError("服务端审批消费未留下可验证收据")
            receipt = {
                "binding": self._approval_binding(record),
                "server_record": dict(consumed_approval),
            }
            promoted = replace(
                record,
                status="APPROVED",
                promoted_at=self.clock(),
                approval_id=str(approval_id),
                approved_by=actor_id,
                approved_expected_version=expected_version,
                approval_action=action,
                approval_receipt=receipt,
                approval_receipt_hash=sha256_hex(receipt),
            )
            self.repository.save(promoted)
            self._write_active_version(record.slug, record.version, record.candidate_id, actor_id)
            if previous_dir is not None and previous_dir.exists():
                shutil.rmtree(previous_dir)
            self._clear_recovery_state(record.candidate_id)
            return promoted
        except Exception as exc:
            if not approval_consumed:
                try:
                    self._rollback_publish(record, previous_dir)
                    self._clear_recovery_state(record.candidate_id)
                except Exception as rollback_exc:
                    raise StorageError(
                        f"候选晋升失败且回滚不完整: {type(rollback_exc).__name__}"
                    ) from exc
                raise
            # The remote approval is already consumed.  Keep an explicit
            # recovery record and expose the inconsistency to the caller.
            try:
                self._write_recovery_state(
                    {
                        "candidate_id": record.candidate_id,
                        "approval_id": str(approval_id),
                        "slug": record.slug,
                        "version": record.version,
                        "phase": "APPROVAL_CONSUMED",
                    }
                )
            except Exception as recovery_exc:
                raise StorageError(
                    f"审批已消费且恢复状态写入失败: {type(recovery_exc).__name__}"
                ) from exc
            raise StorageError(
                f"审批已消费但候选晋升尚未完成，请重试以恢复: {record.candidate_id}"
            ) from exc

    def _publish(self, record: CandidateRecord) -> Optional[Path]:
        target_dir = self.config.skills_dir / record.slug
        staging_dir = self.config.skills_dir / f".{record.slug}.staging-{uuid4().hex}"
        previous_dir: Optional[Path] = None
        try:
            staging_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise StorageError(f"无法创建候选正式目录 {record.slug}: {exc}") from exc
        files: dict[str, Any] = {"package.yaml": record.package}
        if "rules" in record.package:
            files["rules.yaml"] = {"rules": record.package["rules"]}
        if "anti_patterns" in record.package:
            files["anti_patterns.yaml"] = {"anti_patterns": record.package["anti_patterns"]}
        for filename, value in files.items():
            target = staging_dir / filename
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
                os.replace(temporary, target)
            except OSError as exc:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    raise StorageError(
                        f"无法发布候选文件 {target}: {exc}; 临时文件清理失败: {cleanup_exc}"
                    ) from exc
                raise StorageError(f"无法发布候选文件 {target}: {exc}") from exc
        if target_dir.exists():
            previous_dir = self.config.skills_dir / f".{record.slug}.previous-{uuid4().hex}"
            try:
                target_dir.rename(previous_dir)
            except OSError as exc:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise StorageError(f"无法暂存旧候选正式目录 {record.slug}: {exc}") from exc
        try:
            staging_dir.rename(target_dir)
        except OSError as exc:
            if previous_dir is not None and previous_dir.exists() and not target_dir.exists():
                previous_dir.rename(target_dir)
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise StorageError(f"无法切换候选正式目录 {record.slug}: {exc}") from exc
        return previous_dir

    def _rollback_publish(self, record: CandidateRecord, previous_dir: Optional[Path]) -> None:
        target_dir = self.config.skills_dir / record.slug
        if target_dir.exists():
            shutil.rmtree(target_dir)
        if previous_dir is not None and previous_dir.exists():
            previous_dir.rename(target_dir)


__all__ = [
    "CandidateApprovalVerifier",
    "CandidateRecord",
    "CandidateReferenceValidator",
    "CandidateRepository",
    "CandidateStore",
    "JsonCandidateRepository",
    "SqliteCandidateApprovalVerifier",
    "package_hash",
]
