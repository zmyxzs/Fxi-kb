"""Versioned Fxi v3 HTTP boundary.

The router is deliberately a thin adapter.  Authentication is derived from
the server token, scope is checked against the explicit work/source registry,
and all lifecycle work is delegated to the v3 services.  No route reads
projects, materials, or source text directly; source snapshots are fetched
through the registered source adapter and published by ``SnapshotService``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError as PydanticValidationError

from fxi.api.auth import AuthenticatedActor, authenticate_request
from fxi.api.contracts_v3 import (
    ApprovalRequestV3,
    CandidateSubmitRequestV3,
    CommitRequestV3,
    ContextViewRequestV3,
    DecisionRequestV3,
    EvaluationRequestV3,
    ProjectCreateRequestV3,
    ProjectionRebuildRequestV3,
    ProposalRequestV3,
    PromotionRequestV3,
    QueryRequestV3,
    ReadinessResultV3,
    ReviewRequestV3,
    SourceBindingRequestV3,
    SourceSnapshotRequestV3,
    StoryCoordinateV3,
    EvidenceRefV3,
    V3Response,
    CONTRACT_REVISION,
    ERROR_CODE_MAP,
    PUBLIC_ERROR_CODES,
    SCHEMA_HASH,
    SCHEMA_VERSION,
)
from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError
from fxi.core.identifiers import validate_segment
from fxi.knowledge.approval_service import ApprovalRequest as ServiceApprovalRequest
from fxi.knowledge.branch_service import BranchServiceError
from fxi.knowledge.commit_service import CommitExpectation
from fxi.knowledge.contracts import (
    Actor,
    ApprovalRef,
    CandidateEnvelope,
    ContextView,
    EvidenceRef,
    EvaluationPolicy,
    Scope,
    SourceBindingRef,
    SourceSnapshotRef,
    StoryCoordinate,
)
from fxi.knowledge.decisions import DecisionAction
from fxi.knowledge.proposal_service import ProposalRequest as ServiceProposalRequest
from fxi.knowledge.review_service import ReviewRequest as ServiceReviewRequest
from fxi.knowledge.source_graph import SourceCompositeRef
from fxi.knowledge.state_change_service import StateChangeServiceError
from fxi.knowledge.objects import KnowledgeHead
from fxi.knowledge.candidate_service import candidate_input_hash
from fxi.sources.adapters import SourceResource, SourceRevision
from fxi.storage.versioned_store import NORMALIZATION_VERSION


router = APIRouter(prefix="/v3", tags=["v3"])

_ALL_READ_ROLES = frozenset(
    {"reader", "writer", "reviewer", "approver", "editor", "owner", "admin"}
)
_PROJECT_ROLES = frozenset({"writer", "owner", "admin"})
_REVIEW_ROLES = frozenset({"writer", "reviewer", "editor", "owner", "admin"})
_APPROVER_ROLES = frozenset({"approver", "owner", "admin"})


class StateKnowledgeProvider:
    """Read-only collection facade for context and projection services."""

    def __init__(self, app_state: Any) -> None:
        self.app_state = app_state

    @staticmethod
    def _values(repository: Any, name: str) -> tuple[Any, ...]:
        value = getattr(repository, name, {}) if repository is not None else {}
        if isinstance(value, Mapping):
            return tuple(value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(value)
        return ()

    @property
    def objects(self) -> tuple[Any, ...]:
        promotion = getattr(self.app_state, "promotion_service", None)
        repository = getattr(promotion, "repository", None)
        return self._values(repository, "objects")

    @property
    def claims(self) -> tuple[Any, ...]:
        return tuple(getattr(self.app_state, "knowledge_claims", ()))

    @property
    def relations(self) -> tuple[Any, ...]:
        return tuple(getattr(self.app_state, "knowledge_relations", ()))

    @property
    def conflicts(self) -> tuple[Any, ...]:
        return tuple(getattr(self.app_state, "knowledge_conflicts", ()))

    @property
    def records(self) -> tuple[Any, ...]:
        return (*self.objects, *self.claims, *self.relations)


def _state(request: Request) -> Any:
    return request.app.state


def _trace_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip()
    if supplied:
        try:
            return "trace-" + validate_segment(supplied, "request_id")
        except FxiError:
            return "trace-" + uuid4().hex
    return "trace-" + uuid4().hex


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_jsonable(item) for item in value)
    return value


def _error_code(error: BaseException, default: str = "INTERNAL_ERROR") -> str:
    raw_code = getattr(error, "code", None)
    detail = getattr(error, "detail", None)
    if not raw_code and isinstance(detail, Mapping):
        raw_code = detail.get("code")
    raw = str(raw_code or default)
    return ERROR_CODE_MAP.get(raw, raw)


def _status_for_code(code: str) -> int:
    if code in {"NOT_FOUND", "WORK_NOT_FOUND", "SOURCE_NOT_FOUND"}:
        return 404
    if code in {"AUTHORIZATION_FAILED", "AUTH_FORBIDDEN", "INVALID_SCOPE"}:
        return 403
    if code in {
        "STALE_VERSION",
        "CAS_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "CONFLICTING_ASSERTIONS",
        "APPROVAL_REQUIRED",
        "APPROVAL_EXPIRED",
        "PROJECTION_STALE",
    }:
        return 409
    if code in {"CAPABILITY_UNSUPPORTED", "MODEL_UNAVAILABLE"}:
        return 422
    if code in {"INTERNAL_ERROR", "STORAGE_ERROR", "WORK_REGISTRY_UNAVAILABLE"}:
        return 503
    return 422


def _raise_v3(code: str, message: str, *, status_code: int | None = None) -> None:
    normalized = ERROR_CODE_MAP.get(str(code), str(code))
    raise HTTPException(
        status_code=status_code or _status_for_code(normalized),
        detail={"code": normalized, "message": message[:500]},
    )


def _dependencies(state: Any) -> dict[str, str]:
    return {
        "contract_revision": CONTRACT_REVISION,
        "schema_hash": SCHEMA_HASH,
        "schema_version": SCHEMA_VERSION,
        "openapi_hash": str(getattr(state, "openapi_hash", "")),
        "service": "fxi-v3",
    }


def _response(
    request: Request,
    result: Any,
    *,
    code: str = "OK",
    message: str = "OK",
    retryable: bool = False,
    trace_id: str | None = None,
) -> dict[str, Any]:
    data = _jsonable(result)
    return V3Response(
        code=ERROR_CODE_MAP.get(code, code),
        message=message,
        trace_id=trace_id or _trace_id(request),
        dependency_versions=_dependencies(_state(request)),
        retryable=retryable,
        result=data,
    ).model_dump(mode="json")


def _service(state: Any, name: str) -> Any:
    value = getattr(state, name, None)
    if value is None:
        _raise_v3("CAPABILITY_UNSUPPORTED", f"v3 service is not configured: {name}")
    return value


def _actor_for_roles(
    request: Request,
    roles: frozenset[str],
    work_id: str | None = None,
    *,
    require_registered_work: bool = True,
) -> AuthenticatedActor:
    try:
        actor = authenticate_request(request)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_v3("AUTH_CONFIG_UNAVAILABLE", str(exc), status_code=503)
    if not (actor.roles & roles) and "admin" not in actor.roles:
        _raise_v3("AUTHORIZATION_FAILED", "actor lacks the required role", status_code=403)
    if work_id is None:
        return actor
    if work_id not in actor.work_ids and "*" not in actor.work_ids and "admin" not in actor.roles:
        _raise_v3("AUTHORIZATION_FAILED", "actor is outside the work scope", status_code=403)
    if require_registered_work:
        registry = _service(request.app.state, "work_registry")
        try:
            registry.require_work(work_id)
        except Exception as exc:
            _raise_v3(_error_code(exc, "WORK_NOT_FOUND"), str(exc), status_code=404)
    return actor


def _project_actor(request: Request, work_id: str) -> AuthenticatedActor:
    # Work creation is the one operation that cannot require a registry row in
    # advance.  The token still must explicitly name the work or use the
    # administrator wildcard; the body never supplies an actor identity.
    return _actor_for_roles(
        request,
        _PROJECT_ROLES,
        work_id,
        require_registered_work=False,
    )


def _choose_role(actor: AuthenticatedActor, preferred: Sequence[str]) -> str:
    for role in preferred:
        if role in actor.roles:
            return role
    if "admin" in actor.roles:
        return "admin"
    return sorted(actor.roles)[0]


def _bound_actor(
    actor: AuthenticatedActor,
    *,
    role: str,
    work_id: str,
    branch_id: str,
    as_of: int | str,
    purpose: str,
) -> Actor:
    return Actor(
        actor_id=actor.actor_id,
        role=role,
        work_id=work_id,
        scope=Scope(
            work_id=work_id,
            branch_id=branch_id,
            as_of=as_of,
            purpose=purpose,
            actor=actor.actor_id,
        ),
    )


def _idempotency(request: Request, body: Any) -> str:
    value = getattr(body, "idempotency_key", None) or request.headers.get("Idempotency-Key")
    if not isinstance(value, str) or not value.strip():
        _raise_v3("INVALID_SCHEMA", "Idempotency-Key is required", status_code=422)
    try:
        return validate_segment(value.strip(), "idempotency_key")
    except Exception as exc:
        _raise_v3("INVALID_SCHEMA", "idempotency key is invalid", status_code=422)
        raise AssertionError from exc


def _operation_payload(body: Any, actor: AuthenticatedActor | None) -> dict[str, Any]:
    payload = _jsonable(body)
    if actor is None:
        return {"request": payload}
    return {
        "request": payload,
        "actor": {"actor_id": actor.actor_id, "roles": sorted(actor.roles)},
    }


def _execute(
    request: Request,
    *,
    kind: str,
    body: Any,
    actor: AuthenticatedActor | None,
    handler: Any,
) -> dict[str, Any]:
    manifest = _service(request.app.state, "operation_manifest")
    key = _idempotency(request, body)
    try:
        record = manifest.execute(
            kind,
            key,
            _operation_payload(body, actor),
            lambda: _jsonable(handler()),
        )
    except Exception as exc:
        _raise_v3(_error_code(exc), str(exc))
        raise AssertionError
    if record.status != "SUCCEEDED":
        _raise_v3(record.code, record.message)
    return _response(
        request,
        record.result,
        trace_id="trace-" + record.operation_id.removeprefix("operation-")[:48],
    )


def _coordinate(value: StoryCoordinateV3) -> StoryCoordinate:
    # ``schema_version`` identifies the public wire contract; the internal
    # coordinate model intentionally does not carry transport metadata.
    return StoryCoordinate.model_validate(
        value.model_dump(mode="json", exclude={"schema_version"})
    )


def _binding_for(state: Any, work_id: str, source_id: str, branch_id: str) -> SourceBindingRef:
    service = _service(state, "source_binding_service")
    matches = tuple(
        item for item in service.list(work_id)
        if item.source_id == source_id and item.branch_id == branch_id
    )
    if not matches:
        _raise_v3("SOURCE_NOT_FOUND", "source is not explicitly bound to the requested work/branch", status_code=404)
    if len(matches) > 1:
        _raise_v3("SOURCE_SCOPE_REQUIRED", "multiple source bindings match the requested scope", status_code=409)
    return matches[0]


def _source_path(config: Any, value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        _raise_v3("INVALID_SCOPE", "source_dir is required")
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        _raise_v3("INVALID_SCOPE", "source_dir must be a contained relative path")
    root = Path(config.sources_dir).resolve()
    target = (root.joinpath(*relative.parts)).resolve(strict=False)
    if target == root or root not in target.parents:
        _raise_v3("INVALID_SCOPE", "source_dir is outside the configured source root")
    return target


def _ensure_branch(state: Any, work_id: str, branch_id: str) -> None:
    branch_service = _service(state, "branch_service")
    if branch_id == "branch-main":
        try:
            branch_service.create(work_id, None, None)
            head = branch_service.head(work_id, branch_id)
            for service_name in ("promotion_service", "commit_service"):
                service = getattr(state, service_name, None)
                repository = getattr(service, "repository", None)
                seed_head = getattr(repository, "seed_head", None)
                if callable(seed_head) and repository.get_head(work_id, branch_id) is None:
                    seed_head(head)
                versions = getattr(repository, "knowledge_versions", None)
                branch_versions = getattr(branch_service, "_versions", {})
                version = branch_versions.get(head.knowledge_version) if isinstance(branch_versions, Mapping) else None
                if version is not None and isinstance(versions, dict):
                    versions.setdefault(version.knowledge_version, version)
            return
        except Exception as exc:
            _raise_v3(_error_code(exc, "INVALID_SCOPE"), str(exc))
    branches = getattr(branch_service, "_branches", {})
    branch = branches.get(branch_id) if isinstance(branches, Mapping) else None
    if branch is None or branch.work_id != work_id:
        _raise_v3("INVALID_SCOPE", "branch is not registered for the requested work")


def _fetch_revision(state: Any, binding: SourceBindingRef) -> SourceRevision:
    config = _service(state, "config")
    source_dir = binding.source_id
    registry = getattr(state, "work_registry", None)
    if registry is not None:
        database_binding = registry.get_source(binding.work_id, binding.source_id)
        if database_binding is not None and database_binding.source_dir:
            source_dir = database_binding.source_dir
    path = _source_path(config, source_dir)
    from fxi.sources.adapters import LocalTextAdapter

    adapter = LocalTextAdapter(config.sources_dir, source_id=binding.source_id)
    if path.is_file():
        relative = path.relative_to(Path(config.sources_dir).resolve())
        return adapter.fetch(
            SourceResource(
                path=relative,
                source_id=binding.source_id,
                work_id=binding.work_id,
                resource_id=relative.as_posix(),
            )
        )
    if not path.is_dir():
        _raise_v3("SOURCE_NOT_FOUND", "bound source path does not exist", status_code=404)
    files = sorted(
        item for item in path.rglob("*")
        if item.is_file() and item.suffix.lower() in {".txt", ".md", ".markdown"}
    )
    if not files:
        _raise_v3("SOURCE_NOT_FOUND", "bound source directory has no supported text documents", status_code=404)
    if len(files) > 4096:
        _raise_v3("BUDGET_EXCEEDED", "source snapshot document count exceeds the limit", status_code=422)
    revisions: list[SourceRevision] = []
    root = Path(config.sources_dir).resolve()
    for index, file_path in enumerate(files, start=1):
        if file_path.is_symlink():
            _raise_v3("INVALID_SCOPE", "source snapshot cannot traverse a symlink")
        relative = file_path.relative_to(root)
        revision = adapter.fetch(
            SourceResource(
                path=relative,
                source_id=binding.source_id,
                work_id=binding.work_id,
                document_id=f"document-{index}",
                resource_id=relative.as_posix(),
            )
        )
        # LocalTextAdapter returns one chapter per resource.  Re-number the
        # immutable input before composing a directory revision so the
        # VersionedStore's contiguous-chapter contract remains satisfied.
        from dataclasses import replace

        document = replace(revision.documents[0], chapter_index=index)
        revisions.append(replace(revision, documents=(document,)))
    documents = tuple(document for revision in revisions for document in revision.documents)
    revision_hash = (
        sha256_hex(documents[0].text)
        if len(documents) == 1
        else sha256_hex([document.text for document in documents])
    )
    return SourceRevision(
        source_id=binding.source_id,
        source_version=revision_hash,
        documents=documents,
        content_hash=revision_hash,
        normalization_version=revisions[0].normalization_version,
        work_id=binding.work_id,
        resource_id=source_dir,
        metadata={"document_count": len(documents)},
    )


def _snapshot(state: Any, snapshot_id: str) -> SourceSnapshotRef:
    service = _service(state, "snapshot_service")
    try:
        return service.get(snapshot_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
        raise AssertionError


def _source_composite(
    state: Any,
    coordinate: StoryCoordinate,
    source_ids: Sequence[str] = (),
) -> SourceCompositeRef:
    graph = _service(state, "source_graph")
    binding_service = _service(state, "source_binding_service")
    selected_ids = set(source_ids)
    if coordinate.source_id:
        selected_ids.add(coordinate.source_id)
    bindings = tuple(
        item for item in binding_service.list(coordinate.work_id)
        if item.branch_id == coordinate.branch_id and (not selected_ids or item.source_id in selected_ids)
    )
    if not bindings:
        _raise_v3("MISSING_CONTEXT", "no source binding is available for the requested scope")
    try:
        return graph.compose_snapshot(coordinate.work_id, bindings, as_of=coordinate.as_of)
    except Exception as exc:
        _raise_v3(_error_code(exc, "INVALID_SCOPE"), str(exc))
        raise AssertionError


def _head_for(state: Any, coordinate: StoryCoordinate) -> KnowledgeHead:
    repositories = []
    commit_service = getattr(state, "commit_service", None)
    promotion_service = getattr(state, "promotion_service", None)
    for service in (commit_service, promotion_service):
        repository = getattr(service, "repository", None)
        if repository is not None:
            repositories.append(repository)
    current = None
    for repository in repositories:
        getter = getattr(repository, "get_head", None)
        if callable(getter):
            candidate = getter(coordinate.work_id, coordinate.branch_id)
            if candidate is not None:
                current = candidate
                break
    if current is not None:
        if current.knowledge_version != coordinate.knowledge_version:
            _raise_v3("STALE_VERSION", "coordinate knowledge_version is not the current head", status_code=409)
        return current
    return KnowledgeHead(
        work_id=coordinate.work_id,
        branch_id=coordinate.branch_id,
        knowledge_version=coordinate.knowledge_version,
        version_hash="0" * 64,
        cas_revision=0,
    )


def _internal_evidence(
    value: Any,
    *,
    work_id: str,
    branch_id: str,
    purpose: str,
    actor_id: str,
    snapshot_ref: str | None = None,
    license_name: str | None = None,
) -> EvidenceRef:
    if isinstance(value, EvidenceRef):
        return value
    if isinstance(value, EvidenceRefV3):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        _raise_v3("INVALID_EVIDENCE", "evidence reference must be an object")
    source_id = value.get("source_id")
    source_version = value.get("source_version")
    document_id = value.get("document_id")
    start = value.get("start", value.get("start_char"))
    end = value.get("end", value.get("end_char"))
    try:
        return EvidenceRef(
            evidence_id=value.get("evidence_id"),
            source_snapshot_ref=value.get("source_snapshot_ref", snapshot_ref),
            source_id=source_id,
            source_version=source_version,
            document_id=document_id,
            start=start,
            end=end,
            excerpt_hash=value.get("excerpt_hash"),
            normalization_version=value.get("normalization_version", NORMALIZATION_VERSION),
            scope=Scope(
                work_id=work_id,
                branch_id=branch_id,
                as_of=value.get("as_of", 0),
                purpose=purpose,
                actor=actor_id,
            ),
            license=value.get("license", license_name),
        )
    except Exception as exc:
        _raise_v3(_error_code(exc, "INVALID_EVIDENCE"), "evidence reference is invalid")
        raise AssertionError from exc


def _public_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, EvidenceRef):
        return {
            "source_id": value.source_id,
            "source_version": value.source_version,
            "document_id": value.document_id,
            "start_char": value.start,
            "end_char": value.end,
            "excerpt_hash": value.excerpt_hash,
        }
    data = _jsonable(value)
    if isinstance(data, Mapping):
        result = dict(data)
        if "start" in result and "start_char" not in result:
            result["start_char"] = result.pop("start")
        if "end" in result and "end_char" not in result:
            result["end_char"] = result.pop("end")
        for key in ("evidence_id", "source_snapshot_ref", "normalization_version", "scope", "license"):
            result.pop(key, None)
        return result
    return {"value": data}


def _context_from_wire(state: Any, value: Mapping[str, Any] | None, coordinate: StoryCoordinate, actor_id: str) -> ContextView:
    if value is None:
        _raise_v3("MISSING_CONTEXT", "context_view is required")
    view_hash = value.get("view_hash")
    cache = getattr(state, "context_views", {})
    if isinstance(cache, Mapping) and isinstance(view_hash, str) and view_hash in cache:
        return cache[view_hash].model_copy(deep=True)
    if isinstance(value.get("view_id"), str) and isinstance(cache, Mapping) and value["view_id"] in cache:
        return cache[value["view_id"]].model_copy(deep=True)
    try:
        parsed = ContextView.model_validate(value)
    except PydanticValidationError as exc:
        # A public ContextManifest is a projection, not an authority object.
        # It is accepted here only when it is the exact manifest previously
        # compiled by this app and indexed in the in-process view cache.  A
        # caller cannot turn arbitrary JSON into an internal ContextView.
        _raise_v3("INVALID_SCHEMA", "context_view must be a validated Fxi ContextView")
        raise AssertionError from exc
    if parsed.work_id != coordinate.work_id or parsed.branch_id != coordinate.branch_id:
        _raise_v3("INVALID_SCOPE", "context_view is outside coordinate scope")
    if parsed.scope.work_id != coordinate.work_id or parsed.scope.branch_id != coordinate.branch_id:
        _raise_v3("INVALID_SCOPE", "context_view scope is outside coordinate scope")
    if parsed.scope.actor != actor_id:
        _raise_v3("AUTHORIZATION_FAILED", "context_view actor does not match caller", status_code=403)
    if parsed.as_of != coordinate.as_of:
        _raise_v3("STALE_VERSION", "context_view as_of does not match coordinate")
    if view_hash is not None and view_hash != parsed.view_hash:
        _raise_v3("INVALID_HASH", "context_view view_hash does not match canonical content")
    return parsed


def _public_context(view: ContextView, coordinate: StoryCoordinate, composite: SourceCompositeRef) -> dict[str, Any]:
    fact_refs: list[str] = []
    method_refs: list[str] = []
    expression_refs: list[str] = []
    for block in view.blocks:
        target = {
            "FACT": fact_refs,
            "METHOD": method_refs,
            "EXPRESSION": expression_refs,
        }.get(block.block_kind)
        if target is not None:
            target.append(block.block_id)
    completeness = "CONFLICTED" if view.conflicts else ("INCOMPLETE" if view.completeness != "COMPLETE" else "COMPLETE")
    error_code = None
    if completeness == "CONFLICTED":
        error_code = "CONFLICTING_ASSERTIONS"
    elif completeness == "INCOMPLETE":
        error_code = "MISSING_CONTEXT"
    scope = {
        "work_id": view.scope.work_id,
        "branch_id": view.scope.branch_id,
        "as_of": view.scope.as_of,
        "purpose": view.scope.purpose,
        "actor": view.scope.actor,
    }
    return {
        "schema_version": "context-manifest.v3",
        "coordinate": coordinate.model_dump(mode="json"),
        "knowledge_version": coordinate.knowledge_version,
        "source_snapshot_refs": list(composite.source_snapshot_refs),
        "fact_refs": fact_refs,
        "method_refs": method_refs,
        "expression_refs": expression_refs,
        "forbidden_refs": list(view.forbidden_refs),
        "evidence_refs": [_public_evidence(item) for item in view.evidence_refs],
        "scope": scope,
        "budget": _jsonable(view.budget),
        "view_hash": view.view_hash,
        "content_hash": view.content_hash,
        "completeness": completeness,
        "error_code": error_code,
        "diagnostics": [{"code": item} for item in (view.forbidden_refs if completeness != "COMPLETE" else ())],
        "style_selection": "none",
        "style_refs": [],
    }


def _projection_records(state: Any) -> tuple[Any, ...]:
    provider = getattr(state, "knowledge_provider", None)
    return tuple(getattr(provider, "records", ())) if provider is not None else ()


def _sync_projection_records(state: Any) -> None:
    records = _projection_records(state)
    runner = getattr(state, "projection_runner", None)
    if runner is None:
        return
    for provider in getattr(runner, "providers", {}).values():
        if hasattr(provider, "records"):
            provider.records = records
        if hasattr(provider, "conflicts"):
            provider.conflicts = tuple(getattr(state.knowledge_provider, "conflicts", ()))


def _remember_context(state: Any, view: ContextView) -> None:
    cache = getattr(state, "context_views", None)
    if isinstance(cache, dict):
        cache[view.view_id] = view.model_copy(deep=True)
        cache[view.view_hash] = view.model_copy(deep=True)
        cache[f"{view.work_id}:{view.branch_id}"] = view.model_copy(deep=True)
    runner = getattr(state, "projection_runner", None)
    if runner is not None:
        runner.context_views[view.view_id] = view.model_copy(deep=True)
        runner.context_views[f"{view.work_id}:{view.branch_id}"] = view.model_copy(deep=True)


@router.get("/capabilities", response_model=V3Response)
def capabilities(request: Request, work_id: str = Query(...)) -> dict[str, Any]:
    actor = _actor_for_roles(request, _ALL_READ_ROLES, work_id)
    registry = _service(request.app.state, "domain_registry")
    try:
        manifest = registry.capabilities(work_id)
    except Exception as exc:
        _raise_v3(_error_code(exc), str(exc))
    capability_values = {
        "source-snapshot",
        "candidate",
        "evaluation",
        "decision",
        "promotion",
        "review",
        "proposal",
        "approval",
        "commit",
        "context",
        "query",
        "fts",
    }
    capability_values.update(manifest.views)
    capability_values.update(manifest.projections)
    runner = getattr(request.app.state, "projection_runner", None)
    capability_values.update(getattr(runner, "providers", {}).keys())
    result = {
        "work_id": work_id,
        "capabilities": tuple(sorted(capability_values)),
        "contract_revision": CONTRACT_REVISION,
        "schema_hash": SCHEMA_HASH,
        "trace_id": _trace_id(request),
        "registered_type_uris": manifest.registered_type_uris,
        "error_codes": PUBLIC_ERROR_CODES,
    }
    del actor
    return _response(request, result)


@router.post("/projects", response_model=V3Response)
def create_project(body: ProjectCreateRequestV3, request: Request) -> dict[str, Any]:
    actor = _project_actor(request, body.work_id)
    registry = _service(request.app.state, "work_registry")

    def handler() -> dict[str, Any]:
        record = registry.create_work(
            body.work_id,
            owner_id=actor.actor_id,
            slug=body.slug,
            title=body.title,
            source_dir=body.source_dir,
            skill_root=body.skill_root,
        )
        _ensure_branch(request.app.state, body.work_id, "branch-main")
        return {
            "work_id": record.work_id,
            "owner_id": record.owner_id,
            "slug": record.slug,
            "title": record.title,
        }

    return _execute(request, kind="project-create", body=body, actor=actor, handler=handler)


@router.post("/sources/bindings", response_model=V3Response)
def bind_source(body: SourceBindingRequestV3, request: Request) -> dict[str, Any]:
    actor = _actor_for_roles(request, _PROJECT_ROLES, body.work_id)
    _ensure_branch(request.app.state, body.work_id, body.branch_id)
    if body.source_dir:
        _source_path(_service(request.app.state, "config"), body.source_dir)
    binding_service = _service(request.app.state, "source_binding_service")
    registry = _service(request.app.state, "work_registry")

    def handler() -> dict[str, Any]:
        try:
            ref = SourceBindingRef(
                binding_id=body.binding_id or "binding-" + sha256_hex(body.model_dump(mode="json"))[:48],
                work_id=body.work_id,
                source_id=body.source_id,
                role=body.role,
                priority=body.priority,
                branch_id=body.branch_id,
                license=body.license,
                access=body.access,
                allowed_purposes=body.allowed_purposes,
                sync_direction=body.sync_direction,
            )
            stored = binding_service.bind(ref)
            registry.register_source(
                body.work_id,
                body.source_id,
                source_dir=body.source_dir or body.source_id,
                source_version=body.source_version,
            )
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return stored.model_dump(mode="json")

    return _execute(request, kind="source-bind", body=body, actor=actor, handler=handler)


@router.post("/sources/snapshots", response_model=V3Response)
def create_snapshot(body: SourceSnapshotRequestV3, request: Request) -> dict[str, Any]:
    coordinate = _coordinate(body.coordinate)
    actor = _actor_for_roles(request, _PROJECT_ROLES, coordinate.work_id)
    if coordinate.branch_id != body.coordinate.branch_id:
        _raise_v3("INVALID_SCOPE", "coordinate branch is invalid")
    binding = _binding_for(request.app.state, coordinate.work_id, body.source_id, coordinate.branch_id)
    if coordinate.source_id and coordinate.source_id != body.source_id:
        _raise_v3("INVALID_SCOPE", "source_id does not match coordinate")
    if coordinate.source_version and coordinate.source_version != body.source_version:
        _raise_v3("STALE_VERSION", "source_version does not match coordinate")
    snapshot_service = _service(request.app.state, "snapshot_service")

    def handler() -> dict[str, Any]:
        revision = _fetch_revision(request.app.state, binding)
        if revision.source_version != body.source_version:
            _raise_v3("STALE_VERSION", "requested source_version is not the fetched source revision")
        if binding.validity.status.upper() != "ACTIVE":
            _raise_v3("STALE_VERSION", "source binding is not active")
        try:
            ref = snapshot_service.create(revision, binding)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return {
            "snapshot_ref": ref.snapshot_id,
            "source_id": ref.source_id,
            "source_version": ref.source_version,
            "snapshot_hash": ref.content_hash,
        }

    return _execute(request, kind="source-snapshot", body=body, actor=actor, handler=handler)


@router.post("/candidates", response_model=V3Response)
def submit_candidate(body: CandidateSubmitRequestV3, request: Request) -> dict[str, Any]:
    actor = _actor_for_roles(request, _PROJECT_ROLES, body.work_id)
    snapshot = _snapshot(request.app.state, body.source_snapshot_ref)
    binding = _binding_for(request.app.state, body.work_id, snapshot.source_id, _service(request.app.state, "snapshot_service").binding_for(snapshot.snapshot_id).branch_id)
    branch_id = _service(request.app.state, "snapshot_service").binding_for(snapshot.snapshot_id).branch_id
    if snapshot.work_id != body.work_id:
        _raise_v3("INVALID_SCOPE", "candidate source snapshot is outside work scope")
    evidence = tuple(
        _internal_evidence(
            item,
            work_id=body.work_id,
            branch_id=branch_id,
            purpose="candidate",
            actor_id=actor.actor_id,
            snapshot_ref=body.source_snapshot_ref,
            license_name=binding.license,
        )
        for item in body.evidence_refs
    )
    domain_registry = _service(request.app.state, "domain_registry")
    try:
        domain_registry.validate(body.artifact_kind, body.payload, package_version=body.domain_package_version)
    except Exception as exc:
        _raise_v3(_error_code(exc, "UNREGISTERED_TYPE"), str(exc))
    envelope = CandidateEnvelope(
        candidate_id=body.artifact_id,
        artifact_kind=body.artifact_kind,
        status="CANDIDATE",
        work_id=body.work_id,
        branch_id=branch_id,
        source_snapshot_ref=body.source_snapshot_ref,
        evidence_refs=evidence,
        input_hash=body.input_hash,
        extractor_id=body.extractor_id,
        schema_version=body.schema_version,
        domain_package_version=body.domain_package_version,
        model_route=body.model_route,
        prompt_hash=body.prompt_hash,
        policy_hash=body.policy_hash,
        budget_ref=body.budget_ref,
        payload=body.payload,
    )
    if body.input_hash != candidate_input_hash(envelope):
        _raise_v3("INVALID_HASH", "input_hash does not match canonical candidate input")
    service = _service(request.app.state, "candidate_service")

    def handler() -> dict[str, Any]:
        evidence_service = getattr(request.app.state, "evidence_service", None)
        if evidence_service is not None:
            for item in evidence:
                evidence_service.validate(item)
        try:
            ref = service.submit(envelope)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return {
            "artifact_id": ref.candidate_id,
            "artifact_hash": ref.candidate_hash,
            "status": ref.status,
            "trace_id": _trace_id(request),
        }

    return _execute(request, kind="candidate-submit", body=body, actor=actor, handler=handler)


@router.post("/evaluations", response_model=V3Response)
def evaluate_candidate(body: EvaluationRequestV3, request: Request) -> dict[str, Any]:
    candidate_service = _service(request.app.state, "candidate_service")
    try:
        candidate = candidate_service.get(body.candidate_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
    actor = _actor_for_roles(request, _REVIEW_ROLES, candidate.work_id)
    service = _service(request.app.state, "evaluation_service")
    policy = EvaluationPolicy.model_validate(body.policy.model_dump(mode="json"))

    def handler() -> dict[str, Any]:
        try:
            manifest = service.evaluate(body.candidate_id, policy)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return manifest.model_dump(mode="json")

    return _execute(request, kind="candidate-evaluate", body=body, actor=actor, handler=handler)


@router.post("/decisions", response_model=V3Response)
def decide_candidate(body: DecisionRequestV3, request: Request) -> dict[str, Any]:
    candidate_service = _service(request.app.state, "candidate_service")
    try:
        candidate = candidate_service.get(body.candidate_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
    action = DecisionAction(body.action)
    roles = _APPROVER_ROLES if action is DecisionAction.APPROVE else _REVIEW_ROLES
    actor = _actor_for_roles(request, roles, candidate.work_id)
    service = _service(request.app.state, "decision_service")
    role = _choose_role(actor, ("approver", "owner", "reviewer", "writer", "admin"))
    bound = _bound_actor(
        actor,
        role=role,
        work_id=candidate.work_id,
        branch_id=candidate.branch_id,
        as_of=0,
        purpose="candidate-decision",
    )

    def handler() -> dict[str, Any]:
        try:
            decision = service.decide(body.candidate_id, action, bound)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return decision.model_dump(mode="json")

    return _execute(request, kind="candidate-decide", body=body, actor=actor, handler=handler)


@router.post("/promotions", response_model=V3Response)
def promote_candidate(body: PromotionRequestV3, request: Request) -> dict[str, Any]:
    candidate_service = _service(request.app.state, "candidate_service")
    try:
        candidate = candidate_service.get(body.candidate_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
    actor = _actor_for_roles(request, _APPROVER_ROLES, candidate.work_id)
    promotion = _service(request.app.state, "promotion_service")
    approval_service = getattr(request.app.state, "approval_service", None)

    def handler() -> dict[str, Any]:
        approval = None
        if approval_service is not None:
            try:
                approval = approval_service.get(body.approval_id)
            except Exception as exc:
                _raise_v3(_error_code(exc, "APPROVAL_REQUIRED"), str(exc), status_code=409)
        if approval is None:
            getter = getattr(getattr(promotion, "repository", None), "get_approval", None)
            approval = getter(body.approval_id) if callable(getter) else None
        if not isinstance(approval, ApprovalRef):
            _raise_v3("APPROVAL_REQUIRED", "promotion approval is not registered", status_code=409)
        if approval.approval_hash != body.approval_hash:
            _raise_v3("IDEMPOTENCY_CONFLICT", "approval hash does not match", status_code=409)
        if approval.actor.actor_id != actor.actor_id:
            _raise_v3("AUTHORIZATION_FAILED", "approval actor does not match caller", status_code=403)
        try:
            promotion.register_approval(approval)
            receipt = promotion.promote(body.candidate_id, approval, body.expected_head)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        repository = getattr(promotion, "repository", None)
        version = getattr(repository, "versions", {}).get(receipt.new_knowledge_version)
        if version is not None:
            runner = getattr(request.app.state, "projection_runner", None)
            if runner is not None:
                runner.knowledge_versions[version.knowledge_version] = version
        return receipt.model_dump(mode="json")

    return _execute(request, kind="candidate-promote", body=body, actor=actor, handler=handler)


@router.post("/context/views", response_model=V3Response)
def compile_context(body: ContextViewRequestV3, request: Request) -> dict[str, Any]:
    coordinate = _coordinate(body.coordinate)
    actor = _actor_for_roles(request, _ALL_READ_ROLES, coordinate.work_id)
    _ensure_branch(request.app.state, coordinate.work_id, coordinate.branch_id)
    composite = None
    if body.source_composite is not None:
        try:
            composite = SourceCompositeRef.model_validate(body.source_composite)
        except Exception as exc:
            _raise_v3("INVALID_SCHEMA", "source_composite is invalid")
    if composite is None:
        composite = _source_composite(request.app.state, coordinate, body.source_ids)
    head = _head_for(request.app.state, coordinate)
    selector_payload = {
        "work_id": coordinate.work_id,
        "branch_id": coordinate.branch_id,
        "as_of": coordinate.as_of,
        "purpose": body.purpose,
        "type_uris": body.type_uris,
        "object_refs": body.object_refs,
        "required_claim_refs": body.required_claim_refs,
        "required_evidence_refs": body.required_evidence_refs,
        "source_ids": body.source_ids,
        "capabilities": body.required_capabilities,
        "pov_id": body.pov_id,
        "budget_tokens": body.budget,
        "source_composite_ref": composite,
        "knowledge_head": head,
        "actor": actor.actor_id,
    }
    compiler = _service(request.app.state, "context_compiler")

    def handler() -> dict[str, Any]:
        try:
            view = compiler.compile(selector_payload)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        _remember_context(request.app.state, view)
        return _public_context(view, coordinate, composite)

    return _execute(request, kind="context-view", body=body, actor=actor, handler=handler)


@router.post("/query", response_model=V3Response)
def query_knowledge(body: QueryRequestV3, request: Request) -> dict[str, Any]:
    coordinate = _coordinate(body.coordinate)
    actor = _actor_for_roles(request, _ALL_READ_ROLES, coordinate.work_id)
    service = _service(request.app.state, "query_service")
    internal = {
        "work_id": coordinate.work_id,
        "branch_id": coordinate.branch_id,
        "as_of": coordinate.as_of,
        "purpose": body.purpose,
        "query": body.query,
        "source_ids": body.source_ids,
        "type_uris": body.type_uris,
        "capabilities": body.capabilities,
        "pov_id": body.pov_id,
        "budget_tokens": body.budget,
    }

    def handler() -> dict[str, Any]:
        try:
            result = service.query(internal, intent=body.purpose)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return {
            "results": tuple({"ref": ref} for ref in result.result_refs),
            "trace_id": result.trace_id,
            "code": result.code,
            "status": result.status,
            "query_hash": result.query_hash,
            "result_hash": result.result_hash,
            "source_versions": result.source_versions,
            "evidence_refs": [_public_evidence(item) for item in result.evidence_refs],
            "diagnostics": result.diagnostics,
        }

    return _execute(request, kind="knowledge-query", body=body, actor=actor, handler=handler)


def _review_request(state: Any, body: ReviewRequestV3, actor: AuthenticatedActor) -> ServiceReviewRequest:
    coordinate = _coordinate(body.coordinate)
    context = _context_from_wire(state, body.context_view, coordinate, actor.actor_id)
    head = _head_for(state, coordinate)
    composite = None
    if body.source_composite is not None:
        try:
            composite = SourceCompositeRef.model_validate(body.source_composite)
        except Exception as exc:
            _raise_v3("INVALID_SCHEMA", "source_composite is invalid")
    if composite is None:
        composite = _source_composite(state, coordinate, (coordinate.source_id,) if coordinate.source_id else ())
    evidence = tuple(
        _internal_evidence(
            item,
            work_id=coordinate.work_id,
            branch_id=coordinate.branch_id,
            purpose="review",
            actor_id=actor.actor_id,
            snapshot_ref=body.source_snapshot_ref,
        )
        for item in context.evidence_refs
    )
    if evidence and not context.blocks:
        context = context.model_copy(update={"evidence_refs": evidence})
    return ServiceReviewRequest(
        draft_ref=body.draft_ref,
        draft_hash=body.draft_hash,
        coordinate=coordinate,
        context_view=context,
        knowledge_head=head,
        source_composite=composite,
        source_snapshot_ref=body.source_snapshot_ref,
        source_id=body.source_id,
        source_version=body.source_version,
        context_hash=body.context_hash or context.view_hash,
        checks=body.checks,
        required_checks=body.required_checks,
        semantic_reviewer_id=body.semantic_reviewer_id,
        semantic_reviewer_version=body.semantic_reviewer_version,
        review_id=body.review_id,
    )


@router.post("/writing/reviews", response_model=V3Response)
def review_writing(body: ReviewRequestV3, request: Request) -> dict[str, Any]:
    coordinate = _coordinate(body.coordinate)
    actor = _actor_for_roles(request, _REVIEW_ROLES, coordinate.work_id)
    service = _service(request.app.state, "review_service")
    configured = getattr(request.app.state, "semantic_reviewer", None)
    if configured is not None and getattr(service, "semantic_reviewer", None) is not configured:
        from fxi.knowledge.review_service import ReviewService

        service = ReviewService(
            semantic_reviewer=configured,
            reviewer_id=getattr(configured, "reviewer_id", None),
            reviewer_version=getattr(configured, "reviewer_version", None),
            repository=service.repository,
        )
        request.app.state.review_service = service
    internal = _review_request(request.app.state, body, actor)

    def handler() -> dict[str, Any]:
        try:
            report = service.review(internal)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return report.model_dump(mode="json")

    return _execute(request, kind="writing-review", body=body, actor=actor, handler=handler)


def _review_from_mapping(value: Mapping[str, Any]) -> Any:
    from fxi.knowledge.contracts import ReviewReport

    try:
        return ReviewReport.model_validate(value)
    except PydanticValidationError as exc:
        _raise_v3("INVALID_SCHEMA", "review report is invalid")
        raise AssertionError from exc


def _state_change_from_mapping(value: Mapping[str, Any]) -> Any:
    from fxi.knowledge.contracts import StateChangeSet

    try:
        return StateChangeSet.model_validate(value)
    except PydanticValidationError as exc:
        _raise_v3("INVALID_SCHEMA", "state change set is invalid")
        raise AssertionError from exc


def _proposal_request(state: Any, body: ProposalRequestV3, actor: AuthenticatedActor) -> ServiceProposalRequest:
    review = _review_from_mapping(body.review)
    coordinate = review.coordinate
    context = _context_from_wire(state, body.context_view, coordinate, actor.actor_id)
    head = _head_for(state, coordinate)
    state_change_set = _state_change_from_mapping(body.state_change_set)
    composite = None
    if body.source_composite is not None:
        try:
            composite = SourceCompositeRef.model_validate(body.source_composite)
        except Exception as exc:
            _raise_v3("INVALID_SCHEMA", "source_composite is invalid")
    if composite is None:
        composite = _source_composite(state, coordinate, (coordinate.source_id,) if coordinate.source_id else ())
    return ServiceProposalRequest(
        draft_ref=body.draft_ref,
        draft_hash=body.draft_hash,
        review=review,
        state_change_set=state_change_set,
        context_view=context,
        knowledge_head=head,
        source_composite=composite,
        source_snapshot_ref=body.source_snapshot_ref,
        source_id=body.source_id,
        source_version=body.source_version,
        proposal_id=body.proposal_id,
    )


@router.post("/writing/proposals", response_model=V3Response)
def create_proposal(body: ProposalRequestV3, request: Request) -> dict[str, Any]:
    review_data = body.review
    work_id = review_data.get("coordinate", {}).get("work_id") if isinstance(review_data, Mapping) else None
    if not isinstance(work_id, str):
        _raise_v3("INVALID_SCHEMA", "proposal review must include a coordinate")
    actor = _actor_for_roles(request, _REVIEW_ROLES, work_id)
    service = _service(request.app.state, "proposal_service")
    internal = _proposal_request(request.app.state, body, actor)

    def handler() -> dict[str, Any]:
        try:
            proposal = service.create(internal)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return proposal.model_dump(mode="json")

    return _execute(request, kind="writing-proposal", body=body, actor=actor, handler=handler)


@router.post("/approvals", response_model=V3Response)
def create_approval(body: ApprovalRequestV3, request: Request) -> dict[str, Any]:
    actor = _actor_for_roles(request, _APPROVER_ROLES, body.work_id)
    service = _service(request.app.state, "approval_service")
    proposal_service = _service(request.app.state, "proposal_service")
    try:
        record = proposal_service.get_record(body.proposal_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
    work_id = record.review.coordinate.work_id
    branch_id = record.review.coordinate.branch_id
    if body.work_id and body.work_id != work_id:
        _raise_v3("INVALID_SCOPE", "approval work_id does not match proposal")
    _actor_for_roles(request, _APPROVER_ROLES, work_id)
    role = _choose_role(actor, ("approver", "owner", "admin"))
    bound = _bound_actor(
        actor,
        role=role,
        work_id=work_id,
        branch_id=branch_id,
        as_of=record.review.coordinate.as_of,
        purpose="proposal-approval",
    )
    internal = ServiceApprovalRequest(
        proposal_id=body.proposal_id,
        proposal_hash=body.proposal_hash,
        work_id=work_id,
        branch_id=branch_id,
        knowledge_version=body.expected_version or record.knowledge_head.knowledge_version,
        action="COMMIT",
        expires_at=body.expires_at,
        approval_id=body.approval_id,
        idempotency_key=body.idempotency_key or request.headers.get("Idempotency-Key"),
        approval_hash=body.approval_hash,
    )

    def handler() -> dict[str, Any]:
        try:
            approval = service.create(internal, bound)
            promotion = getattr(request.app.state, "promotion_service", None)
            if promotion is not None:
                promotion.register_approval(approval)
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        return {
            "schema_version": "approval-ref.v3",
            "approval_id": approval.approval_id,
            "proposal_id": approval.proposal_id,
            "proposal_hash": record.proposal.proposal_hash,
            "actor": approval.actor.model_dump(mode="json"),
            "approved_at": body.approved_at,
            "expires_at": approval.expires_at,
            "expected_version": record.knowledge_head.knowledge_version,
            "approval_hash": approval.approval_hash,
        }

    return _execute(request, kind="proposal-approval", body=body, actor=actor, handler=handler)


def _commit_expectation(state: Any, body: CommitRequestV3, actor: AuthenticatedActor) -> tuple[Any, Any, CommitExpectation]:
    proposal_service = _service(state, "proposal_service")
    try:
        record = proposal_service.get_record(body.proposal_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
    coordinate = record.review.coordinate
    bound = _bound_actor(
        actor,
        role=_choose_role(actor, ("approver", "owner", "admin")),
        work_id=coordinate.work_id,
        branch_id=coordinate.branch_id,
        as_of=coordinate.as_of,
        purpose="proposal-commit",
    )
    approval_service = _service(state, "approval_service")
    try:
        approval = approval_service.get(body.approval_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "APPROVAL_REQUIRED"), str(exc), status_code=409)
    if approval.approval_hash != body.approval_hash:
        _raise_v3("IDEMPOTENCY_CONFLICT", "approval hash does not match stored approval")
    context = _context_from_wire(state, body.context_view, coordinate, actor.actor_id) if body.context_view else record.context_view
    review = _review_from_mapping(body.review) if body.review else record.review
    changes = _state_change_from_mapping(body.state_change_set) if body.state_change_set else record.state_change_set
    composite = record.source_composite
    if body.source_composite:
        try:
            composite = SourceCompositeRef.model_validate(body.source_composite)
        except Exception as exc:
            _raise_v3("INVALID_SCHEMA", "source_composite is invalid")
    head = record.knowledge_head
    if body.knowledge_head:
        try:
            head = KnowledgeHead.model_validate(body.knowledge_head)
        except Exception as exc:
            _raise_v3("INVALID_SCHEMA", "knowledge_head is invalid")
    expectation = CommitExpectation(
        work_id=body.work_id or coordinate.work_id,
        branch_id=body.branch_id or coordinate.branch_id,
        expected_knowledge_version=body.expected_knowledge_version,
        chapter_version=body.chapter_version,
        idempotency_key=body.idempotency_key,
        actor=bound,
        review=review,
        state_change_set=changes,
        context_view=context,
        knowledge_head=head,
        source_composite=composite,
        source_snapshot_ref=body.source_snapshot_ref,
        source_id=body.source_id,
        source_version=body.source_version,
        draft_ref=body.draft_ref,
        draft_hash=body.draft_hash,
        chapter=body.chapter,
        projection_kinds=body.projection_kinds,
        commit_id=body.commit_id,
    )
    return record, approval, expectation


@router.post("/writing/commits", response_model=V3Response)
def commit_writing(body: CommitRequestV3, request: Request) -> dict[str, Any]:
    proposal_service = _service(request.app.state, "proposal_service")
    try:
        record = proposal_service.get_record(body.proposal_id)
    except Exception as exc:
        _raise_v3(_error_code(exc, "NOT_FOUND"), str(exc), status_code=404)
    actor = _actor_for_roles(request, _APPROVER_ROLES, record.review.coordinate.work_id)
    service = _service(request.app.state, "commit_service")
    _, approval, expectation = _commit_expectation(request.app.state, body, actor)

    def handler() -> dict[str, Any]:
        try:
            receipt = service.commit(
                {"proposal_id": body.proposal_id, "proposal_hash": body.proposal_hash},
                approval,
                expectation,
            )
        except Exception as exc:
            _raise_v3(_error_code(exc), str(exc))
        repository = getattr(service, "repository", None)
        version = getattr(repository, "knowledge_versions", {}).get(receipt.new_knowledge_version)
        runner = getattr(request.app.state, "projection_runner", None)
        if version is not None and runner is not None:
            runner.knowledge_versions[version.knowledge_version] = version
        return {
            "schema_version": "commit-receipt.v3",
            "commit_id": receipt.commit_id,
            "proposal_id": receipt.proposal_id,
            "new_knowledge_version": receipt.new_knowledge_version,
            "chapter_version": receipt.chapter_version,
            "idempotent_replay": receipt.idempotent_replay,
            "proposal_hash": body.proposal_hash,
            "status": "COMMITTED",
        }

    return _execute(request, kind="writing-commit", body=body, actor=actor, handler=handler)


@router.post("/projections/rebuild", response_model=V3Response)
def rebuild_projections(body: ProjectionRebuildRequestV3, request: Request) -> dict[str, Any]:
    actor = _actor_for_roles(request, _PROJECT_ROLES, body.work_id)
    runner = _service(request.app.state, "projection_runner")
    repositories = [
        getattr(getattr(request.app.state, "commit_service", None), "repository", None),
        getattr(getattr(request.app.state, "promotion_service", None), "repository", None),
    ]
    version = runner.knowledge_versions.get(body.knowledge_version)
    for repository in repositories:
        versions = getattr(repository, "knowledge_versions", getattr(repository, "versions", {}))
        if version is None and isinstance(versions, Mapping):
            version = versions.get(body.knowledge_version)
    if version is None:
        _raise_v3("NOT_FOUND", "knowledge version is not registered", status_code=404)
    if version.work_id != body.work_id or version.branch_id != body.branch_id:
        _raise_v3("INVALID_SCOPE", "knowledge version is outside the requested scope")

    def handler() -> dict[str, Any]:
        runner.knowledge_versions[version.knowledge_version] = version
        if body.context_view:
            coordinate = StoryCoordinate(
                work_id=body.work_id,
                branch_id=body.branch_id,
                as_of=0,
                chapter_index=1,
                narrative_order=1,
                knowledge_version=body.knowledge_version,
            )
            view = _context_from_wire(request.app.state, body.context_view, coordinate, actor.actor_id)
            runner.context_views[version.knowledge_version] = view
        _sync_projection_records(request.app.state)
        manifests = []
        for projection_id in body.projection_ids:
            try:
                manifests.append(runner.rebuild(projection_id, version).model_dump(mode="json"))
            except Exception as exc:
                _raise_v3(_error_code(exc), str(exc))
        codes = [str(item.get("error_code")) for item in manifests if item.get("error_code")]
        return {"knowledge_version": version.knowledge_version, "manifests": manifests, "codes": codes}

    return _execute(request, kind="projection-rebuild", body=body, actor=actor, handler=handler)


@router.get("/health", response_model=V3Response)
def health(request: Request) -> dict[str, Any]:
    return _response(
        request,
        {
            "status": "ok",
            "service": "fxi",
            "contract_revision": CONTRACT_REVISION,
            "schema_hash": SCHEMA_HASH,
        },
    )


@router.get("/readiness", response_model=V3Response)
def readiness(request: Request) -> dict[str, Any]:
    service = _service(request.app.state, "readiness_service")
    try:
        result = ReadinessResultV3.model_validate(service.check())
    except Exception as exc:
        _raise_v3(_error_code(exc), str(exc))
    return _response(request, result.model_dump(mode="json"), code="OK" if result.status == "READY" else result.status)


__all__ = ["StateKnowledgeProvider", "router"]
