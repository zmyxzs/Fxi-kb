"""Atomic authority commit for the Fxi v3 review/proposal workflow."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterator, Mapping

from pydantic import Field, ValidationError as PydanticValidationError, model_validator

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .approval_service import ApprovalService, ApprovalServiceError
from .contracts import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    Actor,
    CommitReceipt,
    ContextView,
    ContractModel,
    ErrorCode,
    ReviewReport,
    StateChangeSet,
    _hash,
    _stable_hash,
    _token,
)
from .objects import Commit, KnowledgeHead, KnowledgeVersion
from .proposal_service import Proposal, ProposalRecord, ProposalRef, ProposalService, ProposalServiceError
from .review_service import _review_payload_hash
from .source_graph import SourceCompositeRef
from .state_change_service import StateChangeService, StateChangeServiceError, _change_set_hash


class CommitServiceError(FxiError):
    """A commit failed before authority state could be published."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class CommitExpectation(ContractModel):
    """All caller expectations needed for one atomic commit attempt."""

    work_id: str | None = None
    branch_id: str | None = None
    expected_knowledge_version: str
    chapter_version: str
    idempotency_key: str
    actor: Actor
    review: ReviewReport | None = None
    state_change_set: StateChangeSet | None = None
    context_view: ContextView | None = None
    knowledge_head: KnowledgeHead | None = None
    source_composite: SourceCompositeRef | None = None
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    draft_ref: str | None = None
    draft_hash: str | None = None
    chapter: Mapping[str, Any] = Field(default_factory=dict)
    projection_kinds: tuple[str, ...] = ("fts",)
    commit_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        for target, aliases in {
            "expected_knowledge_version": ("expected_version", "knowledge_version"),
            "context_view": ("context",),
            "state_change_set": ("state_changes",),
            "source_composite": ("source_graph",),
            "source_snapshot_ref": ("snapshot_ref",),
            "chapter": ("chapter_payload",),
        }.items():
            if result.get(target) is None:
                for alias in aliases:
                    if alias in result:
                        result[target] = result.pop(alias)
                        break
        return result

    @model_validator(mode="after")
    def validate_expectation(self) -> "CommitExpectation":
        _token(self.expected_knowledge_version, "expected_knowledge_version")
        _token(self.chapter_version, "chapter_version")
        _token(self.idempotency_key, "idempotency_key")
        if self.commit_id is not None:
            _token(self.commit_id, "commit_id")
        for field_name in (
            "work_id",
            "branch_id",
            "source_snapshot_ref",
            "source_id",
            "source_version",
            "draft_ref",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _token(value, field_name)
        if self.draft_hash is not None:
            _hash(self.draft_hash, "draft_hash")
        seen: set[str] = set()
        for kind in self.projection_kinds:
            normalized = _token(kind, "projection_kind")
            if normalized in seen:
                raise ValueError("projection_kinds must be unique")
            seen.add(normalized)
        return self


@dataclass(frozen=True)
class CommitRecord:
    commit: Commit
    receipt: CommitReceipt
    fingerprint: str
    knowledge_version: KnowledgeVersion


class InMemoryCommitRepository:
    """Transactional clean-room repository; no SQLite or source text access."""

    def __init__(self) -> None:
        self.heads: dict[tuple[str, str], KnowledgeHead] = {}
        self.knowledge_versions: dict[str, KnowledgeVersion] = {}
        self.commits: dict[str, Commit] = {}
        self.receipts: dict[tuple[str, str], CommitReceipt] = {}
        self.receipt_fingerprints: dict[tuple[str, str], str] = {}
        self.chapters: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.state_changes: list[dict[str, Any]] = []
        self.projection_tasks: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def get_head(self, work_id: str, branch_id: str) -> KnowledgeHead | None:
        with self._lock:
            value = self.heads.get((work_id, branch_id))
            return None if value is None else value.model_copy(deep=True)

    def seed_head(self, head: KnowledgeHead) -> None:
        with self._lock:
            self.heads[(head.work_id, head.branch_id)] = head.model_copy(deep=True)

    def get_receipt(self, work_id: str, idempotency_key: str) -> CommitReceipt | None:
        with self._lock:
            value = self.receipts.get((work_id, idempotency_key))
            return None if value is None else value.model_copy(deep=True)

    def get_fingerprint(self, work_id: str, idempotency_key: str) -> str | None:
        with self._lock:
            return self.receipt_fingerprints.get((work_id, idempotency_key))

    @contextmanager
    def transaction(self) -> Iterator["InMemoryCommitRepository"]:
        with self._lock:
            snapshot = {
                "heads": deepcopy(self.heads),
                "knowledge_versions": deepcopy(self.knowledge_versions),
                "commits": deepcopy(self.commits),
                "receipts": deepcopy(self.receipts),
                "receipt_fingerprints": deepcopy(self.receipt_fingerprints),
                "chapters": deepcopy(self.chapters),
                "state_changes": deepcopy(self.state_changes),
                "projection_tasks": deepcopy(self.projection_tasks),
            }
            try:
                yield self
            except Exception:
                self.heads = snapshot["heads"]
                self.knowledge_versions = snapshot["knowledge_versions"]
                self.commits = snapshot["commits"]
                self.receipts = snapshot["receipts"]
                self.receipt_fingerprints = snapshot["receipt_fingerprints"]
                self.chapters = snapshot["chapters"]
                self.state_changes = snapshot["state_changes"]
                self.projection_tasks = snapshot["projection_tasks"]
                raise

    def store(
        self,
        *,
        work_id: str,
        branch_id: str,
        idempotency_key: str,
        fingerprint: str,
        commit: Commit,
        receipt: CommitReceipt,
        knowledge_version: KnowledgeVersion,
        head: KnowledgeHead,
        chapter: Mapping[str, Any],
        changes: tuple[Mapping[str, Any], ...],
        projection_tasks: tuple[Mapping[str, Any], ...],
    ) -> None:
        key = (work_id, idempotency_key)
        existing = self.receipts.get(key)
        if existing is not None:
            existing_fingerprint = self.receipt_fingerprints[key]
            if existing_fingerprint != fingerprint:
                raise CommitServiceError(
                    "idempotency key is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            return
        chapter_key = (work_id, branch_id, commit.chapter_version)
        existing_chapter = self.chapters.get(chapter_key)
        if existing_chapter is not None and existing_chapter.get("draft_hash") != chapter.get("draft_hash"):
            raise CommitServiceError(
                "chapter version is already bound to different content",
                ErrorCode.IDEMPOTENCY_CONFLICT.value,
            )
        self.knowledge_versions[knowledge_version.knowledge_version] = knowledge_version
        self.heads[(work_id, branch_id)] = head.model_copy(deep=True)
        self.chapters[chapter_key] = deepcopy(dict(chapter))
        self.state_changes.extend(deepcopy(dict(item)) for item in changes)
        self.commits[commit.commit_id] = commit
        self.receipts[key] = receipt
        self.receipt_fingerprints[key] = fingerprint
        for task in projection_tasks:
            self.projection_tasks[str(task["task_id"])] = deepcopy(dict(task))


def _receipt_with_replay(receipt: CommitReceipt) -> CommitReceipt:
    return CommitReceipt(
        commit_id=receipt.commit_id,
        proposal_id=receipt.proposal_id,
        new_knowledge_version=receipt.new_knowledge_version,
        chapter_version=receipt.chapter_version,
        idempotent_replay=True,
    )


class CommitService:
    """Validate and atomically publish one approved proposal."""

    contract_revision = CONTRACT_REVISION
    schema_hash = SCHEMA_HASH

    def __init__(
        self,
        *,
        repository: InMemoryCommitRepository | None = None,
        proposal_service: ProposalService | None = None,
        approval_service: ApprovalService | None = None,
        state_change_service: StateChangeService | None = None,
    ) -> None:
        self.repository = repository or InMemoryCommitRepository()
        self.proposal_service = proposal_service
        self.approval_service = approval_service
        self.state_change_service = state_change_service or StateChangeService()

    def commit(
        self,
        proposal: Proposal | ProposalRef | Mapping[str, Any],
        approval: Any,
        expected: CommitExpectation | Mapping[str, Any],
    ) -> CommitReceipt:
        record = self._resolve_proposal(proposal)
        expectation = self._normalize_expectation(expected)
        work_id = expectation.work_id or record.review.coordinate.work_id
        branch_id = expectation.branch_id or record.review.coordinate.branch_id
        if work_id != record.review.coordinate.work_id or branch_id != record.review.coordinate.branch_id:
            raise CommitServiceError("commit scope does not match proposal", ErrorCode.INVALID_SCOPE.value)
        fingerprint = self._fingerprint(record, approval, expectation, work_id, branch_id)
        existing = self.repository.get_receipt(work_id, expectation.idempotency_key)
        if existing is not None:
            if self.repository.get_fingerprint(work_id, expectation.idempotency_key) != fingerprint:
                raise CommitServiceError(
                    "idempotency replay has different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            return _receipt_with_replay(existing)

        commit_id = expectation.commit_id or f"commit-{fingerprint}"
        prior_consumed_by: str | None = None
        approval_record = None
        try:
            with self.repository.transaction():
                # The second lookup is under the repository lock, closing the
                # race between the initial replay check and this transaction.
                existing = self.repository.get_receipt(work_id, expectation.idempotency_key)
                if existing is not None:
                    if self.repository.get_fingerprint(work_id, expectation.idempotency_key) != fingerprint:
                        raise CommitServiceError(
                            "idempotency replay has different content",
                            ErrorCode.IDEMPOTENCY_CONFLICT.value,
                        )
                    return _receipt_with_replay(existing)

                self._validate_proposal(record, expectation, work_id, branch_id)
                self._validate_review(record, expectation)
                self._validate_context(record, expectation, work_id, branch_id)
                self._validate_source(record, expectation, work_id, branch_id)
                state_change_set = self._validate_state(record, expectation)
                self._validate_head(record, expectation, work_id, branch_id)
                approval_record = self._validate_approval(
                    record,
                    approval,
                    expectation,
                    work_id,
                    branch_id,
                )
                prior_consumed_by = approval_record.consumed_by
                knowledge_version = self._build_knowledge_version(
                    record,
                    expectation,
                    state_change_set,
                    commit_id,
                )
                current_head = self.repository.get_head(work_id, branch_id)
                new_head = KnowledgeHead(
                    work_id=work_id,
                    branch_id=branch_id,
                    knowledge_version=knowledge_version.knowledge_version,
                    version_hash=knowledge_version.version_hash,
                    cas_revision=0 if current_head is None else current_head.cas_revision + 1,
                )
                chapter = self._chapter_payload(record, expectation, commit_id)
                commit_record = Commit(
                    commit_id=commit_id,
                    work_id=work_id,
                    branch_id=branch_id,
                    proposal_ref=record.proposal.proposal_id,
                    knowledge_version=knowledge_version.knowledge_version,
                    chapter_version=expectation.chapter_version,
                    actor=expectation.actor.actor_id,
                    idempotency_key=expectation.idempotency_key,
                )
                receipt = CommitReceipt(
                    commit_id=commit_id,
                    proposal_id=record.proposal.proposal_id,
                    new_knowledge_version=knowledge_version.knowledge_version,
                    chapter_version=expectation.chapter_version,
                )
                tasks = self._projection_tasks(
                    record,
                    expectation,
                    commit_id,
                    knowledge_version.knowledge_version,
                )
                persist = getattr(self.repository, "store", None)
                if not callable(persist):
                    persist = getattr(self.repository, "save", None)
                if not callable(persist):
                    raise CommitServiceError(
                        "commit repository cannot store records",
                        ErrorCode.INVALID_SCHEMA.value,
                    )
                persist(
                    work_id=work_id,
                    branch_id=branch_id,
                    idempotency_key=expectation.idempotency_key,
                    fingerprint=fingerprint,
                    commit=commit_record,
                    receipt=receipt,
                    knowledge_version=knowledge_version,
                    head=new_head,
                    chapter=chapter,
                    changes=tuple(state_change_set.changes),
                    projection_tasks=tasks,
                )
                if self.approval_service is None:
                    raise CommitServiceError("commit requires an approval service", ErrorCode.APPROVAL_REQUIRED.value)
                self.approval_service.consume(approval_record.approval.approval_id, commit_id)
                return receipt
        except Exception:
            if approval_record is not None and self.approval_service is not None:
                self.approval_service.restore_consumption(
                    approval_record.approval.approval_id,
                    prior_consumed_by,
                )
            raise

    def _resolve_proposal(self, value: Proposal | ProposalRef | Mapping[str, Any]) -> ProposalRecord:
        if self.proposal_service is None:
            raise CommitServiceError("commit requires a proposal service", ErrorCode.MISSING_CONTEXT.value)
        try:
            return self.proposal_service.resolve(value)
        except ProposalServiceError as exc:
            raise CommitServiceError(str(exc), exc.code) from exc

    @staticmethod
    def _normalize_expectation(expected: CommitExpectation | Mapping[str, Any]) -> CommitExpectation:
        if isinstance(expected, CommitExpectation):
            return expected.model_copy(deep=True)
        try:
            return CommitExpectation.model_validate(expected)
        except PydanticValidationError as exc:
            raise CommitServiceError("commit expectation is invalid", ErrorCode.INVALID_SCHEMA.value) from exc

    @staticmethod
    def _fingerprint(
        record: ProposalRecord,
        approval: Any,
        expectation: CommitExpectation,
        work_id: str,
        branch_id: str,
    ) -> str:
        approval_data = approval.model_dump(mode="json") if hasattr(approval, "model_dump") else approval
        return _stable_hash(
            {
                "contract_revision": CONTRACT_REVISION,
                "proposal": record.proposal,
                "review_hash": record.review.report_hash,
                "state_change_set_hash": record.state_change_set.change_set_hash,
                "context_hash": record.context_view.view_hash,
                "knowledge_head": record.knowledge_head,
                "approval": approval_data,
                "expectation": expectation,
                "work_id": work_id,
                "branch_id": branch_id,
            }
        )

    @staticmethod
    def _validate_proposal(
        record: ProposalRecord,
        expectation: CommitExpectation,
        work_id: str,
        branch_id: str,
    ) -> None:
        proposal = record.proposal
        if proposal.status != "PENDING_CONFIRMATION":
            raise CommitServiceError("proposal is not pending external approval", ErrorCode.APPROVAL_REQUIRED.value)
        if proposal.draft_ref != record.review.draft_ref or proposal.draft_hash != record.review.draft_hash:
            raise CommitServiceError("stored proposal draft binding is invalid", ErrorCode.INVALID_SCHEMA.value)
        if proposal.context_hash != record.context_view.view_hash:
            raise CommitServiceError("stored proposal context binding is invalid", ErrorCode.INVALID_SCHEMA.value)
        if proposal.knowledge_version != record.knowledge_head.knowledge_version:
            raise CommitServiceError("stored proposal knowledge binding is stale", ErrorCode.STALE_VERSION.value)
        if expectation.expected_knowledge_version != record.knowledge_head.knowledge_version:
            raise CommitServiceError("expected knowledge version does not match proposal", ErrorCode.STALE_VERSION.value)
        if record.review.coordinate.work_id != work_id or record.review.coordinate.branch_id != branch_id:
            raise CommitServiceError("proposal metadata has foreign scope", ErrorCode.INVALID_SCOPE.value)
        if expectation.draft_ref and expectation.draft_ref != proposal.draft_ref:
            raise CommitServiceError("commit draft_ref does not match proposal", ErrorCode.INVALID_SCHEMA.value)
        if expectation.draft_hash and expectation.draft_hash != proposal.draft_hash:
            raise CommitServiceError("commit draft_hash does not match proposal", ErrorCode.INVALID_SCHEMA.value)

    @staticmethod
    def _validate_review(record: ProposalRecord, expectation: CommitExpectation) -> None:
        report = record.review
        if report.report_hash != _review_payload_hash(report):
            raise CommitServiceError("stored review hash is not canonical", ErrorCode.INVALID_SCHEMA.value)
        if report.overall_status != "PASSED":
            raise CommitServiceError("commit requires a final passed review", ErrorCode.KNOWLEDGE_INSUFFICIENT.value)
        if report.report_id != record.proposal.review_ref:
            raise CommitServiceError("proposal review reference is invalid", ErrorCode.INVALID_SCHEMA.value)
        if expectation.review is not None and expectation.review.report_hash != report.report_hash:
            raise CommitServiceError("commit review differs from proposal review", ErrorCode.IDEMPOTENCY_CONFLICT.value)

    @staticmethod
    def _validate_context(
        record: ProposalRecord,
        expectation: CommitExpectation,
        work_id: str,
        branch_id: str,
    ) -> None:
        context = record.context_view
        if context.view_hash != record.proposal.context_hash:
            raise CommitServiceError("context hash does not match proposal", ErrorCode.INVALID_SCHEMA.value)
        if context.work_id != work_id or context.branch_id != branch_id:
            raise CommitServiceError("context view is foreign", ErrorCode.INVALID_SCOPE.value)
        if context.scope.work_id != work_id or context.scope.branch_id != branch_id:
            raise CommitServiceError("context scope is foreign", ErrorCode.INVALID_SCOPE.value)
        if context.completeness.upper() != "COMPLETE":
            raise CommitServiceError("context view is incomplete", ErrorCode.KNOWLEDGE_INSUFFICIENT.value)
        if context.conflicts:
            raise CommitServiceError("context view contains unresolved conflicts", ErrorCode.CONFLICTING_ASSERTIONS.value)
        if expectation.context_view is not None and expectation.context_view.view_hash != context.view_hash:
            raise CommitServiceError("commit context differs from proposal context", ErrorCode.IDEMPOTENCY_CONFLICT.value)

    @staticmethod
    def _validate_source(
        record: ProposalRecord,
        expectation: CommitExpectation,
        work_id: str,
        branch_id: str,
    ) -> None:
        coordinate = record.review.coordinate
        composite = record.source_composite
        if composite is None and not record.source_snapshot_ref:
            raise CommitServiceError("proposal has no source binding", ErrorCode.MISSING_CONTEXT.value)
        if composite is not None:
            if composite.work_id != work_id or composite.branch_id != branch_id:
                raise CommitServiceError("source composite is foreign", ErrorCode.INVALID_SCOPE.value)
            if expectation.source_composite is not None and expectation.source_composite.composite_hash != composite.composite_hash:
                raise CommitServiceError("commit source composite differs", ErrorCode.IDEMPOTENCY_CONFLICT.value)
            if expectation.source_snapshot_ref and expectation.source_snapshot_ref not in composite.source_snapshot_refs:
                raise CommitServiceError("commit source snapshot is outside composite", ErrorCode.INVALID_SCOPE.value)
        if expectation.source_id and coordinate.source_id and expectation.source_id != coordinate.source_id:
            raise CommitServiceError("commit source_id differs", ErrorCode.INVALID_SCOPE.value)
        if expectation.source_version and coordinate.source_version and expectation.source_version != coordinate.source_version:
            raise CommitServiceError("commit source_version is stale", ErrorCode.STALE_VERSION.value)

    def _validate_state(
        self,
        record: ProposalRecord,
        expectation: CommitExpectation,
    ) -> StateChangeSet:
        state = record.state_change_set
        if state.change_set_id != record.proposal.state_change_set_ref:
            raise CommitServiceError("proposal state change reference is invalid", ErrorCode.INVALID_SCHEMA.value)
        if state.change_set_hash != _change_set_hash(state):
            raise CommitServiceError("state change hash is not canonical", ErrorCode.INVALID_SCHEMA.value)
        try:
            result = self.state_change_service.validate(state, record.review)
        except StateChangeServiceError as exc:
            raise CommitServiceError(str(exc), exc.code) from exc
        if not result.valid:
            raise CommitServiceError("state change set is incomplete", ErrorCode.KNOWLEDGE_INSUFFICIENT.value)
        if expectation.state_change_set is not None and expectation.state_change_set.change_set_hash != state.change_set_hash:
            raise CommitServiceError("commit state changes differ", ErrorCode.IDEMPOTENCY_CONFLICT.value)
        return state

    def _validate_head(
        self,
        record: ProposalRecord,
        expectation: CommitExpectation,
        work_id: str,
        branch_id: str,
    ) -> None:
        expected_head = record.knowledge_head
        if expected_head.work_id != work_id or expected_head.branch_id != branch_id:
            raise CommitServiceError("proposal knowledge head is foreign", ErrorCode.INVALID_SCOPE.value)
        if expectation.knowledge_head is not None:
            if expectation.knowledge_head.knowledge_version != expected_head.knowledge_version or expectation.knowledge_head.version_hash != expected_head.version_hash:
                raise CommitServiceError("commit knowledge head differs", ErrorCode.STALE_VERSION.value)
        current = self.repository.get_head(work_id, branch_id)
        if current is not None and (
            current.knowledge_version != expectation.expected_knowledge_version
            and current.version_hash != expectation.expected_knowledge_version
        ):
            raise CommitServiceError("knowledge head changed during commit", ErrorCode.CAS_CONFLICT.value)

    def _validate_approval(
        self,
        record: ProposalRecord,
        approval: Any,
        expectation: CommitExpectation,
        work_id: str,
        branch_id: str,
    ) -> Any:
        if self.approval_service is None:
            raise CommitServiceError("commit requires an approval service", ErrorCode.APPROVAL_REQUIRED.value)
        try:
            return self.approval_service.validate_for_commit(
                approval,
                proposal=record,
                work_id=work_id,
                branch_id=branch_id,
                knowledge_version=expectation.expected_knowledge_version,
                actor=expectation.actor,
            )
        except ApprovalServiceError as exc:
            raise CommitServiceError(str(exc), exc.code) from exc

    @staticmethod
    def _build_knowledge_version(
        record: ProposalRecord,
        expectation: CommitExpectation,
        state: StateChangeSet,
        commit_id: str,
    ) -> KnowledgeVersion:
        identity = {
            "parent": expectation.expected_knowledge_version,
            "proposal": record.proposal.proposal_hash,
            "state_change_set": state.change_set_hash,
            "commit_id": commit_id,
        }
        version_id = f"knowledge-{_stable_hash(identity)}"
        claim_refs: list[str] = []
        relation_refs: list[str] = []
        object_refs: list[str] = []
        for change in state.changes:
            for key, target in (
                ("claim_refs", claim_refs),
                ("relation_refs", relation_refs),
                ("object_refs", object_refs),
            ):
                values = change.get(key, ())
                if isinstance(values, str):
                    values = (values,)
                if isinstance(values, (tuple, list)):
                    for value in values:
                        if isinstance(value, str) and value not in target:
                            target.append(value)
        snapshots = ()
        if record.source_composite is not None:
            snapshots = tuple(record.source_composite.source_snapshot_refs)
        return KnowledgeVersion(
            knowledge_version=version_id,
            work_id=record.review.coordinate.work_id,
            branch_id=record.review.coordinate.branch_id,
            parent_version=expectation.expected_knowledge_version,
            object_refs=tuple(object_refs),
            claim_refs=tuple(claim_refs),
            relation_refs=tuple(relation_refs),
            source_snapshot_refs=snapshots,
            status="APPROVED",
            actor=expectation.actor.actor_id,
        )

    @staticmethod
    def _chapter_payload(
        record: ProposalRecord,
        expectation: CommitExpectation,
        commit_id: str,
    ) -> dict[str, Any]:
        payload = deepcopy(dict(expectation.chapter))
        payload.update(
            {
                "commit_id": commit_id,
                "work_id": record.review.coordinate.work_id,
                "branch_id": record.review.coordinate.branch_id,
                "chapter_version": expectation.chapter_version,
                "draft_ref": record.proposal.draft_ref,
                "draft_hash": record.proposal.draft_hash,
                "proposal_id": record.proposal.proposal_id,
                "review_ref": record.proposal.review_ref,
                "context_hash": record.proposal.context_hash,
            }
        )
        text = payload.get("text")
        text_hash = payload.get("text_hash")
        if text is not None and text_hash is not None and sha256_hex(text) != text_hash:
            raise CommitServiceError("chapter text hash does not match content", ErrorCode.INVALID_SCHEMA.value)
        return payload

    @staticmethod
    def _projection_tasks(
        record: ProposalRecord,
        expectation: CommitExpectation,
        commit_id: str,
        knowledge_version: str,
    ) -> tuple[Mapping[str, Any], ...]:
        tasks: list[Mapping[str, Any]] = []
        for kind in expectation.projection_kinds:
            task_id = f"projection-{kind}-{knowledge_version}"
            tasks.append(
                {
                    "task_id": task_id,
                    "projection_kind": kind,
                    "status": "PENDING",
                    "rebuildable": True,
                    "commit_id": commit_id,
                    "knowledge_version": knowledge_version,
                    "proposal_id": record.proposal.proposal_id,
                }
            )
        return tuple(tasks)


__all__ = [
    "CommitExpectation",
    "CommitRecord",
    "CommitService",
    "CommitServiceError",
    "InMemoryCommitRepository",
    "SCHEMA_HASH",
]
