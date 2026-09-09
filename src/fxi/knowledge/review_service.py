"""Final writing review boundary for the generic Fxi v3 kernel.

The service accepts only hashes and public references.  It never parses draft
text into knowledge and it never creates a proposal.  A semantic reviewer is
an explicitly injected capability; its output is treated as untrusted input
and the final status is recomputed from normalized checks.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from pydantic import Field, ValidationError as PydanticValidationError, model_validator

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .contracts import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    Actor,
    ContextView,
    ContractModel,
    EvidenceRef,
    ErrorCode,
    ReviewReport,
    Scope,
    StoryCoordinate,
    _hash,
    _stable_hash,
    _token,
)
from .objects import KnowledgeHead
from .source_graph import SourceCompositeRef


class ReviewServiceError(FxiError):
    """A review request or final report failed the public boundary."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class SemanticReviewer(Protocol):
    """The minimum port implemented by a trusted, versioned reviewer."""

    def review(self, request: Mapping[str, Any]) -> Any: ...


class ReviewRequest(ContractModel):
    """Strict local input DTO for ``ReviewService.review``.

    F1 intentionally freezes the wire result rather than service-specific
    request plumbing.  Aliases accepted here are normalized before validation;
    the result still contains only the F1 ``ReviewReport`` contract.
    """

    draft_ref: str
    draft_hash: str
    coordinate: StoryCoordinate
    context_view: ContextView
    knowledge_head: KnowledgeHead
    source_composite: SourceCompositeRef | None = None
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    context_hash: str | None = None
    checks: tuple[Mapping[str, Any], ...] = ()
    required_checks: tuple[str, ...] = ()
    semantic_reviewer_id: str | None = None
    semantic_reviewer_version: str | None = None
    review_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        aliases = {
            "draft_ref": ("draft_id",),
            "context_view": ("context",),
            "source_composite": ("source_graph",),
            "source_snapshot_ref": ("snapshot_ref",),
            "context_hash": ("context_view_hash",),
            "review_id": ("report_id",),
            "semantic_reviewer_id": ("reviewer_id", "semantic_reviewer"),
            "semantic_reviewer_version": ("reviewer_version",),
        }
        for target, candidates in aliases.items():
            if result.get(target) is not None:
                continue
            for candidate in candidates:
                if candidate in result:
                    value = result.pop(candidate)
                    if target == "semantic_reviewer_id" and isinstance(value, Mapping):
                        value = value.get("reviewer_id") or value.get("id")
                    result[target] = value
                    break
        return result

    @classmethod
    def _validate_token_or_none(cls, value: str | None, label: str) -> str | None:
        return None if value is None else _token(value, label)

    @model_validator(mode="after")
    def validate_request(self) -> "ReviewRequest":
        try:
            _token(self.draft_ref, "draft_ref")
            _hash(self.draft_hash, "draft_hash")
            _token(self.coordinate.work_id, "work_id")
            _token(self.coordinate.branch_id, "branch_id")
            _token(self.knowledge_head.knowledge_version, "knowledge_version")
            if self.context_hash is not None:
                _hash(self.context_hash, "context_hash")
            for field_name in (
                "source_snapshot_ref",
                "source_id",
                "source_version",
                "semantic_reviewer_id",
                "semantic_reviewer_version",
                "review_id",
            ):
                self._validate_token_or_none(getattr(self, field_name), field_name)
            seen: set[str] = set()
            for check_id in self.required_checks:
                normalized = _token(check_id, "required_check")
                if normalized in seen:
                    raise ValueError("required_checks must be unique")
                seen.add(normalized)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self


class ReviewRepository(Protocol):
    def get(self, report_id: str) -> ReviewReport | None: ...

    def save(self, report: ReviewReport) -> None: ...


@dataclass(frozen=True)
class ReviewRecord:
    report: ReviewReport
    request: ReviewRequest


class InMemoryReviewRepository:
    """Immutable report store used by the API adapter and clean-room tests."""

    def __init__(self) -> None:
        self._records: dict[str, ReviewRecord] = {}
        self._lock = RLock()

    def get(self, report_id: str) -> ReviewReport | None:
        with self._lock:
            record = self._records.get(report_id)
            return None if record is None else record.report.model_copy(deep=True)

    def get_record(self, report_id: str) -> ReviewRecord | None:
        with self._lock:
            record = self._records.get(report_id)
            return None if record is None else deepcopy(record)

    def save(self, report: ReviewReport) -> None:
        with self._lock:
            existing = self._records.get(report.report_id)
            if existing is not None and existing.report.report_hash != report.report_hash:
                raise ReviewServiceError(
                    "report_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            # ``save`` is intentionally also usable by an adapter that has no
            # request metadata.  Such a report remains retrievable as a report,
            # while ReviewService itself stores the richer ReviewRecord.
            request = existing.request if existing is not None else None
            if request is None:
                request = ReviewRequest(
                    draft_ref=report.draft_ref,
                    draft_hash=report.draft_hash,
                    coordinate=report.coordinate,
                    context_view=_minimal_context_view(report),
                    knowledge_head=KnowledgeHead(
                        work_id=report.coordinate.work_id,
                        branch_id=report.coordinate.branch_id,
                        knowledge_version=report.coordinate.knowledge_version,
                        version_hash="0" * 64,
                    ),
                    context_hash=report.context_hash,
                )
            self._records[report.report_id] = ReviewRecord(
                report=report.model_copy(deep=True),
                request=request,
            )

    def save_record(self, record: ReviewRecord) -> None:
        with self._lock:
            existing = self._records.get(record.report.report_id)
            if existing is not None and existing.report.report_hash != record.report.report_hash:
                raise ReviewServiceError(
                    "report_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            self._records[record.report.report_id] = deepcopy(record)


def _minimal_context_view(report: ReviewReport) -> ContextView:
    """Build adapter metadata without inventing a fact or source reference."""

    return ContextView(
        view_id=f"context-for-{report.report_id}",
        work_id=report.coordinate.work_id,
        branch_id=report.coordinate.branch_id,
        as_of=report.coordinate.as_of,
        purpose="review",
        scope=Scope(
            work_id=report.coordinate.work_id,
            branch_id=report.coordinate.branch_id,
            as_of=report.coordinate.as_of,
            purpose="review",
            actor="review-adapter",
        ),
        view_hash=report.context_hash,
    )


def _review_payload_hash(report: ReviewReport) -> str:
    payload = {
        key: getattr(report, key)
        for key in (
            "report_id",
            "draft_ref",
            "draft_hash",
            "coordinate",
            "context_hash",
            "checks",
            "overall_status",
            "blocking_findings",
        )
    }
    return _stable_hash(payload)


class ReviewService:
    """Run and persist one final, hash-bound semantic review report."""

    contract_revision = CONTRACT_REVISION
    schema_hash = SCHEMA_HASH

    def __init__(
        self,
        *,
        semantic_reviewer: SemanticReviewer | Callable[[Mapping[str, Any]], Any] | None = None,
        reviewer: SemanticReviewer | Callable[[Mapping[str, Any]], Any] | None = None,
        reviewer_id: str | None = None,
        reviewer_version: str | None = None,
        repository: InMemoryReviewRepository | ReviewRepository | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if semantic_reviewer is not None and reviewer is not None:
            raise ReviewServiceError("configure only one semantic reviewer", ErrorCode.INVALID_SCHEMA.value)
        self.semantic_reviewer = semantic_reviewer if semantic_reviewer is not None else reviewer
        self.reviewer_id = reviewer_id or self._reviewer_attr("reviewer_id", "id")
        self.reviewer_version = reviewer_version or self._reviewer_attr("reviewer_version", "version")
        self.repository = repository or InMemoryReviewRepository()
        self.id_factory = id_factory or (lambda: f"review-{uuid4().hex}")
        self._lock = RLock()

    def _reviewer_attr(self, *names: str) -> str | None:
        if self.semantic_reviewer is None:
            return None
        for name in names:
            value = getattr(self.semantic_reviewer, name, None)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def review(self, request: ReviewRequest | Mapping[str, Any]) -> ReviewReport:
        normalized = self._normalize_request(request)
        self._validate_bindings(normalized)
        reviewer_id = normalized.semantic_reviewer_id or self.reviewer_id
        reviewer_version = normalized.semantic_reviewer_version or self.reviewer_version
        if self.semantic_reviewer is None or not reviewer_id or not reviewer_version:
            return self._store_report(
                normalized,
                reviewer_id=reviewer_id or "unconfigured-reviewer",
                reviewer_version=reviewer_version or "unconfigured",
                checks=({
                    "check_id": "semantic_reviewer",
                    "status": "INCOMPLETE",
                    "required": True,
                    "error_code": "SEMANTIC_REVIEWER_MISSING",
                    "findings": ({"code": "SEMANTIC_REVIEWER_MISSING"},),
                },),
                blocking=({"check_id": "semantic_reviewer", "code": "SEMANTIC_REVIEWER_MISSING"},),
            )

        reviewer_request = normalized.model_dump(mode="json")
        reviewer_request["semantic_reviewer_id"] = reviewer_id
        reviewer_request["semantic_reviewer_version"] = reviewer_version
        try:
            raw = self._invoke_reviewer(reviewer_request)
            checks = self._extract_checks(raw)
        except Exception as exc:
            return self._store_report(
                normalized,
                reviewer_id=reviewer_id,
                reviewer_version=reviewer_version,
                checks=({
                    "check_id": "semantic_reviewer",
                    "status": "INCOMPLETE",
                    "required": True,
                    "error_code": "SEMANTIC_REVIEWER_FAILED",
                    "findings": ({"code": "SEMANTIC_REVIEWER_FAILED", "error": type(exc).__name__},),
                },),
                blocking=({"check_id": "semantic_reviewer", "code": "SEMANTIC_REVIEWER_FAILED"},),
            )

        normalized_checks, blocking = self._normalize_checks(
            checks,
            required_checks=normalized.required_checks,
            coordinate=normalized.coordinate,
        )
        return self._store_report(
            normalized,
            reviewer_id=reviewer_id,
            reviewer_version=reviewer_version,
            checks=normalized_checks,
            blocking=blocking,
        )

    def get(self, report_id: str) -> ReviewReport:
        report = self.repository.get(report_id)
        if report is None:
            raise ReviewServiceError(f"review report not found: {report_id}", ErrorCode.NOT_FOUND.value)
        return report

    def get_record(self, report_id: str) -> ReviewRecord:
        getter = getattr(self.repository, "get_record", None)
        if not callable(getter):
            report = self.get(report_id)
            return ReviewRecord(report=report, request=self._request_from_report(report))
        record = getter(report_id)
        if record is None:
            raise ReviewServiceError(f"review report not found: {report_id}", ErrorCode.NOT_FOUND.value)
        return record

    def _request_from_report(self, report: ReviewReport) -> ReviewRequest:
        return ReviewRequest(
            draft_ref=report.draft_ref,
            draft_hash=report.draft_hash,
            coordinate=report.coordinate,
            context_view=_minimal_context_view(report),
            knowledge_head=KnowledgeHead(
                work_id=report.coordinate.work_id,
                branch_id=report.coordinate.branch_id,
                knowledge_version=report.coordinate.knowledge_version,
                version_hash="0" * 64,
            ),
            context_hash=report.context_hash,
        )

    def _store_report(
        self,
        request: ReviewRequest,
        *,
        reviewer_id: str,
        reviewer_version: str,
        checks: Sequence[Mapping[str, Any]],
        blocking: Sequence[Mapping[str, Any]],
    ) -> ReviewReport:
        identity_payload = {
            "draft_ref": request.draft_ref,
            "draft_hash": request.draft_hash,
            "coordinate": request.coordinate,
            "context_hash": request.context_hash or request.context_view.view_hash,
            "checks": checks,
            "reviewer_id": reviewer_id,
            "reviewer_version": reviewer_version,
        }
        report_id = request.review_id or f"review-{_stable_hash(identity_payload)}"
        report = ReviewReport(
            report_id=report_id,
            draft_ref=request.draft_ref,
            draft_hash=request.draft_hash,
            coordinate=request.coordinate,
            context_hash=request.context_hash or request.context_view.view_hash,
            checks=tuple(deepcopy(dict(item)) for item in checks),
            overall_status=self._overall_status(checks, blocking, request.required_checks),
            blocking_findings=tuple(deepcopy(dict(item)) for item in blocking),
        )
        if report.report_hash != _review_payload_hash(report):
            raise ReviewServiceError("review report hash calculation failed", ErrorCode.INVALID_SCHEMA.value)
        with self._lock:
            existing = self.repository.get(report.report_id)
            if existing is not None:
                if existing.report_hash != report.report_hash:
                    raise ReviewServiceError(
                        "review replay has different content",
                        ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    )
                return existing
            saver = getattr(self.repository, "save_record", None)
            record = ReviewRecord(report=report, request=request)
            if callable(saver):
                saver(record)
            else:
                self.repository.save(report)
            return report

    @staticmethod
    def _overall_status(
        checks: Sequence[Mapping[str, Any]],
        blocking: Sequence[Mapping[str, Any]],
        required_checks: Sequence[str],
    ) -> str:
        statuses = {str(item.get("status", "INCOMPLETE")).upper() for item in checks}
        present = {str(item.get("check_id")) for item in checks}
        if any(item in statuses for item in {"REJECTED"}):
            return "REJECTED"
        if any(item in statuses for item in {"NEEDS_REVISION"}):
            return "NEEDS_REVISION"
        if blocking or any(item not in present for item in required_checks):
            return "INCOMPLETE"
        if not checks or "INCOMPLETE" in statuses:
            return "INCOMPLETE"
        return "PASSED"

    @staticmethod
    def _normalize_request(request: ReviewRequest | Mapping[str, Any]) -> ReviewRequest:
        if isinstance(request, ReviewRequest):
            return request.model_copy(deep=True)
        try:
            return ReviewRequest.model_validate(request)
        except PydanticValidationError as exc:
            raise ReviewServiceError("review request is invalid", ErrorCode.INVALID_SCHEMA.value) from exc

    def _validate_bindings(self, request: ReviewRequest) -> None:
        coordinate = request.coordinate
        context = request.context_view
        if context.work_id != coordinate.work_id or context.branch_id != coordinate.branch_id:
            raise ReviewServiceError("context view scope does not match coordinate", ErrorCode.INVALID_SCOPE.value)
        if context.scope.work_id != coordinate.work_id or context.scope.branch_id != coordinate.branch_id:
            raise ReviewServiceError("context view scope is foreign", ErrorCode.INVALID_SCOPE.value)
        expected_context_hash = context.view_hash
        if request.context_hash is not None and request.context_hash != expected_context_hash:
            raise ReviewServiceError("context hash does not match context view", ErrorCode.INVALID_SCHEMA.value)

        head = request.knowledge_head
        if head.work_id != coordinate.work_id or head.branch_id != coordinate.branch_id:
            raise ReviewServiceError("knowledge head scope does not match coordinate", ErrorCode.INVALID_SCOPE.value)
        if head.knowledge_version != coordinate.knowledge_version:
            raise ReviewServiceError("coordinate knowledge version is not the requested head", ErrorCode.STALE_VERSION.value)

        composite = request.source_composite
        if composite is None and not request.source_snapshot_ref:
            raise ReviewServiceError(
                "review requires a source composite or snapshot reference",
                ErrorCode.MISSING_CONTEXT.value,
            )
        if composite is not None:
            if composite.work_id != coordinate.work_id or composite.branch_id != coordinate.branch_id:
                raise ReviewServiceError("source composite scope does not match coordinate", ErrorCode.INVALID_SCOPE.value)
            snapshot_refs = set(composite.source_snapshot_refs)
            versions = set(composite.source_versions)
            source_ids = {item.source_id for item in composite.contributions}
            if request.source_snapshot_ref and request.source_snapshot_ref not in snapshot_refs:
                raise ReviewServiceError("source snapshot is not in composite", ErrorCode.INVALID_SCOPE.value)
            if request.source_version and request.source_version not in versions:
                raise ReviewServiceError("source version is not in composite", ErrorCode.STALE_VERSION.value)
            if coordinate.source_id and coordinate.source_id not in source_ids:
                raise ReviewServiceError("coordinate source is not in composite", ErrorCode.INVALID_SCOPE.value)
            if coordinate.source_version and coordinate.source_version not in versions:
                raise ReviewServiceError("coordinate source version is not in composite", ErrorCode.STALE_VERSION.value)
        elif request.source_version is None and coordinate.source_version is None:
            raise ReviewServiceError("source version is required with a snapshot reference", ErrorCode.INVALID_SCHEMA.value)
        if request.source_id and coordinate.source_id and request.source_id != coordinate.source_id:
            raise ReviewServiceError("request source_id does not match coordinate", ErrorCode.INVALID_SCOPE.value)
        if request.source_version and coordinate.source_version and request.source_version != coordinate.source_version:
            raise ReviewServiceError("request source_version does not match coordinate", ErrorCode.STALE_VERSION.value)

    def _invoke_reviewer(self, request: Mapping[str, Any]) -> Any:
        method = getattr(self.semantic_reviewer, "review", None)
        if callable(method):
            return method(request)
        if callable(self.semantic_reviewer):
            return self.semantic_reviewer(request)
        raise ReviewServiceError("semantic reviewer must expose review", ErrorCode.MODEL_UNAVAILABLE.value)

    @staticmethod
    def _extract_checks(raw: Any) -> list[Mapping[str, Any]]:
        if isinstance(raw, Mapping):
            value = raw.get("checks", raw.get("review_checks"))
            if value is None and "check_id" in raw:
                value = [raw]
        else:
            value = raw
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            result = [item for item in value if isinstance(item, Mapping)]
            if len(result) != len(value):
                raise ReviewServiceError("reviewer checks must be mappings", ErrorCode.INVALID_SCHEMA.value)
            return result
        raise ReviewServiceError("semantic reviewer returned no checks", ErrorCode.INVALID_SCHEMA.value)

    def _normalize_checks(
        self,
        checks: Sequence[Mapping[str, Any]],
        *,
        required_checks: Sequence[str],
        coordinate: StoryCoordinate,
    ) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
        normalized: list[Mapping[str, Any]] = []
        blocking: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        required = set(required_checks)
        for raw in checks:
            check_id = raw.get("check_id", raw.get("id"))
            try:
                check_id = _token(check_id, "check_id")
            except ValueError:
                blocking.append({"check_id": "invalid", "code": "INVALID_CHECK_ID"})
                continue
            if check_id in seen:
                blocking.append({"check_id": check_id, "code": "DUPLICATE_CHECK"})
                continue
            seen.add(check_id)
            status = str(raw.get("status", raw.get("overall_status", "INCOMPLETE"))).upper()
            status_aliases = {"PASS": "PASSED", "OK": "PASSED", "FAIL": "REJECTED"}
            status = status_aliases.get(status, status)
            if status not in {"PASSED", "NEEDS_REVISION", "REJECTED", "INCOMPLETE"}:
                status = "INCOMPLETE"
                blocking.append({"check_id": check_id, "code": "INVALID_CHECK_STATUS"})
            findings = raw.get("findings", ())
            if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
                findings = ({"code": "INVALID_FINDINGS"},)
                status = "INCOMPLETE"
            safe_findings = tuple(dict(item) for item in findings if isinstance(item, Mapping))
            if len(safe_findings) != len(findings):
                status = "INCOMPLETE"
                blocking.append({"check_id": check_id, "code": "INVALID_FINDINGS"})
            evidence = self._normalize_check_evidence(raw.get("evidence_refs", ()), coordinate, check_id)
            item = {
                "check_id": check_id,
                "status": status,
                "required": bool(raw.get("required", check_id in required)),
                "findings": safe_findings,
                "evidence_refs": tuple(ref.model_dump(mode="json") for ref in evidence),
            }
            if raw.get("error_code"):
                item["error_code"] = str(raw["error_code"])
            normalized.append(item)
            if item["required"] and status != "PASSED":
                blocking.append({"check_id": check_id, "code": f"CHECK_{status}"})
        for check_id in required:
            if check_id not in seen:
                blocking.append({"check_id": check_id, "code": "REQUIRED_CHECK_MISSING"})
        return tuple(normalized), tuple(blocking)

    @staticmethod
    def _normalize_check_evidence(
        values: Any,
        coordinate: StoryCoordinate,
        check_id: str,
    ) -> tuple[EvidenceRef, ...]:
        if values in (None, ()):
            return ()
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ReviewServiceError(
                f"check {check_id} evidence_refs must be a list",
                ErrorCode.INVALID_EVIDENCE.value,
            )
        result: list[EvidenceRef] = []
        for value in values:
            try:
                evidence = value if isinstance(value, EvidenceRef) else EvidenceRef.model_validate(value)
            except PydanticValidationError as exc:
                raise ReviewServiceError(
                    f"check {check_id} evidence is invalid",
                    ErrorCode.INVALID_EVIDENCE.value,
                ) from exc
            if evidence.scope is None or evidence.scope.work_id != coordinate.work_id or evidence.scope.branch_id != coordinate.branch_id:
                raise ReviewServiceError(
                    f"check {check_id} evidence scope is foreign",
                    ErrorCode.INVALID_SCOPE.value,
                )
            if coordinate.source_id and evidence.source_id != coordinate.source_id:
                raise ReviewServiceError(
                    f"check {check_id} evidence source is foreign",
                    ErrorCode.INVALID_SCOPE.value,
                )
            if coordinate.source_version and evidence.source_version != coordinate.source_version:
                raise ReviewServiceError(
                    f"check {check_id} evidence version is stale",
                    ErrorCode.STALE_VERSION.value,
                )
            result.append(evidence)
        return tuple(result)


__all__ = [
    "InMemoryReviewRepository",
    "ReviewRecord",
    "ReviewRepository",
    "ReviewRequest",
    "ReviewService",
    "ReviewServiceError",
    "SCHEMA_HASH",
    "SemanticReviewer",
]
