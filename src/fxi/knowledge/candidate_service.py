"""Candidate submission boundary for the Fxi v3 lifecycle."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

from pydantic import ValidationError as PydanticValidationError

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .contracts import CandidateEnvelope, CandidateRef, ErrorCode, SourceSnapshotRef


class CandidateServiceError(FxiError):
    """A candidate failed the service boundary contract."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class CandidateRepository(Protocol):
    def get(self, candidate_id: str) -> CandidateEnvelope | None: ...

    def save(self, envelope: CandidateEnvelope, candidate_hash: str) -> None: ...


class InMemoryCandidateRepository:
    """Small repository suitable for clean-room use and dependency injection."""

    def __init__(self) -> None:
        self._records: dict[str, tuple[CandidateEnvelope, str]] = {}
        self._lock = RLock()

    def get(self, candidate_id: str) -> CandidateEnvelope | None:
        with self._lock:
            record = self._records.get(candidate_id)
            return None if record is None else record[0].model_copy(deep=True)

    def get_hash(self, candidate_id: str) -> str | None:
        with self._lock:
            record = self._records.get(candidate_id)
            return None if record is None else record[1]

    def save(self, envelope: CandidateEnvelope, candidate_hash: str) -> None:
        with self._lock:
            existing = self._records.get(envelope.candidate_id)
            if existing is not None and existing[1] != candidate_hash:
                raise CandidateServiceError(
                    "candidate_id is already bound to different content",
                    ErrorCode.IDEMPOTENCY_CONFLICT.value,
                )
            self._records[envelope.candidate_id] = (envelope.model_copy(deep=True), candidate_hash)


def candidate_hash(envelope: CandidateEnvelope) -> str:
    """Hash the complete canonical candidate envelope."""

    return sha256_hex(envelope.model_dump(mode="json"))


def candidate_input_hash(envelope: CandidateEnvelope | Mapping[str, Any]) -> str:
    """Hash the generic candidate input binding, excluding derived hashes/status.

    This helper is intentionally generic.  Domain packages may put any payload
    shape below ``payload``; no domain field names are interpreted here.
    """

    if isinstance(envelope, CandidateEnvelope):
        data = envelope.model_dump(mode="json")
    elif isinstance(envelope, Mapping):
        data = dict(envelope)
    else:
        raise TypeError("envelope must be CandidateEnvelope or mapping")
    input_data = {
        key: data.get(key)
        for key in (
            "artifact_kind",
            "work_id",
            "branch_id",
            "source_snapshot_ref",
            "evidence_refs",
            "extractor_id",
            "schema_version",
            "domain_package_version",
            "model_route",
            "prompt_hash",
            "policy_hash",
            "budget_ref",
            "payload",
        )
    }
    return sha256_hex(input_data)


class CandidateService:
    """Accept and retrieve candidates without mutating their lifecycle state."""

    def __init__(
        self,
        *,
        repository: CandidateRepository | None = None,
        snapshot_resolver: Callable[[str], SourceSnapshotRef | Mapping[str, Any] | None] | None = None,
        input_hash_validator: Callable[[CandidateEnvelope], bool] | None = None,
    ) -> None:
        self.repository = repository or InMemoryCandidateRepository()
        self.snapshot_resolver = snapshot_resolver
        self.input_hash_validator = input_hash_validator
        self._lock = RLock()

    def submit(self, envelope: CandidateEnvelope) -> CandidateRef:
        try:
            if not isinstance(envelope, CandidateEnvelope):
                envelope = CandidateEnvelope.model_validate(envelope)
        except PydanticValidationError as exc:
            raise CandidateServiceError("candidate envelope is invalid") from exc
        self._validate_submission(envelope)
        digest = candidate_hash(envelope)
        with self._lock:
            existing = self.repository.get(envelope.candidate_id)
            if existing is not None:
                existing_hash = self._stored_hash(existing)
                if existing_hash != digest:
                    raise CandidateServiceError(
                        "candidate replay contains different content",
                        ErrorCode.IDEMPOTENCY_CONFLICT.value,
                    )
                return self._ref(existing, existing_hash)
            self.repository.save(envelope.model_copy(deep=True), digest)
            stored = self.repository.get(envelope.candidate_id)
            if stored is None:
                raise CandidateServiceError("candidate repository did not retain submission")
            return self._ref(stored, digest)

    def get(self, candidate_id: str) -> CandidateEnvelope:
        envelope = self.repository.get(candidate_id)
        if envelope is None:
            raise CandidateServiceError(
                f"candidate not found: {candidate_id}", ErrorCode.NOT_FOUND.value
            )
        return envelope.model_copy(deep=True)

    def _validate_submission(self, envelope: CandidateEnvelope) -> None:
        if envelope.status != "CANDIDATE":
            raise CandidateServiceError(
                "submission cannot set lifecycle status",
                ErrorCode.INVALID_SCHEMA.value,
            )
        if envelope.actor is not None:
            if envelope.actor.scope is not None and (
                envelope.actor.scope.work_id != envelope.work_id
                or envelope.actor.scope.branch_id != envelope.branch_id
            ):
                raise CandidateServiceError(
                    "candidate actor scope does not match candidate",
                    ErrorCode.INVALID_SCOPE.value,
                )
        if not envelope.evidence_refs:
            raise CandidateServiceError("candidate requires evidence", ErrorCode.NO_EVIDENCE.value)
        seen: set[str] = set()
        for evidence in envelope.evidence_refs:
            if evidence.scope is None:
                raise CandidateServiceError(
                    "evidence scope must bind work and branch",
                    ErrorCode.INVALID_EVIDENCE.value,
                )
            if evidence.scope.work_id != envelope.work_id or evidence.scope.branch_id != envelope.branch_id:
                raise CandidateServiceError(
                    "evidence scope does not match candidate",
                    ErrorCode.INVALID_SCOPE.value,
                )
            if evidence.source_snapshot_ref is not None and evidence.source_snapshot_ref != envelope.source_snapshot_ref:
                raise CandidateServiceError(
                    "evidence source snapshot does not match candidate",
                    ErrorCode.INVALID_EVIDENCE.value,
                )
            evidence_key = sha256_hex(evidence.model_dump(mode="json"))
            if evidence_key in seen:
                raise CandidateServiceError(
                    "candidate evidence references must be unique",
                    ErrorCode.INVALID_EVIDENCE.value,
                )
            seen.add(evidence_key)
        if self.snapshot_resolver is not None:
            snapshot = self.snapshot_resolver(envelope.source_snapshot_ref)
            if snapshot is None:
                raise CandidateServiceError(
                    "source snapshot not found", ErrorCode.NOT_FOUND.value
                )
            if not isinstance(snapshot, SourceSnapshotRef):
                try:
                    snapshot = SourceSnapshotRef.model_validate(snapshot)
                except PydanticValidationError as exc:
                    raise CandidateServiceError(
                        "source snapshot is invalid", ErrorCode.INVALID_EVIDENCE.value
                    ) from exc
            first = envelope.evidence_refs[0]
            if snapshot.work_id != envelope.work_id or snapshot.source_id != first.source_id or snapshot.source_version != first.source_version:
                raise CandidateServiceError(
                    "source snapshot binding does not match candidate evidence",
                    ErrorCode.INVALID_SCOPE.value,
                )
            if any(
                evidence.source_id != snapshot.source_id or evidence.source_version != snapshot.source_version
                for evidence in envelope.evidence_refs
            ):
                raise CandidateServiceError(
                    "candidate evidence source binding is inconsistent",
                    ErrorCode.INVALID_SCOPE.value,
                )
        if self.input_hash_validator is not None and not self.input_hash_validator(envelope):
            raise CandidateServiceError(
                "candidate input_hash failed canonical validation",
                ErrorCode.INVALID_SCHEMA.value,
            )

    def _stored_hash(self, envelope: CandidateEnvelope) -> str:
        getter = getattr(self.repository, "get_hash", None)
        if callable(getter):
            value = getter(envelope.candidate_id)
            if isinstance(value, str):
                return value
        return candidate_hash(envelope)

    @staticmethod
    def _ref(envelope: CandidateEnvelope, digest: str) -> CandidateRef:
        return CandidateRef(
            candidate_id=envelope.candidate_id,
            candidate_hash=digest,
            work_id=envelope.work_id,
            branch_id=envelope.branch_id,
            status=envelope.status,
        )


__all__ = [
    "CandidateRepository",
    "CandidateService",
    "CandidateServiceError",
    "InMemoryCandidateRepository",
    "candidate_hash",
    "candidate_input_hash",
]
