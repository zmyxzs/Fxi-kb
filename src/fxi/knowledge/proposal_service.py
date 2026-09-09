"""Proposal creation after final review and explicit state changes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

from pydantic import ValidationError as PydanticValidationError, model_validator

from fxi.core.exceptions import FxiError

from .contracts import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    ContextView,
    ContractModel,
    ErrorCode,
    Proposal,
    ReviewReport,
    StateChangeSet,
    _hash,
    _stable_hash,
    _token,
)
from .objects import KnowledgeHead
from .review_service import _review_payload_hash
from .source_graph import SourceCompositeRef
from .state_change_service import StateChangeService, StateChangeServiceError, _change_set_hash


class ProposalServiceError(FxiError):
    """A proposal did not meet the final-review binding contract."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class ProposalRef(ContractModel):
    """The small reference accepted by approval and commit ports."""

    proposal_id: str
    proposal_hash: str

    @model_validator(mode="after")
    def validate_ref(self) -> "ProposalRef":
        _token(self.proposal_id, "proposal_id")
        _hash(self.proposal_hash, "proposal_hash")
        return self


class ProposalRequest(ContractModel):
    """Strict request containing all immutable inputs needed by a proposal."""

    draft_ref: str
    draft_hash: str
    review: ReviewReport
    state_change_set: StateChangeSet
    context_view: ContextView
    knowledge_head: KnowledgeHead
    source_composite: SourceCompositeRef | None = None
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    proposal_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        aliases = {
            "review": "review_report",
            "state_change_set": "state_changes",
            "context_view": "context",
            "source_composite": "source_graph",
            "source_snapshot_ref": "snapshot_ref",
        }
        for target, alias in aliases.items():
            if target not in result and alias in result:
                result[target] = result.pop(alias)
        return result

    @model_validator(mode="after")
    def validate_request(self) -> "ProposalRequest":
        _token(self.draft_ref, "draft_ref")
        _hash(self.draft_hash, "draft_hash")
        for field_name in ("source_snapshot_ref", "source_id", "source_version", "proposal_id"):
            value = getattr(self, field_name)
            if value is not None:
                _token(value, field_name)
        return self


@dataclass(frozen=True)
class ProposalRecord:
    proposal: Proposal
    review: ReviewReport
    state_change_set: StateChangeSet
    context_view: ContextView
    knowledge_head: KnowledgeHead
    source_composite: SourceCompositeRef | None = None
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None


class ProposalRepository(Protocol):
    def get(self, proposal_id: str) -> Proposal | None: ...

    def save(self, proposal: Proposal) -> None: ...


class InMemoryProposalRepository:
    """Immutable proposal records for API adapters and clean-room tests."""

    def __init__(self) -> None:
        self._records: dict[str, ProposalRecord] = {}
        self._lock = RLock()

    def get(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            record = self._records.get(proposal_id)
            return None if record is None else record.proposal.model_copy(deep=True)

    def get_record(self, proposal_id: str) -> ProposalRecord | None:
        with self._lock:
            record = self._records.get(proposal_id)
            return None if record is None else deepcopy(record)

    def save(self, proposal: Proposal) -> None:
        with self._lock:
            existing = self._records.get(proposal.proposal_id)
            if existing is not None and existing.proposal.proposal_hash != proposal.proposal_hash:
                raise ProposalServiceError(
                    "proposal_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            if existing is None:
                raise ProposalServiceError(
                    "proposal metadata is required; use save_record",
                    ErrorCode.INVALID_SCHEMA.value,
                )

    def save_record(self, record: ProposalRecord) -> None:
        with self._lock:
            existing = self._records.get(record.proposal.proposal_id)
            if existing is not None and existing.proposal.proposal_hash != record.proposal.proposal_hash:
                raise ProposalServiceError(
                    "proposal_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            self._records[record.proposal.proposal_id] = deepcopy(record)


def proposal_payload_hash(proposal: Proposal) -> str:
    payload = {
        key: getattr(proposal, key)
        for key in (
            "proposal_id",
            "draft_ref",
            "draft_hash",
            "review_ref",
            "state_change_set_ref",
            "context_hash",
            "knowledge_version",
            "status",
        )
    }
    return _stable_hash(payload)


class ProposalService:
    """Create a pending proposal without approving or applying it."""

    contract_revision = CONTRACT_REVISION
    schema_hash = SCHEMA_HASH

    def __init__(
        self,
        *,
        repository: ProposalRepository | None = None,
        state_change_service: StateChangeService | None = None,
    ) -> None:
        self.repository = repository or InMemoryProposalRepository()
        self.state_change_service = state_change_service or StateChangeService()
        self._lock = RLock()

    def create(self, request: ProposalRequest | Mapping[str, Any]) -> Proposal:
        normalized = self._normalize_request(request)
        self._validate_review(normalized)
        self._validate_context(normalized)
        self._validate_knowledge_head(normalized)
        self._validate_source(normalized)
        try:
            state_result = self.state_change_service.validate(
                normalized.state_change_set,
                normalized.review,
            )
        except StateChangeServiceError as exc:
            raise ProposalServiceError(str(exc), exc.code) from exc
        if not state_result.valid:
            raise ProposalServiceError(
                "state change set is not formally submittable",
                ErrorCode.KNOWLEDGE_INSUFFICIENT.value,
            )

        identity = {
            "draft_ref": normalized.draft_ref,
            "draft_hash": normalized.draft_hash,
            "review_ref": normalized.review.report_id,
            "review_hash": normalized.review.report_hash,
            "state_change_set_ref": normalized.state_change_set.change_set_id,
            "state_change_set_hash": normalized.state_change_set.change_set_hash,
            "context_hash": normalized.context_view.view_hash,
            "knowledge_version": normalized.knowledge_head.knowledge_version,
        }
        proposal_id = normalized.proposal_id or f"proposal-{_stable_hash(identity)}"
        try:
            proposal_id = _token(proposal_id, "proposal_id")
        except ValueError as exc:
            raise ProposalServiceError("proposal_id is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
        proposal = Proposal(
            proposal_id=proposal_id,
            draft_ref=normalized.draft_ref,
            draft_hash=normalized.draft_hash,
            review_ref=normalized.review.report_id,
            state_change_set_ref=normalized.state_change_set.change_set_id,
            context_hash=normalized.context_view.view_hash,
            knowledge_version=normalized.knowledge_head.knowledge_version,
            status="PENDING_CONFIRMATION",
        )
        if proposal.proposal_hash != proposal_payload_hash(proposal):
            raise ProposalServiceError("proposal hash calculation failed", ErrorCode.INVALID_SCHEMA.value)
        record = ProposalRecord(
            proposal=proposal,
            review=normalized.review,
            state_change_set=normalized.state_change_set,
            context_view=normalized.context_view,
            knowledge_head=normalized.knowledge_head,
            source_composite=normalized.source_composite,
            source_snapshot_ref=normalized.source_snapshot_ref,
            source_id=normalized.source_id or normalized.review.coordinate.source_id,
            source_version=normalized.source_version or normalized.review.coordinate.source_version,
        )
        with self._lock:
            existing = self._get_record_optional(proposal.proposal_id)
            if existing is not None:
                if existing.proposal.proposal_hash != proposal.proposal_hash:
                    raise ProposalServiceError(
                        "proposal replay has different content",
                        ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    )
                return existing.proposal
            saver = getattr(self.repository, "save_record", None)
            if callable(saver):
                saver(record)
            else:
                self.repository.save(proposal)
        return proposal

    def get(self, proposal_id: str) -> Proposal:
        proposal = self.repository.get(proposal_id)
        if proposal is None:
            raise ProposalServiceError(f"proposal not found: {proposal_id}", ErrorCode.NOT_FOUND.value)
        return proposal

    def get_record(self, proposal_id: str) -> ProposalRecord:
        record = self._get_record_optional(proposal_id)
        if record is None:
            raise ProposalServiceError(f"proposal not found: {proposal_id}", ErrorCode.NOT_FOUND.value)
        return record

    def resolve(self, value: Proposal | ProposalRef | str | Mapping[str, Any]) -> ProposalRecord:
        if isinstance(value, ProposalRef):
            proposal_id = value.proposal_id
            expected_hash = value.proposal_hash
        elif isinstance(value, Proposal):
            proposal_id = value.proposal_id
            expected_hash = value.proposal_hash
        elif isinstance(value, str):
            proposal_id = value
            expected_hash = None
        elif isinstance(value, Mapping):
            try:
                ref = ProposalRef.model_validate(value)
            except PydanticValidationError as exc:
                raise ProposalServiceError("proposal reference is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
            proposal_id = ref.proposal_id
            expected_hash = ref.proposal_hash
        else:
            raise ProposalServiceError("proposal reference is invalid", ErrorCode.INVALID_SCHEMA.value)
        record = self.get_record(proposal_id)
        if expected_hash is not None and expected_hash != record.proposal.proposal_hash:
            raise ProposalServiceError("proposal hash does not match stored content", ErrorCode.IDEMPOTENCY_CONFLICT.value)
        return record

    def _get_record_optional(self, proposal_id: str) -> ProposalRecord | None:
        getter = getattr(self.repository, "get_record", None)
        if callable(getter):
            return getter(proposal_id)
        proposal = self.repository.get(proposal_id)
        return None if proposal is None else ProposalRecord(
            proposal=proposal,
            review=_placeholder_review(proposal),
            state_change_set=_placeholder_state_change(proposal),
            context_view=_placeholder_context(proposal),
            knowledge_head=KnowledgeHead(
                work_id="unknown-work",
                branch_id="unknown-branch",
                knowledge_version=proposal.knowledge_version,
                version_hash="0" * 64,
            ),
        )

    @staticmethod
    def _normalize_request(request: ProposalRequest | Mapping[str, Any]) -> ProposalRequest:
        if isinstance(request, ProposalRequest):
            return request.model_copy(deep=True)
        try:
            return ProposalRequest.model_validate(request)
        except PydanticValidationError as exc:
            raise ProposalServiceError("proposal request is invalid", ErrorCode.INVALID_SCHEMA.value) from exc

    @staticmethod
    def _validate_review(request: ProposalRequest) -> None:
        report = request.review
        if report.report_hash != _review_payload_hash(report):
            raise ProposalServiceError("review report hash is not canonical", ErrorCode.INVALID_SCHEMA.value)
        if report.overall_status != "PASSED":
            raise ProposalServiceError("proposal requires a final passed review", ErrorCode.KNOWLEDGE_INSUFFICIENT.value)
        if report.draft_ref != request.draft_ref or report.draft_hash != request.draft_hash:
            raise ProposalServiceError("proposal draft binding does not match review", ErrorCode.INVALID_SCHEMA.value)
        if report.coordinate.work_id != request.knowledge_head.work_id or report.coordinate.branch_id != request.knowledge_head.branch_id:
            raise ProposalServiceError("review and knowledge head scopes differ", ErrorCode.INVALID_SCOPE.value)

    @staticmethod
    def _validate_context(request: ProposalRequest) -> None:
        context = request.context_view
        coordinate = request.review.coordinate
        if context.view_hash != request.context_view.view_hash:
            raise ProposalServiceError("context view hash is not canonical", ErrorCode.INVALID_SCHEMA.value)
        if context.work_id != coordinate.work_id or context.branch_id != coordinate.branch_id:
            raise ProposalServiceError("context view scope does not match review", ErrorCode.INVALID_SCOPE.value)
        if context.scope.work_id != coordinate.work_id or context.scope.branch_id != coordinate.branch_id:
            raise ProposalServiceError("context scope is foreign", ErrorCode.INVALID_SCOPE.value)

    @staticmethod
    def _validate_knowledge_head(request: ProposalRequest) -> None:
        head = request.knowledge_head
        coordinate = request.review.coordinate
        if head.work_id != coordinate.work_id or head.branch_id != coordinate.branch_id:
            raise ProposalServiceError("knowledge head scope is foreign", ErrorCode.INVALID_SCOPE.value)
        if head.knowledge_version != coordinate.knowledge_version:
            raise ProposalServiceError("knowledge head version does not match review", ErrorCode.STALE_VERSION.value)

    @staticmethod
    def _validate_source(request: ProposalRequest) -> None:
        coordinate = request.review.coordinate
        composite = request.source_composite
        if composite is None and not request.source_snapshot_ref:
            raise ProposalServiceError("proposal requires source binding", ErrorCode.MISSING_CONTEXT.value)
        if composite is not None:
            if composite.work_id != coordinate.work_id or composite.branch_id != coordinate.branch_id:
                raise ProposalServiceError("source composite scope is foreign", ErrorCode.INVALID_SCOPE.value)
            if request.source_snapshot_ref and request.source_snapshot_ref not in composite.source_snapshot_refs:
                raise ProposalServiceError("source snapshot is not in composite", ErrorCode.INVALID_SCOPE.value)
            if request.source_version and request.source_version not in composite.source_versions:
                raise ProposalServiceError("source version is not in composite", ErrorCode.STALE_VERSION.value)
        if request.source_id and coordinate.source_id and request.source_id != coordinate.source_id:
            raise ProposalServiceError("source_id does not match coordinate", ErrorCode.INVALID_SCOPE.value)
        if request.source_version and coordinate.source_version and request.source_version != coordinate.source_version:
            raise ProposalServiceError("source_version does not match coordinate", ErrorCode.STALE_VERSION.value)


def _placeholder_review(proposal: Proposal) -> ReviewReport:
    """Fallback for a repository that stores only public proposals.

    It is deliberately not usable for a commit: the placeholder lacks a real
    context/source record and downstream validation rejects it.
    """

    raise ProposalServiceError(
        "repository must expose proposal metadata for downstream validation",
        ErrorCode.MISSING_CONTEXT.value,
    )


def _placeholder_state_change(proposal: Proposal) -> StateChangeSet:
    raise ProposalServiceError("proposal metadata is unavailable", ErrorCode.MISSING_CONTEXT.value)


def _placeholder_context(proposal: Proposal) -> ContextView:
    raise ProposalServiceError("proposal metadata is unavailable", ErrorCode.MISSING_CONTEXT.value)


__all__ = [
    "InMemoryProposalRepository",
    "ProposalRecord",
    "ProposalRef",
    "ProposalRepository",
    "ProposalRequest",
    "ProposalService",
    "ProposalServiceError",
    "SCHEMA_HASH",
    "proposal_payload_hash",
]
