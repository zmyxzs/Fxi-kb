"""fxi.api.server - FastAPI local service composition and startup boundary."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from fxi.api.router_v1 import router as v1_router
from fxi.api.router_v2 import router as v2_router
from fxi.api.router_v3 import StateKnowledgeProvider, router as v3_router
from fxi.api.registry import WorkRegistry
from fxi.core.canonical import sha256_hex
from fxi.core.config import FxiConfig, load_config
from fxi.index_retrieval.vector_adapter import VectorProjectionProvider
from fxi.knowledge.approval_service import ApprovalService
from fxi.knowledge.branch_service import BranchService
from fxi.knowledge.candidate_service import CandidateService, candidate_input_hash
from fxi.knowledge.commit_service import CommitService
from fxi.knowledge.context_compiler import ContextCompiler
from fxi.knowledge.contracts import CONTRACT_REVISION, EvidenceRef, SCHEMA_HASH, SCHEMA_VERSION
from fxi.knowledge.decision_service import DecisionService
from fxi.knowledge.duplicate_clusters import DuplicateClusterIndex
from fxi.knowledge.evaluation_service import EvaluationService
from fxi.knowledge.oag_projection import OAGLiteProjectionProvider
from fxi.knowledge.projection_runner import LexicalProjectionProvider, ProjectionRunner
from fxi.knowledge.promotion_service import PromotionService
from fxi.knowledge.proposal_service import ProposalService
from fxi.knowledge.query_service import QueryService
from fxi.knowledge.registry import DomainRegistry
from fxi.knowledge.review_service import ReviewService
from fxi.knowledge.source_graph import SourceGraph
from fxi.knowledge.state_change_service import StateChangeService
from fxi.knowledge.wiki_projection import WikiProjectionProvider
from fxi.ops.operation_manifest import OperationManifest, openapi_schema_hash
from fxi.ops.readiness import ReadinessService
from fxi.sources.evidence_coordinates import EvidenceService
from fxi.sources.snapshot_service import SnapshotService
from fxi.sources.source_bindings import SourceBindingService
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.sqlite_repositories import (
    SQLiteApprovalRepository,
    SQLiteBranchState,
    SQLiteCandidateRepository,
    SQLiteCommitRepository,
    SQLiteContextCache,
    SQLiteDecisionRepository,
    SQLiteEvaluationRepository,
    SQLiteKnowledgeStore,
    SQLiteProposalRepository,
    SQLitePromotionRepository,
    SQLiteReviewRepository,
    SQLiteSnapshotService,
    SQLiteSourceBindingMap,
)


def _service_override(overrides: Mapping[str, Any], name: str, factory: Any) -> Any:
    """Resolve one injectable service without importing application internals."""

    value = overrides.get(name)
    return factory() if value is None else value


def _persistent_branch_service(
    store: SQLiteKnowledgeStore,
    snapshot_service: Any,
) -> BranchService:
    """Compose BranchService over the shared SQLite authority maps."""

    state = SQLiteBranchState(store)
    return BranchService(
        branches=state.branches,
        heads=state.heads,
        versions=state.versions,
        snapshot_resolver=snapshot_service,
    )


def _dependency_versions(app: FastAPI) -> dict[str, str]:
    state = app.state
    return {
        "contract_revision": str(getattr(state, "contract_revision", "")),
        "schema_hash": str(getattr(state, "schema_hash", "")),
        "schema_version": str(getattr(state, "schema_version", "")),
        "openapi_hash": str(getattr(state, "openapi_hash", "")),
        "service": "fxi-v3",
    }


def _error_payload(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    result = {
        "code": code,
        "message": message,
        "details": dict(details or {}),
    }
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        # Kept for v1/v2 callers while trace_id is the v3 public field.
        "request_id": request_id,
        "trace_id": "trace-" + request_id,
        "dependency_versions": _dependency_versions(request.app),
        "result_hash": sha256_hex(result),
        "retryable": retryable,
    }
    if details:
        payload["details"] = dict(details)
    return payload


def create_app(
    config: FxiConfig | None = None,
    *,
    services: Mapping[str, Any] | None = None,
    semantic_reviewer: Any = None,
    evaluator: Any = None,
) -> FastAPI:
    """Create the v1/v2/v3 application with explicit injectable boundaries.

    The default composition uses the existing SQLite work registry and one
    shared SQLite v3 authority store.  Callers may inject a complete or
    partial service map for synthetic tests; the public router remains the
    only operation boundary.
    """

    cfg = config or load_config()
    cfg.ensure_directories()
    overrides = dict(services or {})
    app = FastAPI(
        title="Fxi Knowledge Base & World State Service",
        version="0.1.0",
    )

    database_client = _service_override(
        overrides, "database_client", lambda: DatabaseClient(cfg.sqlite_path)
    )
    authority_store = _service_override(
        overrides,
        "knowledge_store",
        lambda: SQLiteKnowledgeStore(database_client),
    )
    work_registry = _service_override(
        overrides,
        "work_registry",
        lambda: WorkRegistry.from_database_client(database_client),
    )
    domain_registry = _service_override(overrides, "domain_registry", DomainRegistry)
    if "domain_registry" not in overrides:
        from fxi.domain_packages.novel import register_novel

        register_novel(domain_registry)

    snapshot_root = cfg.data_dir / "source-snapshots"
    snapshot_service = _service_override(
        overrides,
        "snapshot_service",
        lambda: SQLiteSnapshotService(snapshot_root, store=authority_store),
    )
    source_binding_service = _service_override(
        overrides,
        "source_binding_service",
        lambda: SourceBindingService(SQLiteSourceBindingMap(authority_store)),
    )
    source_graph = _service_override(
        overrides,
        "source_graph",
        lambda: SourceGraph(snapshot_resolver=snapshot_service),
    )
    branch_service = _service_override(
        overrides,
        "branch_service",
        lambda: _persistent_branch_service(authority_store, snapshot_service),
    )
    evidence_service = _service_override(
        overrides,
        "evidence_service",
        lambda: EvidenceService(
            snapshot_root,
            snapshot_service=snapshot_service,
            store=snapshot_service.store,
        ),
    )

    candidate_service = _service_override(
        overrides,
        "candidate_service",
        lambda: CandidateService(
            repository=SQLiteCandidateRepository(authority_store),
            snapshot_resolver=snapshot_service.get,
            input_hash_validator=lambda envelope: envelope.input_hash
            == candidate_input_hash(envelope),
        ),
    )
    evaluation_service = _service_override(
        overrides,
        "evaluation_service",
        lambda: EvaluationService(
            candidate_service,
            repository=SQLiteEvaluationRepository(authority_store),
            evaluator=overrides.get("evaluator", evaluator),
            semantic_reviewer=overrides.get(
                "evaluation_semantic_reviewer", semantic_reviewer
            ),
            duplicate_index=overrides.get("duplicate_index", DuplicateClusterIndex()),
        ),
    )
    decision_service = _service_override(
        overrides,
        "decision_service",
        lambda: DecisionService(
            candidate_service,
            evaluation_service,
            repository=SQLiteDecisionRepository(authority_store),
        ),
    )
    promotion_service = _service_override(
        overrides,
        "promotion_service",
        lambda: PromotionService(
            candidate_service,
            evaluation_service,
            repository=overrides.get(
                "promotion_repository", SQLitePromotionRepository(authority_store)
            ),
            decision_service=decision_service,
        ),
    )

    knowledge_provider = _service_override(
        overrides, "knowledge_provider", lambda: StateKnowledgeProvider(app.state)
    )
    state_change_service = _service_override(
        overrides,
        "state_change_service",
        lambda: StateChangeService(domain_registry=domain_registry),
    )
    proposal_service = _service_override(
        overrides,
        "proposal_service",
        lambda: ProposalService(
            repository=overrides.get(
                "proposal_repository", SQLiteProposalRepository(authority_store)
            ),
            state_change_service=state_change_service,
        ),
    )
    approval_service = _service_override(
        overrides,
        "approval_service",
        lambda: ApprovalService(
            proposal_service=proposal_service,
            repository=overrides.get(
                "approval_repository", SQLiteApprovalRepository(authority_store)
            ),
        ),
    )
    commit_service = _service_override(
        overrides,
        "commit_service",
        lambda: CommitService(
            repository=overrides.get(
                "commit_repository", SQLiteCommitRepository(authority_store)
            ),
            proposal_service=proposal_service,
            approval_service=approval_service,
            state_change_service=state_change_service,
        ),
    )
    context_compiler = _service_override(
        overrides,
        "context_compiler",
        lambda: ContextCompiler(
            knowledge_provider=knowledge_provider,
            registry=domain_registry,
            evidence_validator=evidence_service.validate,
            capabilities=domain_registry,
        ),
    )

    fts_provider = _service_override(
        overrides,
        "fts_provider",
        lambda: LexicalProjectionProvider(records=knowledge_provider.records),
    )

    def lexical_query(query_request: Any) -> Mapping[str, Any]:
        current_version = None
        for service_name in ("commit_service", "promotion_service", "branch_service"):
            service = getattr(app.state, service_name, None)
            repository = getattr(service, "repository", service)
            getter = getattr(repository, "get_head", None)
            if not callable(getter):
                getter = getattr(service, "head", None)
            if not callable(getter):
                continue
            try:
                head = getter(query_request.work_id, query_request.branch_id)
            except Exception:
                continue
            if head is not None:
                current_version = getattr(head, "knowledge_version", None)
                break
        records = fts_provider.search(
            query_request.query,
            work_id=query_request.work_id,
            knowledge_version=current_version,
            limit=min(query_request.budget_tokens or 10, 100),
        )
        evidence: list[EvidenceRef] = []
        for record in records:
            for raw_ref in tuple(record.get("evidence_refs", ())):
                if isinstance(raw_ref, EvidenceRef):
                    evidence.append(raw_ref)
                    continue
                if isinstance(raw_ref, Mapping):
                    try:
                        evidence.append(EvidenceRef.model_validate(raw_ref))
                    except Exception:
                        continue
        return {
            "result_refs": tuple(
                str(record.get("document_id"))
                for record in records
                if record.get("document_id")
            ),
            "evidence_refs": tuple(evidence),
        }

    query_service = _service_override(
        overrides,
        "query_service",
        lambda: QueryService(lexical_query),
    )
    review_service = _service_override(
        overrides,
        "review_service",
        lambda: ReviewService(
            semantic_reviewer=overrides.get("semantic_reviewer", semantic_reviewer),
            repository=SQLiteReviewRepository(authority_store),
        ),
    )

    projection_runner = _service_override(
        overrides,
        "projection_runner",
        lambda: ProjectionRunner(providers={"fts": fts_provider}),
    )
    if "projection_runner" not in overrides:
        projection_runner.register(
            OAGLiteProjectionProvider(
                records=knowledge_provider.records,
                conflicts=knowledge_provider.conflicts,
            )
        )
        projection_runner.register(
            WikiProjectionProvider(
                records=knowledge_provider.records,
                conflicts=knowledge_provider.conflicts,
            )
        )
        projection_runner.register(VectorProjectionProvider())

    operation_manifest = _service_override(
        overrides,
        "operation_manifest",
        lambda: OperationManifest(cfg.data_dir / "operation-manifest.v3.json"),
    )

    app.state.config = cfg
    app.state.database_client = database_client
    app.state.knowledge_store = authority_store
    app.state.contract_revision = CONTRACT_REVISION
    app.state.schema_version = SCHEMA_VERSION
    app.state.schema_hash = SCHEMA_HASH
    app.state.work_registry = work_registry
    app.state.domain_registry = domain_registry
    app.state.source_binding_service = source_binding_service
    app.state.snapshot_service = snapshot_service
    app.state.source_graph = source_graph
    app.state.branch_service = branch_service
    app.state.evidence_service = evidence_service
    app.state.candidate_service = candidate_service
    app.state.evaluation_service = evaluation_service
    app.state.decision_service = decision_service
    app.state.promotion_service = promotion_service
    app.state.knowledge_provider = knowledge_provider
    app.state.state_change_service = state_change_service
    app.state.proposal_service = proposal_service
    app.state.approval_service = approval_service
    app.state.commit_service = commit_service
    app.state.context_compiler = context_compiler
    app.state.fts_provider = fts_provider
    app.state.query_service = query_service
    app.state.review_service = review_service
    app.state.semantic_reviewer = overrides.get("semantic_reviewer", semantic_reviewer)
    app.state.projection_runner = projection_runner
    app.state.operation_manifest = operation_manifest
    app.state.knowledge_claims = list(overrides.get("knowledge_claims", ()))
    app.state.knowledge_relations = list(overrides.get("knowledge_relations", ()))
    app.state.knowledge_conflicts = list(overrides.get("knowledge_conflicts", ()))
    app.state.context_views = overrides.get(
        "context_views", SQLiteContextCache(authority_store)
    )

    readiness_service = _service_override(
        overrides,
        "readiness_service",
        lambda: ReadinessService(
            registry=work_registry,
            operation_manifest=operation_manifest,
            projection_runner=projection_runner,
            domain_registry=domain_registry,
            contract_revision=app.state.contract_revision,
            schema_hash=app.state.schema_hash,
        ),
    )
    app.state.readiness_service = readiness_service

    cors_origins = [
        item.strip()
        for item in os.getenv(
            "FXI_CORS_ORIGINS", "http://127.0.0.1,http://localhost"
        ).split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Fxi-Actor-Token",
            "X-Fxi-Human-Approval-Token",
            "X-Request-ID",
        ],
    )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, Mapping) else {"message": str(exc.detail)}
        code = str(detail.get("code") or f"HTTP_{exc.status_code}")
        message = str(detail.get("message") or "请求失败")
        details = detail.get("details")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _error_payload(
                    request,
                    code=code,
                    message=message,
                    retryable=exc.status_code >= 500 or exc.status_code == 409,
                    details=details if isinstance(details, Mapping) else None,
                )
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": _error_payload(
                    request,
                    code="INCOMPLETE_INPUT",
                    message="请求参数校验失败",
                    retryable=False,
                    details={"fields": [str(item.get("loc")) for item in exc.errors()]},
                )
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        """Expose a stable error code without leaking implementation details."""

        del exc
        return JSONResponse(
            status_code=500,
            content={
                "error": _error_payload(
                    request,
                    code="INTERNAL_ERROR",
                    message="服务内部错误",
                    retryable=True,
                )
            },
        )

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "fxi"}

    app.include_router(v1_router)
    app.include_router(v2_router)
    app.include_router(v3_router)

    app.state.openapi_hash = openapi_schema_hash(app.openapi())
    readiness_service.openapi_hash = app.state.openapi_hash
    return app


def run_server(host: str = "127.0.0.1", port: int = 8765):
    """启动本地服务"""
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
