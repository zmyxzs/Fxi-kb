"""Versioned, auditable writing workflow API.

The v2 routes deliberately keep their state in SQLite and bind every object to
the source/knowledge versions supplied by the caller.  The legacy v1 router is
left untouched for compatibility.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional

import yaml
from fastapi import APIRouter, HTTPException, Request

from fxi.api.auth import require_actor
from fxi.api.registry import SourceNotFoundError, WorkNotFoundError
from fxi.api.contracts import (
    ApprovalRequestV2,
    ApprovalResponseV2,
    ChapterCommitRequestV2,
    ChapterProposalRequestV2,
    CommitReceiptV2,
    ReviewCheckV2,
    ReviewReportV2,
    SourceDocumentResponseV2,
    SourceDocumentV2,
    SourceSnapshotRequestV2,
    SourceSnapshotResponseV2,
    StyleCandidateRequestV2,
    StylePromotionRequestV2,
    WritingContextRequestV2,
    WritingContextResponseV2,
    WritingReviewRequestV2,
)
from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import canonical_json, sha256_hex
from fxi.index_retrieval.context_pruner import ContextPruner
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.storage.sqlite_client import DatabaseClient, ensure_entity
from fxi.sources.evidence_store import EvidenceStore, EvidenceMismatchError
from fxi.storage.versioned_store import SourceDocumentInput
from fxi.timeline.fact_checker import ContinuityFactChecker
from fxi.timeline.writing_review import (
    WritingReviewService,
    dependency_hash,
    normalize_dependency_versions,
    review_input_fingerprint,
)
from fxi.core.exceptions import FxiError


router = APIRouter(prefix="/v2", tags=["v2"])

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_INITIAL_KNOWLEDGE_VERSION = "knowledge-v0"
_INITIAL_CHAPTER_VERSION = "chapter-v0"


def _registry(request: Request):
    registry = getattr(request.app.state, "work_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail={"code": "WORK_REGISTRY_UNAVAILABLE", "message": "作品注册表不可用"})
    return registry


def _scope(request: Request, work_id: str, role: str):
    try:
        actor = require_actor(request, role, work_id, registry=_registry(request))
    except HTTPException:
        raise
    except (WorkNotFoundError, SourceNotFoundError) as exc:
        raise HTTPException(status_code=404, detail={"code": "WORK_NOT_FOUND", "message": str(exc)}) from exc
    return actor


def _source_scope(request: Request, work_id: str, source_id: str, role: str):
    actor = _scope(request, work_id, role)
    try:
        _registry(request).require_source(work_id, source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "SOURCE_NOT_FOUND", "message": str(exc)}) from exc
    return actor


def _source_binding(request: Request, work_id: str, source_id: str, role: str):
    """Return the explicit source binding after enforcing actor/work scope."""
    _scope(request, work_id, role)
    try:
        return _registry(request).require_source(work_id, source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SOURCE_NOT_FOUND", "message": str(exc)},
        ) from exc


def _evidence_store(config: FxiConfig) -> EvidenceStore:
    return EvidenceStore(config.sources_dir)

def _config(request: Request) -> FxiConfig:
    return getattr(request.app.state, "config", None) or load_config()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _canonical(value: Any) -> str:
    return canonical_json(value)


def _hash(value: Any) -> str:
    return sha256_hex(value)


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="v2 持久化数据损坏") from exc


def _require_segment(value: str, label: str) -> None:
    if not _SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise HTTPException(status_code=422, detail=f"非法 {label}")


def _source_dir(config: FxiConfig, source_id: str, registered_dir: Optional[str] = None) -> Path:
    _require_segment(source_id, "source_id")
    root = config.sources_dir.resolve()
    relative_value = registered_dir or source_id
    if not isinstance(relative_value, str) or not relative_value.strip():
        raise HTTPException(status_code=422, detail="来源目录绑定无效")
    candidate = Path(relative_value)
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        relative = PurePosixPath(relative_value.replace("\\", "/"))
        if relative.is_absolute() or any(
            part in {"", ".", ".."} or not _SAFE_SEGMENT.fullmatch(part)
            for part in relative.parts
        ):
            raise HTTPException(status_code=422, detail="非法来源路径")
        target = (root.joinpath(*relative.parts)).resolve()
    if target == root or root not in target.parents:
        raise HTTPException(status_code=422, detail="非法来源路径")
    return target


def _load_source(
    config: FxiConfig,
    source_id: str,
    registered_dir: Optional[str] = None,
) -> tuple[Path, dict[str, Any], str]:
    source_dir = _source_dir(config, source_id, registered_dir)
    manifest_path = source_dir / "source.yaml"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="未找到注册来源")
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=503, detail="来源清单不可读") from exc
    version = str(manifest.get("version") or manifest.get("sha256") or "").strip()
    if not version:
        raise HTTPException(status_code=409, detail="来源清单缺少不可变版本")
    return source_dir, manifest, version


def _document_entries(
    source_dir: Path,
    source_id: str,
    chapter_start: Optional[int] = None,
    chapter_end: Optional[int] = None,
) -> list[dict[str, Any]]:
    chapter_dir = source_dir / "chapters"
    files: list[tuple[int, Path]] = []
    if chapter_dir.is_dir():
        for path in sorted(chapter_dir.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
                continue
            match = re.search(r"(\d+)", path.stem)
            chapter_index = int(match.group(1)) if match else len(files) + 1
            files.append((chapter_index, path))
    else:
        raw = source_dir / "raw.txt"
        if raw.is_file():
            files.append((1, raw))

    if chapter_start is not None:
        files = [(idx, path) for idx, path in files if idx >= chapter_start]
    if chapter_end is not None:
        files = [(idx, path) for idx, path in files if idx <= chapter_end]

    entries: list[dict[str, Any]] = []
    for chapter_index, path in sorted(files, key=lambda item: (item[0], item[1].name)):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=503, detail="来源正文不可读") from exc
        entries.append({
            "document_id": path.stem,
            "chapter_index": chapter_index,
            "relative_path": path.relative_to(source_dir).as_posix(),
            "content_hash": _hash(content),
            "char_count": len(content),
        })
    if not entries:
        raise HTTPException(status_code=404, detail="来源没有可快照文档")
    return entries


def _create_immutable_source_snapshot(
    config: FxiConfig,
    source_id: str,
    source_version: str,
    source_dir: Path,
    entries: list[dict[str, Any]],
):
    documents: list[SourceDocumentInput] = []
    for item in entries:
        path = (source_dir / item["relative_path"]).resolve()
        if source_dir.resolve() not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "SOURCE_DOCUMENT_NOT_FOUND", "message": "来源文档不可读"})
        try:
            documents.append(
                SourceDocumentInput.from_file(
                    path,
                    document_id=item["document_id"],
                    chapter_index=item["chapter_index"],
                    relative_path=item["relative_path"],
                )
            )
        except FxiError as exc:
            raise HTTPException(status_code=422, detail={"code": getattr(exc, "code", "SOURCE_INVALID"), "message": str(exc)}) from exc
    try:
        return _evidence_store(config).create_snapshot(
            source_id,
            documents,
            version=source_version,
            metadata={"source_version": source_version},
        )
    except FxiError as exc:
        raise HTTPException(status_code=409, detail={"code": getattr(exc, "code", "SOURCE_SNAPSHOT_CONFLICT"), "message": str(exc)}) from exc


def _snapshot_documents(snapshot: Any) -> list[dict[str, Any]]:
    return [
        {
            "document_id": item.document_id,
            "chapter_index": item.chapter_index,
            "relative_path": item.relative_path,
            "content_hash": item.content_hash,
            "char_count": item.char_count,
        }
        for item in snapshot.documents
    ]

def _snapshot_response(row: Any) -> SourceSnapshotResponseV2:
    documents = [SourceDocumentV2(**item) for item in _json(row["documents_json"])]
    return SourceSnapshotResponseV2(
        work_id=row["work_id"],
        source_id=row["source_id"],
        version=row["version"],
        manifest_hash=row["manifest_hash"],
        documents=documents,
    )


def _get_snapshot(cur: Any, work_id: str, source_id: str, version: str) -> Any:
    row = cur.execute(
        "SELECT * FROM v2_source_snapshots WHERE work_id = ? AND source_id = ? AND version = ?",
        (work_id, source_id, version),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="未找到匹配的来源快照")
    return row


def _current_head(cur: Any, work_id: str) -> tuple[str, str, bool]:
    row = cur.execute(
        "SELECT knowledge_version, chapter_version FROM v2_work_heads WHERE work_id = ?",
        (work_id,),
    ).fetchone()
    if not row:
        return _INITIAL_KNOWLEDGE_VERSION, _INITIAL_CHAPTER_VERSION, False
    return row["knowledge_version"], row["chapter_version"], True


def _validate_approval(
    row: Any,
    *,
    actor_id: Optional[str],
    work_id: str,
    action: str,
    target_id: str,
    target_hash: str,
    expected_version: str,
    source_id: Optional[str] = None,
    source_version: Optional[str] = None,
    candidate_version: Optional[str] = None,
    evaluation_ref: Optional[str] = None,
) -> None:
    if not row:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "未找到人工审批记录"})
    if row["action"] != action or row["target_id"] != target_id or row["target_hash"] != target_hash or row["expected_version"] != expected_version:
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_MISMATCH", "message": "人工审批与请求不匹配"})
    if row["work_id"] != work_id or not row["actor_id"] or row["role"] != "approver":
        raise HTTPException(status_code=403, detail={"code": "AUTH_FORBIDDEN", "message": "审批主体或作品范围不匹配"})
    for field, expected in (
        ("source_id", source_id),
        ("source_version", source_version),
        ("candidate_version", candidate_version),
        ("evaluation_ref", evaluation_ref),
    ):
        if expected is not None and row[field] != expected:
            raise HTTPException(status_code=409, detail={"code": "APPROVAL_MISMATCH", "message": f"审批 {field} 与请求不匹配"})
    if actor_id is not None and row["actor_id"] != actor_id:
        raise HTTPException(status_code=403, detail={"code": "AUTH_FORBIDDEN", "message": "审批主体或作品范围不匹配"})
    expires_at = row["expires_at"]
    try:
        expires_at_value = datetime.fromisoformat(expires_at) if expires_at else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_INVALID", "message": "approval expiry is invalid"}) from exc
    if expires_at_value is None or expires_at_value.tzinfo is None or expires_at_value <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_EXPIRED", "message": "人工审批已过期"})
    if row["validity"] != "VALID" or row["consumed_by"]:
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_CONSUMED", "message": "人工审批不可用"})

@router.post("/sources/{source_id}/snapshots", response_model=SourceSnapshotResponseV2)
def create_source_snapshot(source_id: str, req: SourceSnapshotRequestV2, request: Request):
    if req.source_id != source_id:
        raise HTTPException(status_code=422, detail={"code": "INCOMPLETE_INPUT", "message": "路径 source_id 与请求体不一致"})
    if req.chapter_start is not None and req.chapter_end is not None and req.chapter_start > req.chapter_end:
        raise HTTPException(status_code=422, detail={"code": "INCOMPLETE_INPUT", "message": "chapter_start 不得大于 chapter_end"})
    binding = _source_binding(request, req.work_id, source_id, "writer")
    config = _config(request)
    source_dir, _manifest, source_version = _load_source(config, source_id, binding.source_dir)
    if binding.source_version and binding.source_version != source_version:
        raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT", "message": "注册来源版本已变化"})
    if req.expected_source_version != source_version:
        raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT", "message": "来源版本已变化"})
    entries = _document_entries(source_dir, source_id, req.chapter_start, req.chapter_end)
    immutable = _create_immutable_source_snapshot(config, source_id, source_version, source_dir, entries)
    documents = _snapshot_documents(immutable)
    manifest_hash = immutable.manifest_hash
    documents_json = _canonical(documents)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        existing = cur.execute(
            "SELECT * FROM v2_source_snapshots WHERE work_id = ? AND source_id = ? AND idempotency_key = ?",
            (req.work_id, source_id, req.idempotency_key),
        ).fetchone()
        if existing:
            if existing["version"] != source_version or existing["manifest_hash"] != manifest_hash:
                raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT", "message": "同幂等键绑定了不同快照"})
            return _snapshot_response(existing)
        same_version = cur.execute(
            "SELECT * FROM v2_source_snapshots WHERE source_id = ? AND version = ?",
            (source_id, source_version),
        ).fetchone()
        if same_version and same_version["work_id"] != req.work_id:
            raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT", "message": "来源版本已绑定其他作品"})
        snapshot_id = _id("snap")
        cur.execute(
            """INSERT INTO v2_source_snapshots
            (snapshot_id, work_id, source_id, version, manifest_hash, documents_json, object_root, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, req.work_id, source_id, source_version, manifest_hash, documents_json, f"objects/{source_id}/{source_version}", req.idempotency_key, _now()),
        )
        row = cur.execute("SELECT * FROM v2_source_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    return _snapshot_response(row)


@router.get(
    "/sources/{source_id}/snapshots/{version}/documents/{document_id}",
    response_model=SourceDocumentResponseV2,
)
def get_source_document(source_id: str, version: str, document_id: str, request: Request):
    _require_segment(version, "version")
    _require_segment(document_id, "document_id")
    config = _config(request)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        snapshot = cur.execute(
            "SELECT * FROM v2_source_snapshots WHERE source_id = ? AND version = ?",
            (source_id, version),
        ).fetchone()
    if not snapshot:
        raise HTTPException(status_code=404, detail="未找到来源快照")
    _source_scope(request, snapshot["work_id"], source_id, "reader")
    _validate_snapshot_object(config, snapshot)
    document = next((item for item in _json(snapshot["documents_json"]) if item["document_id"] == document_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail="未找到快照文档")
    try:
        stored = _evidence_store(config).read_document(
            source_id,
            version,
            document_id,
        )
    except (FxiError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "SOURCE_SNAPSHOT_UNAVAILABLE", "message": "来源快照对象不可读"},
        ) from exc
    if stored.document_hash != document["content_hash"]:
        raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "快照对象哈希不一致"})
    return SourceDocumentResponseV2(
        work_id=snapshot["work_id"],
        source_id=source_id,
        source_version=version,
        document_id=document_id,
        content=stored.content,
        document_hash=stored.document_hash,
        excerpt_hash=stored.excerpt_hash,
        start_char=stored.start_char,
        end_char=stored.end_char,
    )


def _read_snapshot(config: FxiConfig, work_id: str, source_id: str, version: str) -> Any:
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        row = _get_snapshot(cur, work_id, source_id, version)
    _validate_snapshot_object(config, row)
    return row


def _validate_snapshot_object(config: FxiConfig, row: Any) -> Any:
    """Verify the database snapshot row and every immutable document object."""
    source_id = row["source_id"]
    version = row["version"]
    try:
        immutable = _evidence_store(config).read_snapshot(source_id, version)
    except (FxiError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "SOURCE_SNAPSHOT_UNAVAILABLE", "message": "来源快照对象不可用"},
        ) from exc
    stored_documents = _json(row["documents_json"])
    immutable_documents = _snapshot_documents(immutable)
    if (
        row["manifest_hash"] != immutable.manifest_hash
        or _canonical(stored_documents) != _canonical(immutable_documents)
        or row["object_root"] not in (None, f"objects/{source_id}/{version}")
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "EVIDENCE_MISMATCH", "message": "数据库快照记录与不可变来源对象不一致"},
        )
    try:
        for document in immutable.documents:
            stored_document = _evidence_store(config).read_document(
                source_id,
                version,
                document.document_id,
            )
            if stored_document.document_hash != document.content_hash:
                raise EvidenceMismatchError("来源文档对象哈希不一致")
    except (FxiError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "SOURCE_SNAPSHOT_UNAVAILABLE", "message": "来源快照文档对象不可用"},
        ) from exc
    return row


def _validate_knowledge_head(cur: Any, work_id: str, requested_version: str) -> str:
    """Require a version reference to match the current work head or baseline."""
    current_version, _chapter_version, has_head = _current_head(cur, work_id)
    expected = current_version if has_head else _INITIAL_KNOWLEDGE_VERSION
    if requested_version != expected:
        raise HTTPException(
            status_code=409,
            detail={"code": "VERSION_CONFLICT", "message": "知识库版本已变化"},
        )
    return expected


def _style_candidate(config: FxiConfig, work_id: str, version: str) -> Any:
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        row = cur.execute(
            "SELECT * FROM v2_style_candidates WHERE work_id = ? AND version = ?",
            (work_id, version),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="未找到风格包版本")
    return row


def _style_payload(row: Any) -> dict[str, Any]:
    payload = _json(row["payload_json"])
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="风格包持久化数据损坏")
    return {
        "candidate_id": row["candidate_id"],
        "work_id": row["work_id"],
        "version": row["version"],
        "package_hash": row["package_hash"],
        "status": row["status"],
        "package": payload.get("package", payload),
        "learning_run_ref": payload.get("learning_run_ref"),
        "evaluation_ref": payload.get("evaluation_ref"),
    }


def _style_context(config: FxiConfig, req: WritingContextRequestV2) -> Optional[dict[str, Any]]:
    if req.style_selection == "none":
        return None
    row = _style_candidate(config, req.work_id, req.style_package_version or "")
    if req.style_selection == "approved":
        client = DatabaseClient(config.sqlite_path)
        with client.transaction() as cur:
            head = cur.execute(
                "SELECT active_version FROM v2_style_heads WHERE work_id = ?",
                (req.work_id,),
            ).fetchone()
        if not head or head["active_version"] != req.style_package_version or row["status"] != "APPROVED":
            raise HTTPException(status_code=409, detail="请求的风格包尚未晋升为正式版本")
    return _style_payload(row)


def _object_items(items: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [item if isinstance(item, dict) else {field: str(item)} for item in items]


def _context_style_fields(style: Optional[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not style:
        return [], [], []
    package = style.get("package") if isinstance(style.get("package"), dict) else {}
    rules = package.get("style_rules", package.get("rules", []))
    examples = package.get("style_examples", package.get("positive_exemplars", package.get("examples", [])))
    exact = package.get("exact_text_rules", package.get("negative_rules", []))
    return _object_items(rules, "rule"), _object_items(examples, "example"), _object_items(exact, "rule")


@router.post("/writing/context", response_model=WritingContextResponseV2)
def assemble_writing_context(req: WritingContextRequestV2, request: Request):
    _source_scope(request, req.work_id, req.source_id, "reader")
    config = _config(request)
    _read_snapshot(config, req.work_id, req.source_id, req.source_version)
    style = _style_context(config, req)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        stored_knowledge_version, _stored_chapter_version, _has_head = _current_head(cur, req.work_id)
        if req.knowledge_version:
            _validate_knowledge_head(cur, req.work_id, req.knowledge_version)
        event_rows = cur.execute(
            "SELECT event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status FROM causal_events WHERE work_id = ? AND narrative_order <= ? AND COALESCE(status, '') != 'invalidated' ORDER BY narrative_order, event_id",
            (req.work_id, req.current_narrative_order),
        ).fetchall()
    knowledge_version = req.knowledge_version or stored_knowledge_version
    try:
        context = ContextPruner(config).assemble_writing_context(
            work_id=req.work_id,
            source_id=req.source_id,
            source_version=req.source_version,
            knowledge_version=knowledge_version,
            style_package_version=req.style_package_version,
            style_selection=req.style_selection,
            style_package=style,
            mode=req.mode,
            purpose=req.purpose,
            pov_character_id=req.pov_character_id,
            current_narrative_order=req.current_narrative_order,
            scene_beat=req.scene_beat,
            objective=req.objective,
            participants=req.participants,
            canon_events=[dict(row) for row in event_rows],
            budget=req.budget,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CONTEXT_ASSEMBLY_FAILED",
                "message": f"writing context assembly unavailable ({type(exc).__name__})",
            },
        ) from exc
    context.pop("versions", None)
    context.pop("claims", None)
    context.pop("causal_events", None)
    return WritingContextResponseV2(**context)
def _review_finding(violation: Any) -> dict[str, Any]:
    return {
        "severity": getattr(violation, "severity", "warning"),
        "issue_type": getattr(violation, "issue_type", "review_finding"),
        "message": getattr(violation, "message", "审查发现问题"),
        "line_snippet": getattr(violation, "line_snippet", None),
        "suggestion": getattr(violation, "suggestion", None),
    }


def _continuity_check(config: FxiConfig, req: WritingReviewRequestV2) -> ReviewCheckV2:
    try:
        violations = ContinuityFactChecker(config).check_continuity(
            work_id=req.work_id,
            chapter_index=req.chapter_index,
            draft_text=req.text,
            planned_events=req.event_ids,
            use_llm=False,
        )
    except Exception as exc:
        return ReviewCheckV2(
            check_id="continuity",
            status="INCOMPLETE",
            input_text_hash=req.text_hash,
            findings=[],
            coverage_spans=[(0, len(req.text))],
            error_code=f"CONTINUITY_CHECK_FAILED:{type(exc).__name__}",
        )
    findings = [_review_finding(item) for item in violations]
    if any(item["severity"] == "error" for item in findings):
        status = "REJECTED"
    elif findings:
        status = "NEEDS_REVISION"
    else:
        status = "PASSED"
    return ReviewCheckV2(
        check_id="continuity",
        status=status,
        input_text_hash=req.text_hash,
        findings=findings,
    )


def _incomplete_review_check(
    check_id: str,
    input_text_hash: str,
    error_code: str,
    finding_code: Optional[str] = None,
) -> ReviewCheckV2:
    return ReviewCheckV2(
        check_id=check_id,
        status="INCOMPLETE",
        input_text_hash=input_text_hash,
        error_code=error_code,
        findings=([{"code": finding_code}] if finding_code else []),
    )


class _SemanticReviewerAdapter:
    """Adapt the app-owned semantic reviewer to the typed v2 review boundary."""

    reviewer_id = "fxi-semantic-reviewer"
    reviewer_version = "fxi-semantic-review-v2"

    def __init__(self, reviewer: Any):
        self.reviewer = reviewer
        configured_id = getattr(reviewer, "reviewer_id", None)
        configured_version = getattr(reviewer, "reviewer_version", None)
        if isinstance(configured_id, str) and configured_id.strip():
            self.reviewer_id = configured_id
        if isinstance(configured_version, str) and configured_version.strip():
            self.reviewer_version = configured_version

    def review(self, request: Mapping[str, Any]) -> Any:
        typed_request = WritingReviewRequestV2.model_validate(
            {
                key: request[key]
                for key in (
                    "work_id",
                    "source_id",
                    "source_version",
                    "knowledge_version",
                    "style_package_version",
                    "mode",
                    "scene_id",
                    "chapter_index",
                    "text",
                    "text_hash",
                    "plan_hash",
                    "context_hash",
                    "event_ids",
                    "required_checks",
                    "sacred_whitelist",
                )
                if key in request
            }
        )
        review_fn = getattr(self.reviewer, "review", None)
        if not callable(review_fn):
            review_fn = self.reviewer if callable(self.reviewer) else None
        if review_fn is None:
            raise TypeError("semantic reviewer must provide review")
        raw_result = review_fn(typed_request)
        if isinstance(raw_result, ReviewCheckV2):
            return {"checks": [raw_result.model_dump()]}
        return raw_result


def _semantic_check(request: Request, req: WritingReviewRequestV2) -> ReviewCheckV2:
    """Run the typed semantic adapter and fail closed on every invalid result."""
    state = getattr(getattr(request, "app", None), "state", None)
    reviewer = getattr(state, "semantic_reviewer", None)
    if reviewer is None:
        return _incomplete_review_check(
            "semantic_review",
            req.text_hash,
            "MODEL_REVIEWER_NOT_CONFIGURED",
            "MODEL_REVIEWER_NOT_CONFIGURED",
        )
    try:
        adapter = _SemanticReviewerAdapter(reviewer)
        decision = WritingReviewService(
            reviewer=adapter,
            reviewer_id=adapter.reviewer_id,
            reviewer_version=adapter.reviewer_version,
        ).review(
            {
                "work_id": req.work_id,
                "source_id": req.source_id,
                "source_version": req.source_version,
                "knowledge_version": req.knowledge_version,
                "style_package_version": req.style_package_version,
                "mode": req.mode,
                "scene_id": req.scene_id,
                "chapter_index": req.chapter_index,
                "text": req.text,
                "text_hash": req.text_hash,
                "plan_hash": req.plan_hash,
                "context_hash": req.context_hash,
                "event_ids": req.event_ids,
                "required_checks": ["semantic_review"],
                "sacred_whitelist": req.sacred_whitelist,
                "text_length": len(req.text),
            }
        )
    except Exception as exc:
        return _incomplete_review_check(
            "semantic_review",
            req.text_hash,
            f"SEMANTIC_REVIEW_FAILED:{type(exc).__name__}",
            "SEMANTIC_REVIEW_FAILED",
        )

    check = next((item for item in decision.checks if item.check_id == "semantic_review"), None)
    if check is None:
        return _incomplete_review_check(
            "semantic_review",
            req.text_hash,
            "SEMANTIC_REVIEW_CHECK_MISSING",
            "SEMANTIC_REVIEW_CHECK_MISSING",
        )

    if check.input_text_hash != req.text_hash:
        return _incomplete_review_check(
            "semantic_review",
            req.text_hash,
            "SEMANTIC_REVIEW_HASH_MISMATCH",
            "SEMANTIC_REVIEW_HASH_MISMATCH",
        )
    if check.status not in {"PASSED", "NEEDS_REVISION", "REJECTED", "INCOMPLETE"}:
        return _incomplete_review_check(
            "semantic_review",
            req.text_hash,
            "SEMANTIC_REVIEW_INVALID_STATUS",
            "SEMANTIC_REVIEW_INVALID_STATUS",
        )
    payload = check.to_dict()
    if decision.status != "PASSED" and check.status == "PASSED":
        payload["status"] = "INCOMPLETE"
        payload["error_code"] = "SEMANTIC_REVIEW_INCOMPLETE"
        payload["findings"] = [
            {"code": item.get("code", "SEMANTIC_REVIEW_INCOMPLETE")}
            for item in decision.blocking_findings
        ] or [{"code": "SEMANTIC_REVIEW_INCOMPLETE"}]
    try:
        return ReviewCheckV2.model_validate(payload)
    except Exception as exc:
        return _incomplete_review_check(
            "semantic_review",
            req.text_hash,
            f"SEMANTIC_REVIEW_RESULT_INVALID:{type(exc).__name__}",
            "SEMANTIC_REVIEW_RESULT_INVALID",
        )


def _review_dependency_versions(
    req: WritingReviewRequestV2,
    style_candidate: Optional[Any],
) -> dict[str, Optional[str]]:
    return normalize_dependency_versions(
        {
            "source_version": req.source_version,
            "knowledge_version": req.knowledge_version,
            "plan_hash": req.plan_hash,
            "context_hash": req.context_hash,
            "style_package_version": req.style_package_version,
            "style_package_hash": style_candidate["package_hash"] if style_candidate else None,
        }
    )


def _review_fingerprint(
    req: WritingReviewRequestV2,
    required_checks: list[str],
    dependency_versions: Mapping[str, Optional[str]],
) -> str:
    return review_input_fingerprint(
        {
            **req.model_dump(),
            "required_checks": required_checks,
            "event_ids": list(req.event_ids),
            "coverage_required": [],
            "sacred_whitelist": list(req.sacred_whitelist),
            "text_length": len(req.text),
            "dependency_versions": dependency_versions,
        }
    )


def _persist_review(
    config: FxiConfig,
    req: WritingReviewRequestV2,
    report: ReviewReportV2,
    actor_id: str,
    *,
    required_checks: list[str],
    dependency_versions: Mapping[str, Optional[str]],
    input_fingerprint: str,
    reviewer_id: Optional[str],
    reviewer_version: Optional[str],
) -> None:
    payload = report.model_dump()
    payload.update({
        "work_id": req.work_id,
        "source_id": req.source_id,
        "source_version": req.source_version,
        "chapter_index": req.chapter_index,
        "actor_id": actor_id,
        "required_checks": list(required_checks),
        "event_ids": list(req.event_ids),
        "coverage_required": [],
        "sacred_whitelist": list(req.sacred_whitelist),
        "text_length": len(req.text),
        "dependency_versions": dict(dependency_versions),
        "dependency_hash": dependency_hash(dependency_versions),
        "input_fingerprint": input_fingerprint,
        "style_package_version": req.style_package_version,
        "mode": req.mode,
        "scene_id": req.scene_id,
        "reviewer_id": reviewer_id,
        "reviewer_version": reviewer_version,
    })
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        cur.execute(
            """INSERT INTO v2_reviews
            (report_id, work_id, source_id, source_version, text_hash, plan_hash, context_hash, knowledge_version, status, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.report_id,
                req.work_id,
                req.source_id,
                req.source_version,
                req.text_hash,
                req.plan_hash,
                req.context_hash,
                req.knowledge_version,
                report.overall_status,
                _canonical(payload),
                report.created_at,
            ),
        )


def _review_fingerprint_from_payload(payload: Mapping[str, Any]) -> str:
    return review_input_fingerprint(
        {
            "work_id": payload.get("work_id"),
            "source_id": payload.get("source_id"),
            "source_version": payload.get("source_version"),
            "knowledge_version": payload.get("knowledge_version"),
            "text_hash": payload.get("text_hash"),
            "plan_hash": payload.get("plan_hash"),
            "context_hash": payload.get("context_hash"),
            "required_checks": payload.get("required_checks"),
            "event_ids": payload.get("event_ids", []),
            "coverage_required": payload.get("coverage_required", []),
            "sacred_whitelist": payload.get("sacred_whitelist", []),
            "text_length": payload.get("text_length"),
            "dependency_versions": payload.get("dependency_versions", {}),
            "style_package_version": payload.get("style_package_version"),
            "mode": payload.get("mode"),
            "scene_id": payload.get("scene_id"),
            "chapter_index": payload.get("chapter_index"),
        }
    )


def _validate_review_binding(
    row: Any,
    *,
    work_id: str,
    source_id: str,
    source_version: str,
    text_hash: str,
    chapter_index: int,
    expected_knowledge_version: Optional[str] = None,
    expected_dependency_versions: Optional[Mapping[str, Optional[str]]] = None,
) -> dict[str, Any]:
    if not row or row["status"] != "PASSED":
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "final_review_ref must point to a PASSED review for the same work"},
        )
    payload = _json(row["payload_json"])
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail={"code": "REVIEW_DATA_CORRUPTED", "message": "stored review payload is invalid"})
    if (
        row["plan_hash"] != payload.get("plan_hash")
        or row["context_hash"] != payload.get("context_hash")
        or row["knowledge_version"] != payload.get("knowledge_version")
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "stored review dependency hashes are inconsistent"},
        )
    if (
        row["source_id"] != source_id
        or row["source_version"] != source_version
        or row["text_hash"] != text_hash
        or payload.get("source_id") != source_id
        or payload.get("source_version") != source_version
        or payload.get("text_hash") != text_hash
        or payload.get("chapter_id") != f"{work_id}:chapter:{chapter_index}"
        or payload.get("overall_status") != "PASSED"
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "VERSION_CONFLICT", "message": "review is not bound to the requested chapter, source, or text"},
        )
    if expected_knowledge_version is not None and row["knowledge_version"] != expected_knowledge_version:
        raise HTTPException(
            status_code=409,
            detail={"code": "STALE_REVIEW", "message": "review knowledge version is stale"},
        )
    required_checks = payload.get("required_checks")
    if isinstance(required_checks, (str, bytes)) or not isinstance(required_checks, list) or not required_checks:
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review is missing required_checks"},
        )
    if any(not isinstance(check_id, str) or not check_id.strip() for check_id in required_checks):
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review required_checks is invalid"},
        )
    checks = payload.get("checks")
    if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks):
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review checks are invalid"},
        )
    check_ids = [check.get("check_id") for check in checks]
    if any(not isinstance(check_id, str) or not check_id.strip() for check_id in check_ids):
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review checks contain an invalid check_id"},
        )
    if len(set(check_ids)) != len(check_ids):
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review checks contain duplicate check_id"},
        )
    if any(check.get("status") != "PASSED" for check in checks):
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review contains a non-passing check"},
        )
    checks_by_id = {check["check_id"]: check for check in checks}
    missing = [check_id for check_id in required_checks if check_id not in checks_by_id]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review is missing required checks", "details": {"checks": missing}},
        )
    if any(checks_by_id[check_id].get("status") != "PASSED" for check_id in required_checks):
        raise HTTPException(
            status_code=409,
            detail={"code": "INCOMPLETE_INPUT", "message": "review contains a non-passing required check"},
        )
    dependencies = payload.get("dependency_versions")
    try:
        normalized_dependencies = normalize_dependency_versions(dependencies)
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "review dependency_versions is invalid"},
        ) from exc
    if payload.get("dependency_hash") != dependency_hash(normalized_dependencies):
        raise HTTPException(
            status_code=409,
            detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "review dependency_hash is invalid"},
        )
    if (
        normalized_dependencies.get("source_version") != source_version
        or normalized_dependencies.get("knowledge_version") != row["knowledge_version"]
        or normalized_dependencies.get("plan_hash") != row["plan_hash"]
        or normalized_dependencies.get("context_hash") != row["context_hash"]
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "review dependency_versions are not bound"},
        )
    if expected_dependency_versions is not None:
        try:
            expected_dependencies = normalize_dependency_versions(expected_dependency_versions)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "requested dependency_versions is invalid"},
            ) from exc
        mismatches = {
            key: (expected_dependencies[key], normalized_dependencies.get(key))
            for key in expected_dependencies
            if normalized_dependencies.get(key) != expected_dependencies[key]
        }
        if mismatches:
            raise HTTPException(
                status_code=409,
                detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "review dependency_versions do not match proposal", "details": {"dependencies": sorted(mismatches)}},
            )
    expected_fingerprint = _review_fingerprint_from_payload(payload)
    if payload.get("input_fingerprint") != expected_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={"code": "REVIEW_DEPENDENCY_MISMATCH", "message": "review input_fingerprint is invalid"},
        )
    return payload


@router.post("/writing/review", response_model=ReviewReportV2)
def review_writing(req: WritingReviewRequestV2, request: Request):
    actor = _source_scope(request, req.work_id, req.source_id, "reviewer")
    if _hash(req.text) != req.text_hash:
        raise HTTPException(status_code=409, detail="正文 text_hash 不匹配")
    config = _config(request)
    _read_snapshot(config, req.work_id, req.source_id, req.source_version)
    style_candidate = None
    if req.style_package_version:
        style_candidate = _style_candidate(config, req.work_id, req.style_package_version)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        _validate_knowledge_head(cur, req.work_id, req.knowledge_version)

    requested = list(dict.fromkeys(req.required_checks or ["continuity"]))
    effective_required = list(dict.fromkeys([*requested, "semantic_review"]))
    checks: list[ReviewCheckV2] = []
    for check_id in requested:
        if check_id == "continuity":
            checks.append(_continuity_check(config, req))
        elif check_id == "semantic_review":
            checks.append(_semantic_check(request, req))
        elif check_id == "sacred_whitelist":
            preserved = all(item in req.text for item in req.sacred_whitelist)
            checks.append(ReviewCheckV2(
                check_id=check_id,
                status="PASSED" if preserved else "NEEDS_REVISION",
                input_text_hash=req.text_hash,
                sacred_whitelist_preserved=preserved,
                coverage_spans=[(0, len(req.text))],
                findings=[] if preserved else [{"code": "SACRED_WHITELIST_CHANGED"}],
            ))
        elif check_id in {"action_density", "style"}:
            checks.append(ReviewCheckV2(
                check_id=check_id,
                status="INCOMPLETE",
                input_text_hash=req.text_hash,
                error_code=f"{check_id.upper()}_CHECK_NOT_CONFIGURED",
                findings=[{"code": f"{check_id.upper()}_CHECK_NOT_CONFIGURED"}],
            ))
        else:
            checks.append(ReviewCheckV2(
                check_id=check_id,
                status="INCOMPLETE",
                input_text_hash=req.text_hash,
                error_code="UNSUPPORTED_REVIEW_CHECK",
            ))
    if "semantic_review" not in requested:
        checks.append(_semantic_check(request, req))
    if any(item.status == "REJECTED" for item in checks):
        overall = "REJECTED"
    elif any(item.status == "NEEDS_REVISION" for item in checks):
        overall = "NEEDS_REVISION"
    elif any(item.status == "INCOMPLETE" for item in checks):
        overall = "INCOMPLETE"
    else:
        overall = "PASSED"
    blocking = [
        {"check_id": item.check_id, "error_code": item.error_code}
        for item in checks
        if item.status == "INCOMPLETE" or item.error_code
    ]
    report = ReviewReportV2(
        report_id=_id("review"),
        chapter_id=f"{req.work_id}:chapter:{req.chapter_index}",
        text_hash=req.text_hash,
        plan_hash=req.plan_hash,
        context_hash=req.context_hash,
        knowledge_version=req.knowledge_version,
        checks=checks,
        overall_status=overall,
        blocking_findings=blocking,
        created_at=_now(),
    )
    reviewer = getattr(getattr(request, "app", None).state, "semantic_reviewer", None)
    reviewer_adapter = _SemanticReviewerAdapter(reviewer) if reviewer is not None else None
    dependencies = _review_dependency_versions(req, style_candidate)
    _persist_review(
        config,
        req,
        report,
        actor.actor_id,
        required_checks=effective_required,
        dependency_versions=dependencies,
        input_fingerprint=_review_fingerprint(req, effective_required, dependencies),
        reviewer_id=reviewer_adapter.reviewer_id if reviewer_adapter else None,
        reviewer_version=reviewer_adapter.reviewer_version if reviewer_adapter else None,
    )
    return report


@router.get("/style/packages/{version}")
def get_style_package(version: str, request: Request, work_id: Optional[str] = None):
    _require_segment(version, "style package version")
    if not work_id:
        raise HTTPException(status_code=422, detail={"code": "INCOMPLETE_INPUT", "message": "work_id 是必需的"})
    _scope(request, work_id, "reader")
    config = _config(request)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        if work_id:
            row = cur.execute(
                "SELECT * FROM v2_style_candidates WHERE work_id = ? AND version = ?",
                (work_id, version),
            ).fetchone()
        else:
            rows = cur.execute(
                "SELECT * FROM v2_style_candidates WHERE version = ? ORDER BY created_at",
                (version,),
            ).fetchall()
            if len(rows) > 1:
                raise HTTPException(status_code=409, detail="风格包版本属于多个作品，请提供 work_id")
            row = rows[0] if rows else None
    if not row:
        raise HTTPException(status_code=404, detail="未找到风格包版本")
    return _style_payload(row)


@router.post("/style/candidates")
def create_style_candidate(req: StyleCandidateRequestV2, request: Request):
    _scope(request, req.work_id, "writer")
    if _hash(req.package) != req.package_hash:
        raise HTTPException(status_code=409, detail="风格包 package_hash 不匹配")
    config = _config(request)
    stored_payload = {
        "package": req.package,
        "learning_run_ref": req.learning_run_ref,
        "evaluation_ref": req.evaluation_ref,
    }
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        existing = cur.execute(
            "SELECT * FROM v2_style_candidates WHERE work_id = ? AND idempotency_key = ?",
            (req.work_id, req.idempotency_key),
        ).fetchone()
        if existing:
            if (
                existing["version"] != req.version
                or existing["package_hash"] != req.package_hash
                or _canonical(_json(existing["payload_json"])) != _canonical(stored_payload)
            ):
                raise HTTPException(status_code=409, detail="同幂等键绑定了不同风格候选")
            return {**_style_payload(existing), "idempotent_replay": True}
        same_version = cur.execute(
            "SELECT candidate_id FROM v2_style_candidates WHERE work_id = ? AND version = ?",
            (req.work_id, req.version),
        ).fetchone()
        if same_version:
            raise HTTPException(status_code=409, detail="风格候选版本已存在")
        candidate_id = _id("style_candidate")
        cur.execute(
            """INSERT INTO v2_style_candidates
            (candidate_id, work_id, version, package_hash, status, payload_json, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, 'EVALUATION_CANDIDATE', ?, ?, ?)""",
            (candidate_id, req.work_id, req.version, req.package_hash, _canonical(stored_payload), req.idempotency_key, _now()),
        )
        row = cur.execute("SELECT * FROM v2_style_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
    return {**_style_payload(row), "idempotent_replay": False}


@router.post("/style/promotions")
def promote_style(req: StylePromotionRequestV2, request: Request):
    actor = _scope(request, req.work_id, "approver")
    # Actor authentication and role scope are enforced by the caller-specific helper.
    config = _config(request)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        existing = cur.execute(
            "SELECT * FROM v2_style_promotions WHERE work_id = ? AND idempotency_key = ?",
            (req.work_id, req.idempotency_key),
        ).fetchone()
        if existing:
            if (
                existing["candidate_id"] != req.candidate_id
                or existing["candidate_version"] != req.candidate_version
                or existing["candidate_hash"] != req.candidate_hash
                or existing["approval_id"] != req.approval_id
            ):
                raise HTTPException(status_code=409, detail="同幂等键绑定了不同晋升请求")
            replay_approval = cur.execute(
                "SELECT actor_id, work_id, action, target_id, target_hash FROM v2_approvals WHERE approval_id = ?",
                (existing["approval_id"],),
            ).fetchone()
            if (
                not replay_approval
                or replay_approval["actor_id"] != actor.actor_id
                or replay_approval["work_id"] != req.work_id
                or replay_approval["action"] != "style_promotion"
                or replay_approval["target_id"] != req.candidate_id
                or replay_approval["target_hash"] != req.candidate_hash
            ):
                raise HTTPException(status_code=403, detail={"code": "AUTH_FORBIDDEN", "message": "晋升重放主体不匹配"})
            return {
                "promotion_id": existing["promotion_id"],
                "candidate_id": existing["candidate_id"],
                "active_version": existing["candidate_version"],
                "approval_id": existing["approval_id"],
                "idempotent_replay": True,
            }
        candidate = cur.execute(
            "SELECT * FROM v2_style_candidates WHERE candidate_id = ? AND work_id = ?",
            (req.candidate_id, req.work_id),
        ).fetchone()
        if not candidate:
            raise HTTPException(status_code=404, detail="未找到风格候选")
        if candidate["version"] != req.candidate_version or candidate["package_hash"] != req.candidate_hash:
            raise HTTPException(status_code=409, detail="风格候选版本或哈希不匹配")
        if candidate["status"] != "EVALUATION_CANDIDATE":
            raise HTTPException(status_code=409, detail="风格候选状态不可晋升")
        head = cur.execute(
            "SELECT active_version FROM v2_style_heads WHERE work_id = ?",
            (req.work_id,),
        ).fetchone()
        current_version = head["active_version"] if head else "style-v0"
        expected_version = req.expected_active_version or current_version
        if current_version != expected_version:
            raise HTTPException(status_code=409, detail="正式风格版本已变化")
        approval = cur.execute(
            "SELECT * FROM v2_approvals WHERE approval_id = ?",
            (req.approval_id,),
        ).fetchone()
        candidate_payload = _json(candidate["payload_json"])
        candidate_package = candidate_payload.get("package") if isinstance(candidate_payload, dict) else {}
        candidate_source_id = candidate_package.get("source_id") if isinstance(candidate_package, dict) else None
        candidate_source_version = candidate_package.get("source_version") if isinstance(candidate_package, dict) else None
        candidate_evaluation_ref = candidate_payload.get("evaluation_ref") if isinstance(candidate_payload, dict) else None
        _validate_approval(
            approval,
            actor_id=actor.actor_id,
            work_id=req.work_id,
            action="style_promotion",
            target_id=req.candidate_id,
            target_hash=req.candidate_hash,
            expected_version=expected_version,
            source_id=candidate_source_id,
            source_version=candidate_source_version,
            candidate_version=req.candidate_version,
            evaluation_ref=candidate_evaluation_ref,
        )
        promotion_id = _id("style_promotion")
        cur.execute(
            """INSERT INTO v2_style_promotions
            (promotion_id, work_id, candidate_id, candidate_version, candidate_hash, approval_id, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (promotion_id, req.work_id, req.candidate_id, req.candidate_version, req.candidate_hash, req.approval_id, req.idempotency_key, _now()),
        )
        cur.execute(
            "UPDATE v2_style_candidates SET status = 'APPROVED' WHERE candidate_id = ?",
            (req.candidate_id,),
        )
        cur.execute(
            """INSERT INTO v2_style_heads (work_id, active_version, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(work_id) DO UPDATE SET active_version = excluded.active_version, updated_at = excluded.updated_at""",
            (req.work_id, req.candidate_version, _now()),
        )
        cur.execute(
            "UPDATE v2_approvals SET consumed_by = ? WHERE approval_id = ? AND consumed_by IS NULL",
            (promotion_id, req.approval_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="人工审批消费失败")
    return {
        "promotion_id": promotion_id,
        "candidate_id": req.candidate_id,
        "active_version": req.candidate_version,
        "approval_id": req.approval_id,
        "idempotent_replay": False,
    }


@router.post("/approvals", response_model=ApprovalResponseV2)
def create_approval(req: ApprovalRequestV2, request: Request):
    if req.action == "rollback":
        raise HTTPException(
            status_code=409,
            detail={"code": "UNSUPPORTED_APPROVAL_ACTION", "message": "rollback approval has no v2 handler"},
        )
    config = _config(request)
    client = DatabaseClient(config.sqlite_path)
    work_id = req.work_id
    if not work_id:
        with client.transaction() as cur:
            target = cur.execute(
                "SELECT work_id FROM v2_style_candidates WHERE candidate_id = ?",
                (req.target_id,),
            ).fetchone()
            if target is None:
                target = cur.execute(
                    "SELECT work_id FROM v2_proposals WHERE proposal_id = ?",
                    (req.target_id,),
                ).fetchone()
            work_id = target["work_id"] if target else None
    if not work_id:
        raise HTTPException(status_code=404, detail={"code": "WORK_NOT_FOUND", "message": "审批目标未绑定作品"})
    actor = _scope(request, work_id, "approver")
    with client.transaction() as cur:
        approval_source_id: Optional[str] = None
        approval_source_version: Optional[str] = None
        approval_candidate_version: Optional[str] = None
        approval_evaluation_ref: Optional[str] = None
        if req.action == "style_promotion":
            target = cur.execute(
                "SELECT package_hash FROM v2_style_candidates WHERE candidate_id = ? AND work_id = ?",
                (req.target_id, work_id),
            ).fetchone()
            if not target:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "未找到风格候选"})
            if target["package_hash"] != req.target_hash:
                raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "审批 target_hash 与候选不匹配"})
            candidate_row = cur.execute(
                "SELECT version, payload_json FROM v2_style_candidates WHERE candidate_id = ? AND work_id = ?",
                (req.target_id, work_id),
            ).fetchone()
            approval_candidate_version = candidate_row["version"]
            candidate_payload = _json(candidate_row["payload_json"])
            package = candidate_payload.get("package") if isinstance(candidate_payload, dict) else {}
            if isinstance(package, dict):
                approval_source_id = package.get("source_id")
                approval_source_version = package.get("source_version")
            approval_evaluation_ref = candidate_payload.get("evaluation_ref") if isinstance(candidate_payload, dict) else None
        elif req.action == "chapter_commit":
            target = cur.execute(
                "SELECT proposal_hash, payload_json, source_id FROM v2_proposals WHERE proposal_id = ? AND work_id = ?",
                (req.target_id, work_id),
            ).fetchone()
            if not target:
                raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "未找到章节提议"})
            if target["proposal_hash"] != req.target_hash:
                raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "审批 target_hash 与提议不匹配"})
            proposal_payload = _json(target["payload_json"])
            approval_source_id = target["source_id"]
            approval_source_version = proposal_payload.get("source_version")
            approval_candidate_version = proposal_payload.get("chapter_version")
            approval_evaluation_ref = proposal_payload.get("final_review_ref")
        if req.action == "style_promotion" and approval_candidate_version is None:
            raise HTTPException(status_code=409, detail={"code": "APPROVAL_INVALID", "message": "候选版本缺失"})
        if req.action == "chapter_commit" and not all(
            isinstance(value, str) and value.strip()
            for value in (approval_source_id, approval_source_version, approval_candidate_version, approval_evaluation_ref)
        ):
            raise HTTPException(status_code=409, detail={"code": "APPROVAL_INVALID", "message": "章节审批缺少完整来源绑定"})
        approval_id = _id("approval")
        created_at = _now()
        expires_at = (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=15)).isoformat()
        cur.execute(
            """INSERT INTO v2_approvals
            (approval_id, action, target_id, target_hash, expected_version, work_id,
             source_id, source_version, candidate_version, evaluation_ref,
             actor_id, role, expires_at, validity, consumed_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VALID', NULL, ?)""",
            (
                approval_id,
                req.action,
                req.target_id,
                req.target_hash,
                req.expected_version,
                work_id,
                approval_source_id,
                approval_source_version,
                approval_candidate_version,
                approval_evaluation_ref,
                actor.actor_id,
                "approver",
                expires_at,
                created_at,
            ),
        )
    return ApprovalResponseV2(
        approval_id=approval_id,
        action=req.action,
        target_id=req.target_id,
        target_hash=req.target_hash,
        expected_version=req.expected_version,
        source_id=approval_source_id,
        source_version=approval_source_version,
        candidate_version=approval_candidate_version,
        evaluation_ref=approval_evaluation_ref,
        validity="VALID",
        created_at=created_at,
    )
def _proposal_response(row: Any, idempotent_replay: bool = False) -> dict[str, Any]:
    payload = _json(row["payload_json"])
    return {
        "proposal_id": row["proposal_id"],
        "work_id": row["work_id"],
        "source_id": row["source_id"],
        "text_hash": row["text_hash"],
        "proposal_hash": row["proposal_hash"],
        "status": row["status"],
        "final_review_ref": payload.get("final_review_ref"),
        "chapter_index": payload.get("chapter_index"),
        "chapter_version": payload.get("chapter_version"),
        "idempotent_replay": idempotent_replay,
    }


@router.post("/writing/proposals")
def create_chapter_proposal(req: ChapterProposalRequestV2, request: Request):
    _source_scope(request, req.work_id, req.source_id, "writer")
    if _hash(req.text) != req.text_hash:
        raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "提议正文 text_hash 不匹配"})
    if _proposal_payload_fingerprint(req) != req.proposal_hash:
        raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "proposal_hash 不匹配"})
    config = _config(request)
    _read_snapshot(config, req.work_id, req.source_id, req.source_version)
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        review = cur.execute(
            "SELECT * FROM v2_reviews WHERE report_id = ? AND work_id = ?",
            (req.final_review_ref, req.work_id),
        ).fetchone()
    review_payload = _validate_review_binding(
        review,
        work_id=req.work_id,
        source_id=req.source_id,
        source_version=req.source_version,
        text_hash=req.text_hash,
        chapter_index=req.chapter_index,
        expected_dependency_versions=req.dependency_versions or None,
    )
    with client.transaction() as cur:
        _validate_knowledge_head(cur, req.work_id, review_payload["knowledge_version"])
    payload = req.model_dump()
    client = DatabaseClient(config.sqlite_path)
    with client.transaction() as cur:
        existing = cur.execute(
            "SELECT * FROM v2_proposals WHERE work_id = ? AND idempotency_key = ?",
            (req.work_id, req.idempotency_key),
        ).fetchone()
        if existing:
            if _canonical(_json(existing["payload_json"])) != _canonical(payload):
                raise HTTPException(status_code=409, detail="同幂等键绑定了不同章节提议")
            return _proposal_response(existing, idempotent_replay=True)
        same_hash = cur.execute(
            "SELECT proposal_id FROM v2_proposals WHERE work_id = ? AND proposal_hash = ?",
            (req.work_id, req.proposal_hash),
        ).fetchone()
        if same_hash:
            raise HTTPException(status_code=409, detail="章节提议哈希已存在")
        proposal_id = _id("proposal")
        cur.execute(
            """INSERT INTO v2_proposals
            (proposal_id, work_id, source_id, text_hash, proposal_hash, status, payload_json, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                proposal_id,
                req.work_id,
                req.source_id,
                req.text_hash,
                req.proposal_hash,
                req.status,
                _canonical(payload),
                req.idempotency_key,
                _now(),
            ),
        )
        row = cur.execute("SELECT * FROM v2_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return _proposal_response(row)


def _next_version(current: str) -> str:
    match = re.match(r"^(.*?)(\d+)$", current)
    if match:
        return f"{match.group(1)}{int(match.group(2)) + 1}"
    return f"{current}.1"


def _proposal_payload_fingerprint(req: ChapterProposalRequestV2) -> str:
    return _hash(req.model_dump(exclude={"proposal_hash", "idempotency_key", "status"}))

def _commit_payload_fingerprint(req: ChapterCommitRequestV2) -> str:
    return _hash(req.model_dump(exclude={"idempotency_key", "payload_hash"}))


def _commit_receipt(row: Any, idempotent_replay: bool) -> CommitReceiptV2:
    stored = _json(row["receipt_json"])
    return CommitReceiptV2(
        commit_id=stored["commit_id"],
        new_knowledge_version=stored["new_knowledge_version"],
        chapter_version=stored["chapter_version"],
        text_hash=stored["text_hash"],
        idempotent_replay=idempotent_replay,
    )


def _project_commit_to_formal_tables(
    cur: Any,
    *,
    work_id: str,
    commit_id: str,
    chapter_index: int,
    knowledge_version: str,
    event_rows: list[tuple[str, str]],
    state_rows: list[tuple[str, str]],
    accepted_state_ids: set[str],
    knowledge_items: list[tuple[str, Any]],
    proposal_payload: dict[str, Any],
) -> None:
    """Apply the approved commit's structured facts to their owners atomically."""
    for event_id, event_json in event_rows:
        event = _json(event_json)
        if not isinstance(event, dict):
            raise HTTPException(status_code=409, detail={"code": "INVALID_EVENT", "message": "因果事件必须是对象"})
        timeline_id = str(event.get("timeline_id") or "main")
        scene_uuid = str(event.get("scene_uuid") or f"commit:{commit_id}:{event_id}")
        try:
            narrative_order = int(event.get("narrative_order", chapter_index))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail={"code": "INVALID_EVENT", "message": "因果事件 narrative_order 无效"}) from exc
        if narrative_order < 0:
            raise HTTPException(status_code=409, detail={"code": "INVALID_EVENT", "message": "因果事件 narrative_order 不能为负数"})
        physical_time = str(event.get("physical_time") or "unknown")
        summary = str(event.get("summary") or "")
        if not summary:
            raise HTTPException(status_code=409, detail={"code": "INVALID_EVENT", "message": "因果事件 summary 不能为空"})
        is_canon = 1 if bool(event.get("is_canon", False)) else 0
        status = str(event.get("status") or "mutated")
        existing = cur.execute(
            "SELECT work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status FROM causal_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing:
            expected = (work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status)
            actual = tuple(existing[key] for key in ("work_id", "timeline_id", "scene_uuid", "narrative_order", "physical_time", "summary", "is_canon", "status"))
            if actual != expected:
                raise HTTPException(status_code=409, detail={"code": "EVENT_CONFLICT", "message": "因果事件 ID 已绑定不同作品或载荷"})
            continue
        cur.execute(
            """INSERT INTO causal_events
            (event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status),
        )

    for state_id, state_json in state_rows:
        if state_id not in accepted_state_ids:
            continue
        item = _json(state_json)
        if not isinstance(item, dict):
            raise HTTPException(status_code=409, detail={"code": "INVALID_STATE_CHANGE", "message": "状态变更必须是对象"})
        entity_id = str(item.get("entity_id") or "")
        metric_id = str(item.get("metric_id") or "")
        if not entity_id or not metric_id:
            raise HTTPException(status_code=409, detail={"code": "INVALID_STATE_CHANGE", "message": "状态变更缺少 entity_id 或 metric_id"})
        try:
            delta = float(item.get("delta", 0.0))
            narrative_order = int(item.get("narrative_order", chapter_index))
            new_value = item.get("new_value")
            new_value_float = None if new_value is None else float(new_value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail={"code": "INVALID_STATE_CHANGE", "message": "状态变更数值无效"}) from exc
        if not math.isfinite(delta) or (new_value_float is not None and not math.isfinite(new_value_float)):
            raise HTTPException(status_code=409, detail={"code": "INVALID_STATE_CHANGE", "message": "状态变更数值必须是有限数字"})
        if narrative_order < 0:
            raise HTTPException(status_code=409, detail={"code": "INVALID_STATE_CHANGE", "message": "状态变更 narrative_order 不能为负数"})
        scene_uuid = str(item.get("scene_uuid") or f"commit:{commit_id}:{state_id}")
        reason = str(item.get("reason") or "chapter commit")
        is_anchor = bool(item.get("is_anchor", False))
        rule_version = str(item.get("rule_version") or "v1")
        status_type = str(item.get("status_type") or "EXPLICIT").upper()
        if status_type not in {"EXPLICIT", "UNMEASURED", "NOT_APPLICABLE"}:
            raise HTTPException(status_code=409, detail={"code": "INVALID_STATE_CHANGE", "message": "状态变更 status_type 无效"})
        allows_negative = 1 if str(item.get("allows_negative", "")).lower() in {"1", "true", "yes"} else 0
        cur.execute(
            """INSERT OR IGNORE INTO state_metrics
            (metric_id, work_id, metric_name, unit, status_type, allows_negative, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (metric_id, work_id, str(item.get("metric_name") or metric_id), str(item.get("unit") or ""), status_type, allows_negative, rule_version),
        )
        ensure_entity(cur, work_id, entity_id)
        event_slot = json.dumps(
            {"entity_id": entity_id, "metric_id": metric_id, "scene_uuid": scene_uuid, "narrative_order": narrative_order, "is_anchor": is_anchor},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = _hash({
            "work_id": work_id,
            "entity_id": entity_id,
            "metric_id": metric_id,
            "delta": delta,
            "new_value": new_value_float,
            "is_anchor": is_anchor,
            "scene_uuid": scene_uuid,
            "narrative_order": narrative_order,
            "reason": reason,
            "commit_id": commit_id,
            "knowledge_version": knowledge_version,
            "rule_version": rule_version,
        })
        receipt = cur.execute(
            "SELECT state_event_id, payload_hash FROM state_event_receipts WHERE work_id = ? AND commit_id = ? AND event_slot = ?",
            (work_id, commit_id, event_slot),
        ).fetchone()
        if receipt:
            if receipt["payload_hash"] != payload_hash:
                raise HTTPException(status_code=409, detail={"code": "STATE_EVENT_CONFLICT", "message": "状态事件收据载荷不一致"})
            continue
        cur.execute(
            """INSERT INTO state_events
            (work_id, entity_id, metric_id, delta, new_value, is_anchor, scene_uuid,
             narrative_order, reason, rule_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (work_id, entity_id, metric_id, delta, new_value_float, 1 if is_anchor else 0, scene_uuid, narrative_order, reason, rule_version, _now()),
        )
        state_event_id = int(cur.lastrowid)
        cur.execute(
            """INSERT INTO state_event_receipts
            (work_id, entity_id, metric_id, event_slot, commit_id, idempotency_key,
             knowledge_version, payload_hash, state_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (work_id, entity_id, metric_id, event_slot, commit_id, knowledge_version, payload_hash, state_event_id, _now()),
        )
        computed_value = new_value_float
        if computed_value is None and status_type == "EXPLICIT":
            computed_value = float(cur.execute(
                "SELECT COALESCE(SUM(delta), 0) AS value FROM state_events WHERE work_id = ? AND entity_id = ? AND metric_id = ? AND narrative_order <= ?",
                (work_id, entity_id, metric_id, narrative_order),
            ).fetchone()["value"])
        cur.execute(
            """INSERT INTO state_snapshots
            (work_id, entity_id, metric_id, narrative_order, computed_value, status, based_on_event_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id, entity_id, metric_id, narrative_order) DO UPDATE SET
                computed_value = excluded.computed_value,
                status = excluded.status,
                based_on_event_id = excluded.based_on_event_id,
                updated_at = excluded.updated_at""",
            (work_id, entity_id, metric_id, narrative_order, computed_value, status_type, state_event_id, _now()),
        )

    for character_id, item in knowledge_items:
        if not isinstance(item, dict):
            continue
        raw_claim_ids: list[Any] = []
        for key in ("claim_ids", "known_claim_ids", "claims", "claim_id"):
            value = item.get(key)
            if isinstance(value, list):
                raw_claim_ids.extend(value)
            elif value:
                raw_claim_ids.append(value)
        for claim_id in raw_claim_ids:
            claim_value = str(claim_id).strip()
            if not claim_value:
                continue
            try:
                learned_order = int(item.get("narrative_order", chapter_index))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail={"code": "INVALID_KNOWLEDGE", "message": "角色认知 narrative_order 无效"}) from exc
            if learned_order < 0:
                raise HTTPException(status_code=409, detail={"code": "INVALID_KNOWLEDGE", "message": "角色认知 narrative_order 不能为负数"})
            cur.execute(
                """INSERT OR IGNORE INTO character_known_claims
                (work_id, character_id, claim_id, learned_narrative_order, scene_uuid)
                VALUES (?, ?, ?, ?, ?)""",
                (work_id, str(character_id), claim_value, learned_order, str(item.get("scene_uuid") or f"commit:{commit_id}")),
            )

    for item in proposal_payload.get("entities", []) if isinstance(proposal_payload.get("entities"), list) else []:
        if not isinstance(item, dict) or not item.get("entity_id"):
            continue
        ensure_entity(
            cur,
            work_id,
            str(item["entity_id"]),
            name=str(item.get("name") or item["entity_id"]),
            category=str(item.get("category") or "character"),
        )


@router.post("/writing/commits", response_model=CommitReceiptV2)
def commit_chapter(req: ChapterCommitRequestV2, request: Request):
    actor = _source_scope(request, req.work_id, req.source_id, "writer")
    # Actor authentication and role scope are enforced by the caller-specific helper.
    if _hash(req.pending_text) != req.text_hash:
        raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "提交正文 text_hash 不匹配"})
    if req.payload_hash != _commit_payload_fingerprint(req):
        raise HTTPException(status_code=409, detail={"code": "EVIDENCE_MISMATCH", "message": "payload_hash 不匹配"})
    config = _config(request)
    client = DatabaseClient(config.sqlite_path)
    segmented_content = ChineseFTS(config).segment_text(req.pending_text)
    with client.transaction() as cur:
        existing = cur.execute(
            "SELECT * FROM v2_commits WHERE work_id = ? AND idempotency_key = ?",
            (req.work_id, req.idempotency_key),
        ).fetchone()
        if existing:
            if existing["actor_id"] != actor.actor_id:
                raise HTTPException(status_code=403, detail={"code": "AUTH_FORBIDDEN", "message": "提交重放主体不匹配"})
            stored = _json(existing["receipt_json"])
            if stored.get("request_fingerprint") != _commit_payload_fingerprint(req):
                raise HTTPException(status_code=409, detail="同幂等键绑定了不同提交载荷")
            return _commit_receipt(existing, idempotent_replay=True)

        snapshot = _get_snapshot(cur, req.work_id, req.source_id, req.source_version)
        _validate_snapshot_object(config, snapshot)
        proposal = cur.execute(
            "SELECT * FROM v2_proposals WHERE proposal_id = ? AND work_id = ?",
            (req.proposal_id, req.work_id),
        ).fetchone()
        if not proposal:
            raise HTTPException(status_code=404, detail="未找到章节提议")
        proposal_payload = _json(proposal["payload_json"])
        if not isinstance(proposal_payload, dict):
            raise HTTPException(status_code=500, detail={"code": "PROPOSAL_DATA_CORRUPTED", "message": "stored proposal payload is invalid"})
        if proposal["status"] != "PENDING_CONFIRMATION":
            raise HTTPException(status_code=409, detail="章节提议已被提交或失效")
        proposal_mismatches = [
            proposal["source_id"] != req.source_id,
            proposal["text_hash"] != req.text_hash,
            proposal["proposal_hash"] != req.proposal_hash,
            proposal_payload.get("source_version") != req.source_version,
            proposal_payload.get("chapter_index") != req.chapter_index,
            proposal_payload.get("chapter_version") != req.chapter_version,
            proposal_payload.get("text") != req.pending_text,
            proposal_payload.get("final_review_ref") != req.final_review_reference,
        ]
        if any(proposal_mismatches):
            raise HTTPException(status_code=409, detail="提交载荷与章节提议不匹配")

        review = cur.execute(
            "SELECT * FROM v2_reviews WHERE report_id = ? AND work_id = ?",
            (req.final_review_reference, req.work_id),
        ).fetchone()
        _validate_review_binding(
            review,
            work_id=req.work_id,
            source_id=req.source_id,
            source_version=req.source_version,
            text_hash=req.text_hash,
            chapter_index=req.chapter_index,
            expected_knowledge_version=req.expected_knowledge_version,
            expected_dependency_versions=proposal_payload.get("dependency_versions") or None,
        )

        events = proposal_payload.get("causal_events", proposal_payload.get("events", []))
        if not isinstance(events, list):
            raise HTTPException(status_code=409, detail="causal events projection payload must be a list")
        event_rows: list[tuple[str, str]] = []
        seen_event_ids: set[str] = set()
        for index, item in enumerate(events):
            if not isinstance(item, dict):
                raise HTTPException(status_code=409, detail="causal events projection payload contains a non-object")
            event_id = str(item.get("event_id") or item.get("id") or f"{req.proposal_id}:event:{index}")
            if event_id in seen_event_ids:
                raise HTTPException(status_code=409, detail="causal events projection contains duplicate event_id")
            seen_event_ids.add(event_id)
            event_rows.append((event_id, _canonical(item)))

        state_changes = proposal_payload.get("state_change_proposals", [])
        if not isinstance(state_changes, list):
            raise HTTPException(status_code=409, detail="state projection payload must be a list")
        state_rows: list[tuple[str, str]] = []
        seen_state_ids: set[str] = set()
        for index, item in enumerate(state_changes):
            if not isinstance(item, dict):
                raise HTTPException(status_code=409, detail="state projection payload contains a non-object")
            state_id = str(item.get("state_change_id") or item.get("id") or f"{req.proposal_id}:state:{index}")
            if state_id in seen_state_ids:
                raise HTTPException(status_code=409, detail="state projection contains duplicate state_change_id")
            seen_state_ids.add(state_id)
            state_rows.append((state_id, _canonical(item)))

        knowledge = proposal_payload.get("character_knowledge", {})
        if not isinstance(knowledge, (dict, list)):
            raise HTTPException(status_code=409, detail="knowledge projection payload must be an object or list")
        if isinstance(knowledge, dict):
            knowledge_items = [(str(character_id), item) for character_id, item in knowledge.items()]
        else:
            knowledge_items = []
            seen_character_ids: set[str] = set()
            for index, item in enumerate(knowledge):
                if not isinstance(item, dict):
                    raise HTTPException(status_code=409, detail="knowledge projection payload contains a non-object")
                character_id = str(item.get("character_id") or item.get("entity_id") or index)
                if character_id in seen_character_ids:
                    raise HTTPException(status_code=409, detail="knowledge projection contains duplicate character_id")
                seen_character_ids.add(character_id)
                knowledge_items.append((character_id, item))

        known_state_ids = {
            str(item.get("id") or item.get("state_change_id"))
            for item in proposal_payload.get("state_change_proposals", [])
            if isinstance(item, dict) and (item.get("id") or item.get("state_change_id"))
        }
        if not set(req.accepted_state_change_ids).issubset(known_state_ids):
            raise HTTPException(status_code=409, detail="提交包含未在提议中的状态变更")

        approval = cur.execute(
            "SELECT * FROM v2_approvals WHERE approval_id = ?",
            (req.approval_id,),
        ).fetchone()
        _validate_approval(
            approval,
            actor_id=None,
            work_id=req.work_id,
            action="chapter_commit",
            target_id=req.proposal_id,
            target_hash=req.proposal_hash,
            expected_version=req.expected_knowledge_version,
            source_id=req.source_id,
            source_version=req.source_version,
            candidate_version=req.chapter_version,
            evaluation_ref=proposal_payload.get("final_review_ref"),
        )

        current_knowledge, _current_chapter, has_head = _current_head(cur, req.work_id)
        if has_head and current_knowledge != req.expected_knowledge_version:
            raise HTTPException(status_code=409, detail="知识库版本已变化")
        if req.style_package_version:
            style_head = cur.execute(
                "SELECT active_version FROM v2_style_heads WHERE work_id = ?",
                (req.work_id,),
            ).fetchone()
            if not style_head or style_head["active_version"] != req.style_package_version:
                raise HTTPException(status_code=409, detail="提交引用的风格包不是正式版本")

        base_version = req.expected_knowledge_version if not has_head else current_knowledge
        new_knowledge_version = _next_version(base_version)
        commit_id = _id("commit")
        receipt = {
            "commit_id": commit_id,
            "new_knowledge_version": new_knowledge_version,
            "chapter_version": req.chapter_version,
            "text_hash": req.text_hash,
            "request_fingerprint": _commit_payload_fingerprint(req),
        }
        cur.execute(
            """INSERT INTO v2_commits
            (commit_id, work_id, proposal_id, text_hash, chapter_version, payload_hash, source_version, actor_id, receipt_json, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                commit_id,
                req.work_id,
                req.proposal_id,
                req.text_hash,
                req.chapter_version,
                req.payload_hash,
                req.source_version,
                actor.actor_id,
                _canonical(receipt),
                req.idempotency_key,
                _now(),
            ),
        )
# The commit is the sole projection boundary.  Persist every submitted
        # projection under the immutable commit before advancing the head.
        cur.execute(
            """INSERT INTO v2_commit_documents
            (commit_id, work_id, chapter_index, chapter_version, text_hash, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (commit_id, req.work_id, req.chapter_index, req.chapter_version, req.text_hash, req.pending_text, _now()),
        )
        for event_id, event_json in event_rows:
            cur.execute(
                "INSERT INTO v2_commit_events (commit_id, event_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (commit_id, event_id, event_json, _now()),
            )
        for state_id, state_json in state_rows:
            cur.execute(
                "INSERT INTO v2_commit_state_changes (commit_id, state_change_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (commit_id, state_id, state_json, _now()),
            )
        for character_id, item in knowledge_items:
            cur.execute(
                "INSERT INTO v2_commit_knowledge (commit_id, character_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (commit_id, str(character_id), _canonical(item), _now()),
            )
        cur.execute(
            "INSERT INTO v2_commit_index (commit_id, document_id, work_id, chapter_index, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (commit_id, f"commit:{commit_id}", req.work_id, req.chapter_index, req.text_hash, _now()),
        )
        for projection_kind in ("document", "events", "state", "knowledge", "index"):
            cur.execute(
                "INSERT INTO v2_projection_tasks (task_id, commit_id, work_id, projection_kind, status, created_at) VALUES (?, ?, ?, ?, 'APPLIED', ?)",
                (_id("projection"), commit_id, req.work_id, projection_kind, _now()),
            )
        _project_commit_to_formal_tables(
            cur,
            work_id=req.work_id,
            commit_id=commit_id,
            chapter_index=req.chapter_index,
            knowledge_version=new_knowledge_version,
            event_rows=event_rows,
            state_rows=state_rows,
            accepted_state_ids=set(req.accepted_state_change_ids),
            knowledge_items=knowledge_items,
            proposal_payload=proposal_payload,
        )
        cur.execute(
            """INSERT INTO fts_scenes
            (scene_uuid, work_id, source_id, source_version, chapter_index, segmented_content)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                f"commit:{commit_id}",
                req.work_id,
                req.source_id,
                req.source_version,
                req.chapter_index,
                segmented_content,
            ),
        )

        if has_head:
            cur.execute(
                """UPDATE v2_work_heads
                SET knowledge_version = ?, chapter_version = ?, updated_at = ?
                WHERE work_id = ? AND knowledge_version = ?""",
                (new_knowledge_version, req.chapter_version, _now(), req.work_id, req.expected_knowledge_version),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=409, detail="知识库版本 CAS 失败")
        else:
            cur.execute(
                """INSERT INTO v2_work_heads (work_id, knowledge_version, chapter_version, updated_at)
                VALUES (?, ?, ?, ?)""",
                (req.work_id, new_knowledge_version, req.chapter_version, _now()),
            )
        cur.execute(
            "UPDATE v2_proposals SET status = 'COMMITTED' WHERE proposal_id = ? AND status = 'PENDING_CONFIRMATION'",
            (req.proposal_id,),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="章节提议状态 CAS 失败")
        cur.execute(
            "UPDATE v2_approvals SET consumed_by = ? WHERE approval_id = ? AND consumed_by IS NULL",
            (commit_id, req.approval_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="人工审批消费失败")
    return CommitReceiptV2(**{key: value for key, value in receipt.items() if key != "request_fingerprint"})
