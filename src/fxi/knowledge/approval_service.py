"""Explicit human approval for Fxi v3 proposals."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from time import time
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError, model_validator

from fxi.core.exceptions import FxiError

from .contracts import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    Actor,
    ApprovalRef,
    ContractModel,
    ErrorCode,
    _hash,
    _stable_hash,
    _token,
)
from .proposal_service import Proposal, ProposalRef, ProposalRecord, ProposalService, ProposalServiceError


class ApprovalServiceError(FxiError):
    """An approval failed authorization, expiry, or binding checks."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class ApprovalRequest(ContractModel):
    """Strict approval input; actor is supplied separately as an authority."""

    proposal_id: str
    proposal_hash: str | None = None
    work_id: str | None = None
    branch_id: str | None = None
    knowledge_version: str | None = None
    action: str = "COMMIT"
    expires_at: int | str | None = None
    approval_id: str | None = None
    idempotency_key: str | None = None
    approval_hash: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        proposal = result.pop("proposal", None)
        if proposal is not None:
            if isinstance(proposal, Mapping):
                result.setdefault("proposal_id", proposal.get("proposal_id"))
                result.setdefault("proposal_hash", proposal.get("proposal_hash"))
            else:
                result.setdefault("proposal_id", getattr(proposal, "proposal_id", None))
                result.setdefault("proposal_hash", getattr(proposal, "proposal_hash", None))
        for target, aliases in {
            "knowledge_version": ("expected_knowledge_version", "expected_version"),
            "idempotency_key": ("request_id",),
        }.items():
            if result.get(target) is None:
                for alias in aliases:
                    if alias in result:
                        result[target] = result.pop(alias)
                        break
        return result

    @model_validator(mode="after")
    def validate_request(self) -> "ApprovalRequest":
        _token(self.proposal_id, "proposal_id")
        for field_name in ("proposal_hash", "approval_hash"):
            value = getattr(self, field_name)
            if value is not None:
                _hash(value, field_name)
        for field_name in ("work_id", "branch_id", "knowledge_version", "approval_id", "idempotency_key"):
            value = getattr(self, field_name)
            if value is not None:
                _token(value, field_name)
        if not isinstance(self.action, str) or not self.action.strip():
            raise ValueError("action must be non-empty")
        if isinstance(self.expires_at, bool):
            raise ValueError("expires_at cannot be boolean")
        if self.expires_at is not None and not isinstance(self.expires_at, (int, str)):
            raise ValueError("expires_at must be an integer, ISO timestamp, or null")
        return self


@dataclass(frozen=True)
class ApprovalRecord:
    approval: ApprovalRef
    proposal_hash: str
    work_id: str
    branch_id: str
    knowledge_version: str
    action: str
    idempotency_key: str
    consumed_by: str | None = None

    @property
    def consumed(self) -> bool:
        return self.consumed_by is not None


class ApprovalRepository(Protocol):
    def get(self, approval_id: str) -> ApprovalRef | None: ...

    def get_record(self, approval_id: str) -> ApprovalRecord | None: ...

    def save_record(self, record: ApprovalRecord) -> None: ...


class InMemoryApprovalRepository:
    """Approval authority store with one-way consumption under a lock."""

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}
        self._by_idempotency: dict[tuple[str, str], str] = {}
        self._lock = RLock()

    def get(self, approval_id: str) -> ApprovalRef | None:
        with self._lock:
            record = self._records.get(approval_id)
            return None if record is None else self._public_ref(record)

    def get_record(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            record = self._records.get(approval_id)
            return None if record is None else deepcopy(record)

    def get_by_idempotency(self, proposal_id: str, idempotency_key: str) -> ApprovalRecord | None:
        with self._lock:
            approval_id = self._by_idempotency.get((proposal_id, idempotency_key))
            record = None if approval_id is None else self._records.get(approval_id)
            return None if record is None else deepcopy(record)

    def save_record(self, record: ApprovalRecord) -> None:
        with self._lock:
            existing = self._records.get(record.approval.approval_id)
            if existing is not None and _record_fingerprint(existing) != _record_fingerprint(record):
                raise ApprovalServiceError(
                    "approval_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            existing_key = self._by_idempotency.get((record.approval.proposal_id, record.idempotency_key))
            if existing_key is not None and existing_key != record.approval.approval_id:
                other = self._records[existing_key]
                if _record_fingerprint(other) != _record_fingerprint(record):
                    raise ApprovalServiceError(
                        "approval idempotency key is already bound to different content",
                        ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    )
            self._records[record.approval.approval_id] = deepcopy(record)
            self._by_idempotency[(record.approval.proposal_id, record.idempotency_key)] = record.approval.approval_id

    def consume(self, approval_id: str, commit_id: str) -> ApprovalRecord:
        with self._lock:
            record = self._records.get(approval_id)
            if record is None:
                raise ApprovalServiceError("approval not found", ErrorCode.APPROVAL_REQUIRED.value)
            if record.consumed_by is not None:
                raise ApprovalServiceError("approval has already been consumed", ErrorCode.APPROVAL_REQUIRED.value)
            updated = replace(record, consumed_by=commit_id)
            self._records[approval_id] = updated
            return deepcopy(updated)

    def restore_consumption(self, approval_id: str, consumed_by: str | None) -> None:
        with self._lock:
            record = self._records.get(approval_id)
            if record is None:
                return
            self._records[approval_id] = replace(record, consumed_by=consumed_by)

    @staticmethod
    def _public_ref(record: ApprovalRecord) -> ApprovalRef:
        return record.approval.model_copy(update={"consumed": record.consumed})


def _record_fingerprint(record: ApprovalRecord) -> str:
    return _stable_hash(
        {
            "approval": record.approval,
            "proposal_hash": record.proposal_hash,
            "work_id": record.work_id,
            "branch_id": record.branch_id,
            "knowledge_version": record.knowledge_version,
            "action": record.action,
            "idempotency_key": record.idempotency_key,
            "consumed_by": record.consumed_by,
        }
    )


def approval_payload_hash(
    *,
    approval_id: str,
    proposal_id: str,
    proposal_hash: str,
    actor: Actor,
    work_id: str,
    branch_id: str,
    knowledge_version: str,
    action: str,
    expires_at: int | str | None,
    idempotency_key: str,
) -> str:
    return _stable_hash(
        {
            "approval_id": approval_id,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "actor": actor,
            "work_id": work_id,
            "branch_id": branch_id,
            "knowledge_version": knowledge_version,
            "action": action,
            "expires_at": expires_at,
            "idempotency_key": idempotency_key,
        }
    )


class ApprovalService:
    """Issue and consume approvals; approval is never inferred from a proposal."""

    contract_revision = CONTRACT_REVISION
    schema_hash = SCHEMA_HASH
    DEFAULT_ALLOWED_ROLES = frozenset({"approver", "semantic_reviewer", "reviewer", "editor", "owner"})

    def __init__(
        self,
        *,
        proposal_service: ProposalService | None = None,
        repository: ApprovalRepository | None = None,
        allowed_roles: set[str] | frozenset[str] | tuple[str, ...] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.proposal_service = proposal_service
        self.repository = repository or InMemoryApprovalRepository()
        self.allowed_roles = frozenset(item.lower() for item in (allowed_roles or self.DEFAULT_ALLOWED_ROLES))
        self.clock = clock or time
        self._lock = RLock()

    def create(self, request: ApprovalRequest | Mapping[str, Any], actor: Actor | Mapping[str, Any]) -> ApprovalRef:
        normalized = self._normalize_request(request)
        try:
            actor_obj = actor if isinstance(actor, Actor) else Actor.model_validate(actor)
        except PydanticValidationError as exc:
            raise ApprovalServiceError("approval actor is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
        role = actor_obj.role.lower()
        if role == "writer" or role not in self.allowed_roles:
            raise ApprovalServiceError(
                "actor is not allowed to approve proposals",
                ErrorCode.AUTHORIZATION_FAILED.value,
            )

        proposal_record = self._resolve_proposal(normalized)
        proposal_hash = proposal_record.proposal.proposal_hash if proposal_record is not None else normalized.proposal_hash
        if not proposal_hash:
            raise ApprovalServiceError("approval requires a proposal hash", ErrorCode.INVALID_SCHEMA.value)
        if normalized.proposal_hash is not None and normalized.proposal_hash != proposal_hash:
            raise ApprovalServiceError("proposal hash does not match stored proposal", ErrorCode.IDEMPOTENCY_CONFLICT.value)
        work_id = proposal_record.review.coordinate.work_id if proposal_record is not None else normalized.work_id
        branch_id = proposal_record.review.coordinate.branch_id if proposal_record is not None else normalized.branch_id
        knowledge_version = proposal_record.knowledge_head.knowledge_version if proposal_record is not None else normalized.knowledge_version
        if not work_id or not branch_id or not knowledge_version:
            raise ApprovalServiceError("approval scope and expected knowledge version are required", ErrorCode.INVALID_SCOPE.value)
        if normalized.work_id and normalized.work_id != work_id:
            raise ApprovalServiceError("approval work_id does not match proposal", ErrorCode.INVALID_SCOPE.value)
        if normalized.branch_id and normalized.branch_id != branch_id:
            raise ApprovalServiceError("approval branch_id does not match proposal", ErrorCode.INVALID_SCOPE.value)
        if normalized.knowledge_version and normalized.knowledge_version != knowledge_version:
            raise ApprovalServiceError("approval knowledge version does not match proposal", ErrorCode.STALE_VERSION.value)
        if actor_obj.scope is not None and (
            actor_obj.scope.work_id != work_id or actor_obj.scope.branch_id != branch_id
        ):
            raise ApprovalServiceError("approval actor scope is foreign", ErrorCode.INVALID_SCOPE.value)
        action = self._normalize_action(normalized.action)
        self._validate_expiry(normalized.expires_at)
        idempotency_key = normalized.idempotency_key or f"approval-key-{normalized.proposal_id}"
        approval_identity = {
            "proposal_id": normalized.proposal_id,
            "proposal_hash": proposal_hash,
            "actor": actor_obj,
            "work_id": work_id,
            "branch_id": branch_id,
            "knowledge_version": knowledge_version,
            "action": action,
            "expires_at": normalized.expires_at,
            "idempotency_key": idempotency_key,
        }
        approval_id = normalized.approval_id or f"approval-{_stable_hash(approval_identity)}"
        try:
            _token(approval_id, "approval_id")
        except ValueError as exc:
            raise ApprovalServiceError("approval_id is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
        expected_hash = approval_payload_hash(
            approval_id=approval_id,
            proposal_id=normalized.proposal_id,
            proposal_hash=proposal_hash,
            actor=actor_obj,
            work_id=work_id,
            branch_id=branch_id,
            knowledge_version=knowledge_version,
            action=action,
            expires_at=normalized.expires_at,
            idempotency_key=idempotency_key,
        )
        if normalized.approval_hash is not None and normalized.approval_hash != expected_hash:
            raise ApprovalServiceError("approval hash is not canonical", ErrorCode.INVALID_SCHEMA.value)
        approval = ApprovalRef(
            approval_id=approval_id,
            proposal_id=normalized.proposal_id,
            actor=actor_obj,
            approval_hash=expected_hash,
            expires_at=normalized.expires_at,
        )
        record = ApprovalRecord(
            approval=approval,
            proposal_hash=proposal_hash,
            work_id=work_id,
            branch_id=branch_id,
            knowledge_version=knowledge_version,
            action=action,
            idempotency_key=idempotency_key,
        )
        with self._lock:
            getter = getattr(self.repository, "get_by_idempotency", None)
            existing = getter(normalized.proposal_id, idempotency_key) if callable(getter) else None
            if existing is not None:
                if _record_fingerprint(existing) != _record_fingerprint(record):
                    raise ApprovalServiceError(
                        "approval replay has different content",
                        ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    )
                return existing.approval.model_copy(update={"consumed": existing.consumed})
            self.repository.save_record(record)
        return approval

    def get(self, approval_id: str) -> ApprovalRef:
        approval = self.repository.get(approval_id)
        if approval is None:
            raise ApprovalServiceError("approval not found", ErrorCode.APPROVAL_REQUIRED.value)
        return approval

    def get_record(self, approval_id: str) -> ApprovalRecord:
        getter = getattr(self.repository, "get_record", None)
        record = getter(approval_id) if callable(getter) else None
        if record is None:
            raise ApprovalServiceError("approval not found", ErrorCode.APPROVAL_REQUIRED.value)
        return record

    def consume(self, approval_id: str, commit_id: str) -> ApprovalRecord:
        consumer = getattr(self.repository, "consume", None)
        if not callable(consumer):
            raise ApprovalServiceError("approval repository cannot consume approvals", ErrorCode.INVALID_SCHEMA.value)
        return consumer(approval_id, commit_id)

    def restore_consumption(self, approval_id: str, consumed_by: str | None) -> None:
        restore = getattr(self.repository, "restore_consumption", None)
        if callable(restore):
            restore(approval_id, consumed_by)

    def validate_for_commit(
        self,
        approval: ApprovalRef,
        *,
        proposal: ProposalRecord,
        work_id: str,
        branch_id: str,
        knowledge_version: str,
        actor: Actor,
    ) -> ApprovalRecord:
        if not isinstance(approval, ApprovalRef):
            raise ApprovalServiceError("commit requires an ApprovalRef", ErrorCode.APPROVAL_REQUIRED.value)
        if approval.proposal_id != proposal.proposal.proposal_id:
            raise ApprovalServiceError("approval proposal does not match commit proposal", ErrorCode.APPROVAL_REQUIRED.value)
        record = self.get_record(approval.approval_id)
        if record.approval.approval_hash != approval.approval_hash:
            raise ApprovalServiceError("approval hash does not match stored approval", ErrorCode.IDEMPOTENCY_CONFLICT.value)
        if record.proposal_hash != proposal.proposal.proposal_hash:
            raise ApprovalServiceError("approval proposal hash is stale", ErrorCode.STALE_VERSION.value)
        if record.work_id != work_id or record.branch_id != branch_id:
            raise ApprovalServiceError("approval scope is foreign", ErrorCode.INVALID_SCOPE.value)
        if record.knowledge_version != knowledge_version:
            raise ApprovalServiceError("approval knowledge version is stale", ErrorCode.STALE_VERSION.value)
        if record.action != "COMMIT":
            raise ApprovalServiceError("approval action is not commit", ErrorCode.APPROVAL_REQUIRED.value)
        if record.approval.actor.actor_id != actor.actor_id:
            raise ApprovalServiceError("approval actor does not match commit actor", ErrorCode.AUTHORIZATION_FAILED.value)
        if record.consumed:
            raise ApprovalServiceError("approval has already been consumed", ErrorCode.APPROVAL_REQUIRED.value)
        self._validate_expiry(record.approval.expires_at)
        expected_hash = approval_payload_hash(
            approval_id=record.approval.approval_id,
            proposal_id=record.approval.proposal_id,
            proposal_hash=record.proposal_hash,
            actor=record.approval.actor,
            work_id=record.work_id,
            branch_id=record.branch_id,
            knowledge_version=record.knowledge_version,
            action=record.action,
            expires_at=record.approval.expires_at,
            idempotency_key=record.idempotency_key,
        )
        if expected_hash != record.approval.approval_hash:
            raise ApprovalServiceError("stored approval hash is not canonical", ErrorCode.INVALID_SCHEMA.value)
        return record

    def _resolve_proposal(self, request: ApprovalRequest) -> ProposalRecord | None:
        if self.proposal_service is None:
            return None
        try:
            return self.proposal_service.resolve(ProposalRef(
                proposal_id=request.proposal_id,
                proposal_hash=request.proposal_hash or "0" * 64,
            ) if request.proposal_hash else request.proposal_id)
        except ProposalServiceError as exc:
            raise ApprovalServiceError(str(exc), exc.code) from exc

    @staticmethod
    def _normalize_request(request: ApprovalRequest | Mapping[str, Any]) -> ApprovalRequest:
        if isinstance(request, ApprovalRequest):
            return request.model_copy(deep=True)
        try:
            return ApprovalRequest.model_validate(request)
        except PydanticValidationError as exc:
            raise ApprovalServiceError("approval request is invalid", ErrorCode.INVALID_SCHEMA.value) from exc

    @staticmethod
    def _normalize_action(value: str) -> str:
        normalized = value.strip().upper().replace("-", "_")
        if normalized in {"COMMIT", "CHAPTER_COMMIT"}:
            return "COMMIT"
        raise ApprovalServiceError("approval action is not supported", ErrorCode.APPROVAL_REQUIRED.value)

    def _validate_expiry(self, expires_at: int | str | None) -> None:
        if expires_at is None:
            return
        now = self.clock()
        if isinstance(expires_at, int):
            expires = float(expires_at)
        else:
            text = expires_at.strip()
            try:
                expires = float(text)
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ApprovalServiceError("approval expiry is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                expires = parsed.timestamp()
        if expires <= now:
            raise ApprovalServiceError("approval has expired", ErrorCode.APPROVAL_EXPIRED.value)


__all__ = [
    "ApprovalRecord",
    "ApprovalRepository",
    "ApprovalRequest",
    "ApprovalService",
    "ApprovalServiceError",
    "InMemoryApprovalRepository",
    "SCHEMA_HASH",
    "approval_payload_hash",
]
