"""Durable, domain-neutral repositories for the Fxi v3 authority.

The v3 services deliberately depend on small repository protocols so that
clean-room tests can use in-memory stores.  The application composition uses
the adapters in this module instead.  Every adapter shares one
``SQLiteKnowledgeStore``; nested transactions therefore use the same SQLite
transaction and commit/replay state is durable across process restarts.

The ``knowledge_runtime_records`` envelope is an internal persistence detail:
it preserves complete public-contract models and service-local binding
metadata.  The typed v3 tables are updated in the same transaction as a
relational/indexed representation of that authority state.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
from threading import RLock, local
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Sequence, TypeVar

from pydantic import BaseModel

from fxi.core.canonical import canonical_json, sha256_hex
from fxi.core.exceptions import FxiError

from fxi.knowledge.approval_service import ApprovalRecord, _record_fingerprint
from fxi.knowledge.commit_service import CommitRecord, CommitServiceError
from fxi.knowledge.branches import Branch
from fxi.knowledge.candidate_service import CandidateRepository
from fxi.knowledge.contracts import (
    Actor,
    ApprovalRef,
    CandidateEnvelope,
    CommitReceipt,
    ContextView,
    EvaluationPolicy,
    EvidenceRef,
    ReviewReport,
    Scope,
    StateChangeSet,
    SourceBindingRef,
    SourceSnapshotRef,
)
from fxi.knowledge.decisions import Decision, PromotionReceipt
from fxi.knowledge.evaluations import EvaluationManifest
from fxi.knowledge.objects import Commit, KnowledgeHead, KnowledgeObject, KnowledgeVersion
from fxi.knowledge.proposal_service import Proposal, ProposalRecord
from fxi.knowledge.promotion_service import PromotionServiceError
from fxi.knowledge.review_service import ReviewRecord, ReviewRequest, _minimal_context_view
from fxi.knowledge.source_graph import SourceCompositeRef
from fxi.sources.snapshot_service import SnapshotService
from fxi.storage.sqlite_client import DatabaseClient


class SQLiteAuthorityError(FxiError):
    """A durable authority operation failed closed."""

    def __init__(self, message: str, code: str = "INVALID_SCHEMA") -> None:
        super().__init__(message, code=code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    return value


def _encoded(value: Any) -> str:
    try:
        return canonical_json(_jsonable(value))
    except Exception as exc:
        raise SQLiteAuthorityError("authority record is not JSON serializable") from exc


def _clone(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_copy(deep=True)
    return deepcopy(value)


T = TypeVar("T")
K = TypeVar("K")


class SQLiteKnowledgeStore:
    """Shared connection/transaction boundary for all v3 repositories."""

    def __init__(self, database: DatabaseClient) -> None:
        self.database = database
        self._lock = RLock()
        self._local = local()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        active = getattr(self._local, "cursor", None)
        if active is not None:
            yield active
            return
        with self._lock:
            with self.database.transaction() as cursor:
                self._local.cursor = cursor
                try:
                    yield cursor
                finally:
                    self._local.cursor = None

    def _row(self, query: str, params: Sequence[Any] = ()) -> Mapping[str, Any] | None:
        active = getattr(self._local, "cursor", None)
        if active is not None:
            row = active.execute(query, tuple(params)).fetchone()
            return None if row is None else dict(row)
        with self.database.get_connection() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
            return None if row is None else dict(row)

    def _rows(self, query: str, params: Sequence[Any] = ()) -> tuple[Mapping[str, Any], ...]:
        active = getattr(self._local, "cursor", None)
        if active is not None:
            return tuple(dict(row) for row in active.execute(query, tuple(params)).fetchall())
        with self.database.get_connection() as connection:
            return tuple(dict(row) for row in connection.execute(query, tuple(params)).fetchall())

    def runtime_row(self, record_type: str, record_id: str) -> Mapping[str, Any] | None:
        return self._row(
            "SELECT record_type, record_id, work_id, branch_id, idempotency_key, "
            "content_hash, payload_json, status, created_at, updated_at "
            "FROM knowledge_runtime_records WHERE record_type = ? AND record_id = ?",
            (record_type, record_id),
        )

    def runtime_rows(self, record_type: str) -> tuple[Mapping[str, Any], ...]:
        return self._rows(
            "SELECT record_type, record_id, work_id, branch_id, idempotency_key, "
            "content_hash, payload_json, status, created_at, updated_at "
            "FROM knowledge_runtime_records WHERE record_type = ? ORDER BY record_id",
            (record_type,),
        )

    def runtime_row_by_idempotency(
        self,
        record_type: str,
        work_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None:
        """Read one scoped replay record without widening the authority key."""

        return self._row(
            "SELECT record_type, record_id, work_id, branch_id, idempotency_key, "
            "content_hash, payload_json, status, created_at, updated_at "
            "FROM knowledge_runtime_records "
            "WHERE record_type = ? AND work_id = ? AND idempotency_key = ?",
            (record_type, work_id, idempotency_key),
        )

    def decode(self, row: Mapping[str, Any], model_type: type[T]) -> T:
        try:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, Mapping):
                raise ValueError("payload is not an object")
            return model_type.model_validate(payload)  # type: ignore[attr-defined]
        except Exception as exc:
            raise SQLiteAuthorityError(
                f"durable {row.get('record_type', 'authority')} record is invalid",
                "INVALID_SCHEMA",
            ) from exc

    def decode_value(self, row: Mapping[str, Any]) -> Any:
        try:
            return json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SQLiteAuthorityError("durable authority payload is invalid") from exc

    def put(
        self,
        cursor: Any,
        *,
        record_type: str,
        record_id: str,
        payload: Any,
        work_id: str | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        status: str = "ACTIVE",
        content_hash: str | None = None,
        immutable: bool = True,
    ) -> str:
        encoded = _encoded(payload)
        digest = content_hash or sha256_hex(json.loads(encoded))
        existing = cursor.execute(
            "SELECT content_hash, created_at FROM knowledge_runtime_records "
            "WHERE record_type = ? AND record_id = ?",
            (record_type, record_id),
        ).fetchone()
        if existing is not None and immutable and str(existing[0]) != digest:
            raise SQLiteAuthorityError(
                "authority record is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )
        created_at = _now() if existing is None else str(existing[1])
        try:
            cursor.execute(
                """INSERT INTO knowledge_runtime_records
                   (record_type, record_id, work_id, branch_id, idempotency_key,
                    content_hash, payload_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(record_type, record_id) DO UPDATE SET
                     work_id = excluded.work_id,
                     branch_id = excluded.branch_id,
                     idempotency_key = excluded.idempotency_key,
                     content_hash = excluded.content_hash,
                     payload_json = excluded.payload_json,
                     status = excluded.status,
                     updated_at = excluded.updated_at""",
                (
                    record_type,
                    record_id,
                    work_id,
                    branch_id,
                    idempotency_key,
                    digest,
                    encoded,
                    status,
                    created_at,
                    _now(),
                ),
            )
        except Exception as exc:
            if type(exc).__module__ == "sqlite3" and "UNIQUE" in str(exc).upper():
                raise SQLiteAuthorityError(
                    "authority idempotency key is already bound to another record",
                    "IDEMPOTENCY_CONFLICT",
                ) from exc
            raise
        return digest

    def model(
        self,
        record_type: str,
        record_id: str,
        model_type: type[T],
    ) -> T | None:
        row = self.runtime_row(record_type, record_id)
        return None if row is None else self.decode(row, model_type)

    def models(self, record_type: str, model_type: type[T]) -> tuple[T, ...]:
        return tuple(self.decode(row, model_type) for row in self.runtime_rows(record_type))


ModelKey = Callable[[Any], K]
RecordId = Callable[[K], str]
Mirror = Callable[[Any, K, Any], None]


class SQLiteModelMap(MutableMapping[K, T]):
    """A dict-like model collection backed by runtime records."""

    def __init__(
        self,
        store: SQLiteKnowledgeStore,
        record_type: str,
        model_type: type[T],
        *,
        key_from_model: ModelKey,
        record_id: RecordId,
        mirror: Mirror | None = None,
        immutable: bool = True,
    ) -> None:
        self.store = store
        self.record_type = record_type
        self.model_type = model_type
        self.key_from_model = key_from_model
        self.record_id = record_id
        self.mirror = mirror
        self.immutable = immutable
        self._cache: dict[K, T] = {}
        self._refresh()

    def _refresh(self) -> None:
        for value in self.store.models(self.record_type, self.model_type):
            self._cache[self.key_from_model(value)] = value

    def __getitem__(self, key: K) -> T:
        self._refresh()
        try:
            return _clone(self._cache[key])
        except KeyError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: K, value: T) -> None:
        if self.key_from_model(value) != key:
            raise SQLiteAuthorityError("repository key does not match model identity")
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type=self.record_type,
                record_id=self.record_id(key),
                payload=value,
                work_id=getattr(value, "work_id", None),
                branch_id=getattr(value, "branch_id", None),
                status=str(getattr(value, "status", "ACTIVE")),
                immutable=self.immutable,
            )
            if self.mirror is not None:
                self.mirror(cursor, key, value)
        self._cache[key] = _clone(value)

    def __delitem__(self, key: K) -> None:
        with self.store.transaction() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_runtime_records WHERE record_type = ? AND record_id = ?",
                (self.record_type, self.record_id(key)),
            )
        self._cache.pop(key, None)

    def __iter__(self) -> Iterator[K]:
        self._refresh()
        return iter(tuple(self._cache))

    def __len__(self) -> int:
        self._refresh()
        return len(self._cache)

    def values(self):  # type: ignore[override]
        self._refresh()
        return tuple(_clone(value) for value in self._cache.values())

    def items(self):  # type: ignore[override]
        self._refresh()
        return tuple((key, _clone(value)) for key, value in self._cache.items())


class SQLiteJsonMap(MutableMapping[K, Mapping[str, Any]]):
    """A dict-like mapping collection for projection/chapter metadata."""

    def __init__(self, store: SQLiteKnowledgeStore, record_type: str, record_id: RecordId) -> None:
        self.store = store
        self.record_type = record_type
        self.record_id = record_id
        self._cache: dict[K, Mapping[str, Any]] = {}
        self._refresh()

    def _refresh(self) -> None:
        for row in self.store.runtime_rows(self.record_type):
            value = self.store.decode_value(row)
            if isinstance(value, Mapping):
                self._cache[row["record_id"]] = dict(value)  # type: ignore[index]

    def __getitem__(self, key: K) -> Mapping[str, Any]:
        self._refresh()
        return deepcopy(self._cache[key])

    def __setitem__(self, key: K, value: Mapping[str, Any]) -> None:
        if not isinstance(value, Mapping):
            raise SQLiteAuthorityError("repository metadata must be an object")
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type=self.record_type,
                record_id=self.record_id(key),
                payload=dict(value),
                work_id=str(value.get("work_id")) if value.get("work_id") is not None else None,
                branch_id=str(value.get("branch_id")) if value.get("branch_id") is not None else None,
                status=str(value.get("status", "ACTIVE")),
                immutable=False,
            )
        self._cache[key] = dict(value)

    def __delitem__(self, key: K) -> None:
        with self.store.transaction() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_runtime_records WHERE record_type = ? AND record_id = ?",
                (self.record_type, self.record_id(key)),
            )
        del self._cache[key]

    def __iter__(self) -> Iterator[K]:
        self._refresh()
        return iter(tuple(self._cache))

    def __len__(self) -> int:
        self._refresh()
        return len(self._cache)

    def values(self):  # type: ignore[override]
        self._refresh()
        return tuple(deepcopy(value) for value in self._cache.values())


class SQLiteValueMap(MutableMapping[str, str]):
    """A small durable map for active snapshot aliases."""

    def __init__(self, store: SQLiteKnowledgeStore, record_type: str) -> None:
        self.store = store
        self.record_type = record_type
        self._cache: dict[str, str] = {}
        self._refresh()

    def _refresh(self) -> None:
        for row in self.store.runtime_rows(self.record_type):
            value = self.store.decode_value(row)
            if isinstance(value, str):
                self._cache[str(row["record_id"])] = value

    def __getitem__(self, key: str) -> str:
        self._refresh()
        return self._cache[key]

    def __setitem__(self, key: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise SQLiteAuthorityError("repository value must be a non-empty string")
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type=self.record_type,
                record_id=key,
                payload=value,
                status="ACTIVE",
                immutable=False,
            )
        self._cache[key] = value

    def __delitem__(self, key: str) -> None:
        with self.store.transaction() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_runtime_records WHERE record_type = ? AND record_id = ?",
                (self.record_type, key),
            )
        del self._cache[key]

    def __iter__(self) -> Iterator[str]:
        self._refresh()
        return iter(tuple(self._cache))

    def __len__(self) -> int:
        self._refresh()
        return len(self._cache)


class SQLiteSnapshotActiveMap(MutableMapping[tuple[str, str, str], str]):
    """Durable tuple-key map used by ``SnapshotService.active``.

    SnapshotService owns the tuple shape ``(work_id, source_id, branch_id)``.
    Keeping the reversible key encoding here avoids teaching the generic
    runtime envelope anything about source or domain fields.
    """

    _separator = "\x1f"

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store
        self._cache: dict[tuple[str, str, str], str] = {}
        self._refresh()

    @classmethod
    def _record_id(cls, key: tuple[str, str, str]) -> str:
        if not isinstance(key, tuple) or len(key) != 3 or not all(
            isinstance(item, str) and item for item in key
        ):
            raise SQLiteAuthorityError("snapshot active key is invalid")
        return cls._separator.join(key)

    @classmethod
    def _key(cls, record_id: Any) -> tuple[str, str, str]:
        if not isinstance(record_id, str):
            raise SQLiteAuthorityError("snapshot active record id is invalid")
        parts = tuple(record_id.split(cls._separator))
        if len(parts) != 3 or not all(parts):
            raise SQLiteAuthorityError("snapshot active record id is invalid")
        return parts  # type: ignore[return-value]

    def _refresh(self) -> None:
        for row in self.store.runtime_rows("snapshot_active"):
            key = self._key(row["record_id"])
            value = self.store.decode_value(row)
            if not isinstance(value, str) or not value:
                raise SQLiteAuthorityError("snapshot active value is invalid")
            self._cache[key] = value

    def __getitem__(self, key: tuple[str, str, str]) -> str:
        self._refresh()
        return self._cache[key]

    def __setitem__(self, key: tuple[str, str, str], value: str) -> None:
        record_id = self._record_id(key)
        if not isinstance(value, str) or not value:
            raise SQLiteAuthorityError("snapshot active value must be non-empty")
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="snapshot_active",
                record_id=record_id,
                payload=value,
                work_id=key[0],
                branch_id=key[2],
                status="ACTIVE",
                immutable=False,
            )
        self._cache[key] = value

    def __delitem__(self, key: tuple[str, str, str]) -> None:
        record_id = self._record_id(key)
        with self.store.transaction() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_runtime_records WHERE record_type = ? AND record_id = ?",
                ("snapshot_active", record_id),
            )
        del self._cache[key]

    def __iter__(self) -> Iterator[tuple[str, str, str]]:
        self._refresh()
        return iter(tuple(self._cache))

    def __len__(self) -> int:
        self._refresh()
        return len(self._cache)


def _scope_parts(value: Any) -> tuple[str | None, str | None]:
    work_id = getattr(value, "work_id", None)
    branch_id = getattr(value, "branch_id", None)
    return (
        work_id if isinstance(work_id, str) else None,
        branch_id if isinstance(branch_id, str) else None,
    )


def _mirror_binding(cursor: Any, _key: str, value: SourceBindingRef) -> None:
    digest = sha256_hex(value.model_dump(mode="json"))
    now = _now()
    cursor.execute(
        """INSERT INTO knowledge_source_bindings
           (binding_id, work_id, source_id, role, priority, branch_id,
            validity_json, license, access, allowed_purposes_json,
            sync_direction, binding_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(binding_id) DO UPDATE SET
             work_id=excluded.work_id, source_id=excluded.source_id,
             role=excluded.role, priority=excluded.priority,
             branch_id=excluded.branch_id, validity_json=excluded.validity_json,
             license=excluded.license, access=excluded.access,
             allowed_purposes_json=excluded.allowed_purposes_json,
             sync_direction=excluded.sync_direction, binding_hash=excluded.binding_hash,
             updated_at=excluded.updated_at""",
        (
            value.binding_id,
            value.work_id,
            value.source_id,
            value.role,
            value.priority,
            value.branch_id,
            _encoded(value.validity),
            value.license,
            value.access,
            _encoded(value.allowed_purposes),
            value.sync_direction,
            digest,
            now,
            now,
        ),
    )


def _mirror_snapshot(cursor: Any, _key: str, value: SourceSnapshotRef) -> None:
    cursor.execute(
        """INSERT INTO knowledge_source_snapshots
           (snapshot_id, work_id, source_id, source_version, content_hash,
            binding_id, status, document_refs_json, snapshot_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(snapshot_id) DO UPDATE SET
             work_id=excluded.work_id, source_id=excluded.source_id,
             source_version=excluded.source_version, content_hash=excluded.content_hash,
             binding_id=excluded.binding_id, status=excluded.status,
             document_refs_json=excluded.document_refs_json,
             snapshot_hash=excluded.snapshot_hash""",
        (
            value.snapshot_id,
            value.work_id,
            value.source_id,
            value.source_version,
            value.content_hash,
            value.binding_id,
            value.status,
            _encoded(value.document_refs),
            sha256_hex(value.model_dump(mode="json")),
            _now(),
        ),
    )


def _mirror_branch(cursor: Any, _key: str, value: Branch) -> None:
    cursor.execute(
        """INSERT INTO knowledge_branches
           (branch_id, work_id, parent_branch, divergence_anchor_json,
            policy_json, status, actor_id, branch_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(branch_id) DO UPDATE SET
             work_id=excluded.work_id, parent_branch=excluded.parent_branch,
             divergence_anchor_json=excluded.divergence_anchor_json,
             policy_json=excluded.policy_json, status=excluded.status,
             actor_id=excluded.actor_id, branch_hash=excluded.branch_hash""",
        (
            value.branch_id,
            value.work_id,
            value.parent_branch,
            None if value.divergence is None else _encoded(value.divergence),
            _encoded(value.policy),
            value.status,
            value.created_by,
            value.branch_hash,
            _now(),
        ),
    )


def _mirror_version(cursor: Any, _key: str, value: KnowledgeVersion) -> None:
    cursor.execute(
        """INSERT INTO knowledge_versions
           (knowledge_version, work_id, branch_id, parent_version,
            object_refs_json, claim_refs_json, relation_refs_json,
            source_snapshot_refs_json, status, actor_id, version_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(knowledge_version) DO UPDATE SET
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             parent_version=excluded.parent_version, object_refs_json=excluded.object_refs_json,
             claim_refs_json=excluded.claim_refs_json, relation_refs_json=excluded.relation_refs_json,
             source_snapshot_refs_json=excluded.source_snapshot_refs_json,
             status=excluded.status, actor_id=excluded.actor_id,
             version_hash=excluded.version_hash""",
        (
            value.knowledge_version,
            value.work_id,
            value.branch_id,
            value.parent_version,
            _encoded(value.object_refs),
            _encoded(value.claim_refs),
            _encoded(value.relation_refs),
            _encoded(value.source_snapshot_refs),
            value.status,
            value.actor,
            value.version_hash,
            _now(),
        ),
    )


def _mirror_head(cursor: Any, _key: tuple[str, str], value: KnowledgeHead) -> None:
    cursor.execute(
        """INSERT INTO knowledge_heads
           (work_id, branch_id, knowledge_version, version_hash, cas_revision, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(work_id, branch_id) DO UPDATE SET
             knowledge_version=excluded.knowledge_version,
             version_hash=excluded.version_hash,
             cas_revision=excluded.cas_revision,
             updated_at=excluded.updated_at""",
        (
            value.work_id,
            value.branch_id,
            value.knowledge_version,
            value.version_hash,
            value.cas_revision,
            _now(),
        ),
    )


class SQLiteSourceBindingMap(SQLiteModelMap[str, SourceBindingRef]):
    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        super().__init__(
            store,
            "source_binding",
            SourceBindingRef,
            key_from_model=lambda value: value.binding_id,
            record_id=lambda key: key,
            mirror=_mirror_binding,
            immutable=True,
        )


class SQLiteSnapshotService(SnapshotService):
    """Snapshot service whose published refs survive application restarts."""

    def __init__(
        self,
        root: Any,
        *,
        store: SQLiteKnowledgeStore,
        binding_map: SQLiteSourceBindingMap | None = None,
    ) -> None:
        super().__init__(root)
        self._refs = SQLiteModelMap(
            store,
            "snapshot",
            SourceSnapshotRef,
            key_from_model=lambda value: value.snapshot_id,
            record_id=lambda key: key,
            mirror=_mirror_snapshot,
            immutable=True,
        )
        self._bindings = binding_map or SQLiteSourceBindingMap(store)
        self._active = SQLiteSnapshotActiveMap(store)


class SQLiteBranchState:
    """Persistent maps accepted by the existing generic BranchService."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.branches = SQLiteModelMap(
            store,
            "branch",
            Branch,
            key_from_model=lambda value: value.branch_id,
            record_id=lambda key: key,
            mirror=_mirror_branch,
            immutable=True,
        )
        self.versions = SQLiteModelMap(
            store,
            "knowledge_version",
            KnowledgeVersion,
            key_from_model=lambda value: value.knowledge_version,
            record_id=lambda key: key,
            mirror=_mirror_version,
            immutable=True,
        )
        self.heads = SQLiteModelMap(
            store,
            "head",
            KnowledgeHead,
            key_from_model=lambda value: (value.work_id, value.branch_id),
            record_id=lambda key: f"{key[0]}:{key[1]}",
            mirror=_mirror_head,
            immutable=False,
        )


def _mirror_context_view(cursor: Any, _key: str, value: ContextView) -> None:
    snapshot_refs = tuple(
        dict.fromkeys(
            ref.source_snapshot_ref
            for ref in value.evidence_refs
            if ref.source_snapshot_ref is not None
        )
    )
    cursor.execute(
        """INSERT INTO knowledge_context_views
           (view_id, work_id, branch_id, as_of, purpose, scope_json,
            blocks_json, evidence_refs_json, forbidden_refs_json, conflicts_json,
            staleness, completeness, budget_json, view_hash, content_hash,
            knowledge_version, source_snapshot_refs_json, manifest_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(view_id) DO UPDATE SET
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             as_of=excluded.as_of, purpose=excluded.purpose, scope_json=excluded.scope_json,
             blocks_json=excluded.blocks_json, evidence_refs_json=excluded.evidence_refs_json,
             forbidden_refs_json=excluded.forbidden_refs_json, conflicts_json=excluded.conflicts_json,
             staleness=excluded.staleness, completeness=excluded.completeness,
             budget_json=excluded.budget_json, view_hash=excluded.view_hash,
             content_hash=excluded.content_hash, source_snapshot_refs_json=excluded.source_snapshot_refs_json,
             manifest_json=excluded.manifest_json""",
        (
            value.view_id,
            value.work_id,
            value.branch_id,
            str(value.as_of),
            value.purpose,
            _encoded(value.scope),
            _encoded(value.blocks),
            _encoded(value.evidence_refs),
            _encoded(value.forbidden_refs),
            _encoded(value.conflicts),
            value.staleness,
            value.completeness,
            _encoded(value.budget),
            value.view_hash,
            value.content_hash,
            None,
            _encoded(snapshot_refs),
            None if value.manifest is None else _encoded(value.manifest),
            _now(),
        ),
    )


class SQLiteContextCache(dict[str, ContextView]):
    """Durable cache for validated ContextViews, never for arbitrary JSON."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        super().__init__()
        self.store = store
        for value in store.models("context_view", ContextView):
            self._remember_aliases(value)

    def _remember_aliases(self, value: ContextView) -> None:
        dict.__setitem__(self, value.view_id, value.model_copy(deep=True))
        dict.__setitem__(self, value.view_hash, value.model_copy(deep=True))
        dict.__setitem__(self, f"{value.work_id}:{value.branch_id}", value.model_copy(deep=True))

    def __setitem__(self, key: str, value: ContextView) -> None:
        if not isinstance(value, ContextView):
            raise SQLiteAuthorityError("context cache accepts only validated ContextView")
        if key == value.view_id:
            with self.store.transaction() as cursor:
                self.store.put(
                    cursor,
                    record_type="context_view",
                    record_id=value.view_id,
                    payload=value,
                    work_id=value.work_id,
                    branch_id=value.branch_id,
                    status=value.completeness,
                    content_hash=value.view_hash,
                    immutable=True,
                )
                _mirror_context_view(cursor, value.view_id, value)
        dict.__setitem__(self, key, value.model_copy(deep=True))


def _mirror_candidate(cursor: Any, _key: str, value: CandidateEnvelope) -> None:
    work_id, branch_id = _scope_parts(value)
    evidence_json = _encoded(value.evidence_refs)
    actor_id = value.actor.actor_id if value.actor is not None else None
    cursor.execute(
        """INSERT INTO knowledge_candidates
           (candidate_id, artifact_kind, status, work_id, branch_id,
            source_snapshot_ref, evidence_refs_json, input_hash, extractor_id,
            schema_version, domain_package_version, model_route, prompt_hash,
            policy_hash, budget_ref, payload_json, payload_hash, actor_id,
            idempotency_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(candidate_id) DO UPDATE SET
             artifact_kind=excluded.artifact_kind, status=excluded.status,
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             source_snapshot_ref=excluded.source_snapshot_ref,
             evidence_refs_json=excluded.evidence_refs_json,
             input_hash=excluded.input_hash, extractor_id=excluded.extractor_id,
             schema_version=excluded.schema_version,
             domain_package_version=excluded.domain_package_version,
             model_route=excluded.model_route, prompt_hash=excluded.prompt_hash,
             policy_hash=excluded.policy_hash, budget_ref=excluded.budget_ref,
             payload_json=excluded.payload_json, payload_hash=excluded.payload_hash,
             actor_id=excluded.actor_id, idempotency_key=excluded.idempotency_key""",
        (
            value.candidate_id,
            value.artifact_kind,
            value.status,
            work_id,
            branch_id,
            value.source_snapshot_ref,
            evidence_json,
            value.input_hash,
            value.extractor_id,
            value.schema_version,
            value.domain_package_version,
            value.model_route,
            value.prompt_hash,
            value.policy_hash,
            value.budget_ref,
            _encoded(value.payload),
            value.payload_hash,
            actor_id,
            value.candidate_id,
            _now(),
        ),
    )


class SQLiteCandidateRepository:
    """Durable implementation of the v3 candidate repository protocol."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def get(self, candidate_id: str) -> CandidateEnvelope | None:
        return self.store.model("candidate", candidate_id, CandidateEnvelope)

    def get_hash(self, candidate_id: str) -> str | None:
        row = self.store.runtime_row("candidate", candidate_id)
        return None if row is None else str(row["content_hash"])

    def save(self, envelope: CandidateEnvelope, candidate_hash: str) -> None:
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="candidate",
                record_id=envelope.candidate_id,
                payload=envelope,
                work_id=envelope.work_id,
                branch_id=envelope.branch_id,
                status=envelope.status,
                content_hash=candidate_hash,
                immutable=True,
            )
            _mirror_candidate(cursor, envelope.candidate_id, envelope)


def _candidate_scope(store: SQLiteKnowledgeStore, candidate_id: str) -> tuple[str, str]:
    candidate = store.model("candidate", candidate_id, CandidateEnvelope)
    if candidate is None:
        raise SQLiteAuthorityError("evaluation candidate is missing", "NOT_FOUND")
    return candidate.work_id, candidate.branch_id


def _mirror_evaluation(cursor: Any, _key: str, value: EvaluationManifest, policy: EvaluationPolicy, work_id: str, branch_id: str) -> None:
    cursor.execute(
        """INSERT INTO knowledge_evaluations
           (evaluation_id, candidate_id, work_id, branch_id, evaluator_id,
            evaluator_version, policy_id, policy_hash, policy_json, input_hash,
            evidence_coverage_json, duplicate_cluster_ref, same_core, variant_of,
            surface_similarity_risk, suitability, required_adaptation_json,
            uncertainty_json, conflicts_json, semantic_reviewer, result_hash,
            status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(evaluation_id) DO UPDATE SET
             candidate_id=excluded.candidate_id, work_id=excluded.work_id,
             branch_id=excluded.branch_id, evaluator_id=excluded.evaluator_id,
             evaluator_version=excluded.evaluator_version, policy_id=excluded.policy_id,
             policy_hash=excluded.policy_hash, policy_json=excluded.policy_json,
             input_hash=excluded.input_hash, evidence_coverage_json=excluded.evidence_coverage_json,
             duplicate_cluster_ref=excluded.duplicate_cluster_ref, same_core=excluded.same_core,
             variant_of=excluded.variant_of, surface_similarity_risk=excluded.surface_similarity_risk,
             suitability=excluded.suitability, required_adaptation_json=excluded.required_adaptation_json,
             uncertainty_json=excluded.uncertainty_json, conflicts_json=excluded.conflicts_json,
             semantic_reviewer=excluded.semantic_reviewer, result_hash=excluded.result_hash,
             status=excluded.status""",
        (
            value.evaluation_id,
            value.candidate_id,
            work_id,
            branch_id,
            value.evaluator_id,
            value.evaluator_version,
            policy.policy_id,
            policy.policy_hash,
            _encoded(policy),
            value.input_hash,
            _encoded(value.evidence_coverage),
            value.duplicate_cluster_ref,
            int(value.same_core),
            value.variant_of,
            value.surface_similarity_risk,
            value.suitability.value,
            _encoded(value.required_adaptation),
            _encoded(value.uncertainty),
            _encoded(value.conflicts),
            value.semantic_reviewer,
            value.result_hash,
            value.status,
            _now(),
        ),
    )


class SQLiteEvaluationRepository:
    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def get(self, evaluation_id: str) -> EvaluationManifest | None:
        return self.store.model("evaluation", evaluation_id, EvaluationManifest)

    def get_policy(self, evaluation_id: str) -> EvaluationPolicy | None:
        return self.store.model("evaluation_policy", evaluation_id, EvaluationPolicy)

    def save(self, manifest: EvaluationManifest, policy: EvaluationPolicy) -> None:
        work_id, branch_id = _candidate_scope(self.store, manifest.candidate_id)
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="evaluation",
                record_id=manifest.evaluation_id,
                payload=manifest,
                work_id=work_id,
                branch_id=branch_id,
                status=manifest.status,
                content_hash=manifest.result_hash,
                immutable=True,
            )
            self.store.put(
                cursor,
                record_type="evaluation_policy",
                record_id=manifest.evaluation_id,
                payload=policy,
                work_id=work_id,
                branch_id=branch_id,
                status="ACTIVE",
                content_hash=policy.policy_hash,
                immutable=True,
            )
            _mirror_evaluation(cursor, manifest.evaluation_id, manifest, policy, work_id, branch_id)

    def list(self) -> Sequence[EvaluationManifest]:
        return self.store.models("evaluation", EvaluationManifest)


def _mirror_decision(cursor: Any, _key: str, value: Decision) -> None:
    scope = value.actor.scope
    if scope is None:
        raise SQLiteAuthorityError("decision actor scope is required", "INVALID_SCOPE")
    cursor.execute(
        """INSERT INTO knowledge_decisions
           (decision_id, candidate_id, work_id, branch_id, evaluation_ref,
            action, actor_id, actor_json, reason, scope_json, decision_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(decision_id) DO UPDATE SET
             candidate_id=excluded.candidate_id, work_id=excluded.work_id,
             branch_id=excluded.branch_id, evaluation_ref=excluded.evaluation_ref,
             action=excluded.action, actor_id=excluded.actor_id,
             actor_json=excluded.actor_json, reason=excluded.reason,
             scope_json=excluded.scope_json, decision_hash=excluded.decision_hash""",
        (
            value.decision_id,
            value.candidate_id,
            scope.work_id,
            scope.branch_id,
            value.evaluation_ref,
            value.action.value,
            value.actor.actor_id,
            _encoded(value.actor),
            value.reason,
            _encoded(scope),
            value.decision_hash,
            _now(),
        ),
    )


def _mirror_object(cursor: Any, _key: str, value: KnowledgeObject) -> None:
    cursor.execute(
        """INSERT INTO knowledge_objects
           (object_id, work_id, branch_id, type_uri, schema_uri, schema_version,
            payload_json, payload_hash, origin, scope_json, validity_json,
            evidence_refs_json, evaluation_ref, knowledge_version, status,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(object_id) DO UPDATE SET
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             type_uri=excluded.type_uri, schema_uri=excluded.schema_uri,
             schema_version=excluded.schema_version, payload_json=excluded.payload_json,
             payload_hash=excluded.payload_hash, origin=excluded.origin,
             scope_json=excluded.scope_json, validity_json=excluded.validity_json,
             evidence_refs_json=excluded.evidence_refs_json,
             evaluation_ref=excluded.evaluation_ref,
             knowledge_version=excluded.knowledge_version, status=excluded.status,
             updated_at=excluded.updated_at""",
        (
            value.object_id,
            value.work_id,
            value.branch_id,
            value.type_uri,
            value.schema_uri,
            value.schema_version,
            _encoded(value.payload),
            value.payload_hash,
            value.origin,
            _encoded(value.scope),
            _encoded(value.validity),
            _encoded(value.evidence_refs),
            value.evaluation_ref,
            value.knowledge_version,
            value.status,
            _now(),
            _now(),
        ),
    )


def _mirror_approval(cursor: Any, _key: str, value: ApprovalRecord) -> None:
    approval = value.approval
    scope = approval.actor.scope
    if scope is None:
        raise SQLiteAuthorityError("approval actor scope is required", "INVALID_SCOPE")
    cursor.execute(
        """INSERT INTO knowledge_approvals
           (approval_id, proposal_id, work_id, branch_id, actor_id, role,
            scope_json, proposal_hash, approval_hash, expected_version,
            knowledge_version, action, idempotency_key, expires_at, consumed,
            consumed_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(approval_id) DO UPDATE SET
             proposal_id=excluded.proposal_id, work_id=excluded.work_id,
             branch_id=excluded.branch_id, actor_id=excluded.actor_id,
             role=excluded.role, scope_json=excluded.scope_json,
             proposal_hash=excluded.proposal_hash, approval_hash=excluded.approval_hash,
             expected_version=excluded.expected_version,
             knowledge_version=excluded.knowledge_version, action=excluded.action,
             idempotency_key=excluded.idempotency_key, expires_at=excluded.expires_at,
             consumed=excluded.consumed, consumed_by=excluded.consumed_by""",
        (
            approval.approval_id,
            approval.proposal_id,
            value.work_id,
            value.branch_id,
            approval.actor.actor_id,
            approval.actor.role,
            _encoded(scope),
            value.proposal_hash,
            approval.approval_hash,
            value.knowledge_version,
            value.knowledge_version,
            value.action,
            value.idempotency_key,
            approval.expires_at,
            int(value.consumed),
            value.consumed_by,
            _now(),
        ),
    )


def _mirror_review(cursor: Any, _key: str, value: ReviewRecord) -> None:
    report = value.report
    coordinate = report.coordinate
    cursor.execute(
        """INSERT INTO knowledge_reviews
           (report_id, work_id, branch_id, draft_ref, draft_hash,
            coordinate_json, context_hash, checks_json, overall_status,
            blocking_findings_json, report_hash, request_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(report_id) DO UPDATE SET
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             draft_ref=excluded.draft_ref, draft_hash=excluded.draft_hash,
             coordinate_json=excluded.coordinate_json,
             context_hash=excluded.context_hash, checks_json=excluded.checks_json,
             overall_status=excluded.overall_status,
             blocking_findings_json=excluded.blocking_findings_json,
             report_hash=excluded.report_hash, request_json=excluded.request_json""",
        (
            report.report_id,
            coordinate.work_id,
            coordinate.branch_id,
            report.draft_ref,
            report.draft_hash,
            _encoded(coordinate),
            report.context_hash,
            _encoded(report.checks),
            report.overall_status,
            _encoded(report.blocking_findings),
            report.report_hash,
            _encoded(value.request),
            _now(),
        ),
    )


def _mirror_proposal(cursor: Any, _key: str, value: ProposalRecord) -> None:
    proposal = value.proposal
    coordinate = value.review.coordinate
    cursor.execute(
        """INSERT INTO knowledge_proposals
           (proposal_id, work_id, branch_id, draft_ref, draft_hash, review_ref,
            state_change_set_ref, context_hash, knowledge_version, status,
            proposal_hash, idempotency_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(proposal_id) DO UPDATE SET
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             draft_ref=excluded.draft_ref, draft_hash=excluded.draft_hash,
             review_ref=excluded.review_ref,
             state_change_set_ref=excluded.state_change_set_ref,
             context_hash=excluded.context_hash,
             knowledge_version=excluded.knowledge_version, status=excluded.status,
             proposal_hash=excluded.proposal_hash, idempotency_key=excluded.idempotency_key""",
        (
            proposal.proposal_id,
            coordinate.work_id,
            coordinate.branch_id,
            proposal.draft_ref,
            proposal.draft_hash,
            proposal.review_ref,
            proposal.state_change_set_ref,
            proposal.context_hash,
            proposal.knowledge_version,
            proposal.status,
            proposal.proposal_hash,
            proposal.proposal_id,
            _now(),
        ),
    )


def _mirror_commit(cursor: Any, _key: str, value: CommitRecord, chapter: Mapping[str, Any], changes: Sequence[Mapping[str, Any]]) -> None:
    commit = value.commit
    cursor.execute(
        """INSERT INTO knowledge_commits
           (commit_id, proposal_id, work_id, branch_id, knowledge_version,
            chapter_version, actor_id, idempotency_key, status, commit_hash,
            fingerprint, chapter_json, state_changes_json, receipt_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(commit_id) DO UPDATE SET
             proposal_id=excluded.proposal_id, work_id=excluded.work_id,
             branch_id=excluded.branch_id, knowledge_version=excluded.knowledge_version,
             chapter_version=excluded.chapter_version, actor_id=excluded.actor_id,
             idempotency_key=excluded.idempotency_key, status=excluded.status,
             commit_hash=excluded.commit_hash, fingerprint=excluded.fingerprint,
             chapter_json=excluded.chapter_json,
             state_changes_json=excluded.state_changes_json,
             receipt_json=excluded.receipt_json""",
        (
            commit.commit_id,
            commit.proposal_ref,
            commit.work_id,
            commit.branch_id,
            commit.knowledge_version,
            commit.chapter_version,
            commit.actor,
            commit.idempotency_key,
            commit.status,
            commit.commit_hash,
            value.fingerprint,
            _encoded(chapter),
            _encoded(changes),
            _encoded(value.receipt),
            _now(),
        ),
    )


def _mirror_projection_task(cursor: Any, task: Mapping[str, Any], *, work_id: str, branch_id: str, source_hash: str) -> None:
    projection_id = str(task.get("projection_id") or task.get("projection_kind") or "unknown")
    status = str(task.get("status", "PENDING"))
    retry_count = task.get("retry_count", 0)
    if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
        retry_count = 0
    cursor.execute(
        """INSERT INTO knowledge_projection_tasks
           (task_id, work_id, branch_id, projection_id, knowledge_version,
            source_hash, projection_hash, status, retry_count, error_code,
            error_message, dead_letter, idempotency_key, created_at, updated_at,
            manifest_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(task_id) DO UPDATE SET
             work_id=excluded.work_id, branch_id=excluded.branch_id,
             projection_id=excluded.projection_id,
             knowledge_version=excluded.knowledge_version,
             source_hash=excluded.source_hash,
             projection_hash=excluded.projection_hash, status=excluded.status,
             retry_count=excluded.retry_count, error_code=excluded.error_code,
             error_message=excluded.error_message, dead_letter=excluded.dead_letter,
             idempotency_key=excluded.idempotency_key,
             updated_at=excluded.updated_at, manifest_json=excluded.manifest_json""",
        (
            str(task["task_id"]),
            work_id,
            branch_id,
            projection_id,
            str(task.get("knowledge_version", "")),
            source_hash,
            task.get("projection_hash"),
            status,
            retry_count,
            task.get("error_code"),
            task.get("error_message"),
            int(bool(task.get("dead_letter", False))),
            task.get("idempotency_key"),
            _now(),
            _now(),
            _encoded(task),
        ),
    )


def _mirror_promotion(cursor: Any, _key: str, value: PromotionReceipt, *, work_id: str, branch_id: str) -> None:
    cursor.execute(
        """INSERT INTO knowledge_promotions
           (promotion_id, candidate_id, approval_id, expected_head,
            new_knowledge_version, receipt_json, receipt_hash, work_id,
            branch_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(promotion_id) DO UPDATE SET
             candidate_id=excluded.candidate_id, approval_id=excluded.approval_id,
             expected_head=excluded.expected_head,
             new_knowledge_version=excluded.new_knowledge_version,
             receipt_json=excluded.receipt_json, receipt_hash=excluded.receipt_hash,
             work_id=excluded.work_id, branch_id=excluded.branch_id""",
        (
            value.promotion_id,
            value.candidate_id,
            value.approval_ref.approval_id,
            value.expected_head,
            value.new_knowledge_version,
            _encoded(value),
            value.receipt_hash,
            work_id,
            branch_id,
            _now(),
        ),
    )


class SQLiteDecisionRepository:
    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def get(self, decision_id: str) -> Decision | None:
        return self.store.model("decision", decision_id, Decision)

    def save(self, decision: Decision) -> None:
        work_id, branch_id = _scope_parts(decision.actor)
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="decision",
                record_id=decision.decision_id,
                payload=decision,
                work_id=work_id,
                branch_id=branch_id,
                status=decision.action.value,
                content_hash=decision.decision_hash,
                immutable=True,
            )
            _mirror_decision(cursor, decision.decision_id, decision)

    def list(self) -> Sequence[Decision]:
        return self.store.models("decision", Decision)


def _payload_mapping(store: SQLiteKnowledgeStore, row: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    payload = store.decode_value(row)
    if not isinstance(payload, Mapping):
        raise SQLiteAuthorityError(f"durable {label} record is not an object", "INVALID_SCHEMA")
    return payload


def _approval_record_from_row(store: SQLiteKnowledgeStore, row: Mapping[str, Any]) -> ApprovalRecord:
    payload = _payload_mapping(store, row, "approval")
    try:
        approval = ApprovalRef.model_validate(payload["approval"])
        return ApprovalRecord(
            approval=approval,
            proposal_hash=str(payload["proposal_hash"]),
            work_id=str(payload["work_id"]),
            branch_id=str(payload["branch_id"]),
            knowledge_version=str(payload["knowledge_version"]),
            action=str(payload["action"]),
            idempotency_key=str(payload["idempotency_key"]),
            consumed_by=(
                None
                if payload.get("consumed_by") is None
                else str(payload["consumed_by"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SQLiteAuthorityError("durable approval record is invalid", "INVALID_SCHEMA") from exc


def _review_record_from_row(store: SQLiteKnowledgeStore, row: Mapping[str, Any]) -> ReviewRecord:
    payload = _payload_mapping(store, row, "review")
    try:
        return ReviewRecord(
            report=ReviewReport.model_validate(payload["report"]),
            request=ReviewRequest.model_validate(payload["request"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SQLiteAuthorityError("durable review record is invalid", "INVALID_SCHEMA") from exc


def _proposal_record_from_row(store: SQLiteKnowledgeStore, row: Mapping[str, Any]) -> ProposalRecord:
    payload = _payload_mapping(store, row, "proposal")
    try:
        return ProposalRecord(
            proposal=Proposal.model_validate(payload["proposal"]),
            review=ReviewReport.model_validate(payload["review"]),
            state_change_set=StateChangeSet.model_validate(payload["state_change_set"]),
            context_view=ContextView.model_validate(payload["context_view"]),
            knowledge_head=KnowledgeHead.model_validate(payload["knowledge_head"]),
            source_composite=(
                None
                if payload.get("source_composite") is None
                else SourceCompositeRef.model_validate(payload["source_composite"])
            ),
            source_snapshot_ref=(
                None
                if payload.get("source_snapshot_ref") is None
                else str(payload["source_snapshot_ref"])
            ),
            source_id=None if payload.get("source_id") is None else str(payload["source_id"]),
            source_version=(
                None
                if payload.get("source_version") is None
                else str(payload["source_version"])
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SQLiteAuthorityError("durable proposal record is invalid", "INVALID_SCHEMA") from exc


def _commit_record_from_row(store: SQLiteKnowledgeStore, row: Mapping[str, Any]) -> CommitRecord:
    payload = _payload_mapping(store, row, "commit")
    try:
        return CommitRecord(
            commit=Commit.model_validate(payload["commit"]),
            receipt=CommitReceipt.model_validate(payload["receipt"]),
            fingerprint=str(payload["fingerprint"]),
            knowledge_version=KnowledgeVersion.model_validate(payload["knowledge_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SQLiteAuthorityError("durable commit record is invalid", "INVALID_SCHEMA") from exc


def _head_from_store(store: SQLiteKnowledgeStore, work_id: str, branch_id: str) -> KnowledgeHead | None:
    record_id = f"{work_id}:{branch_id}"
    row = store.runtime_row("head", record_id)
    if row is not None:
        return store.decode(row, KnowledgeHead)
    typed = store._row(
        "SELECT work_id, branch_id, knowledge_version, version_hash, cas_revision "
        "FROM knowledge_heads WHERE work_id = ? AND branch_id = ?",
        (work_id, branch_id),
    )
    if typed is None:
        return None
    try:
        return KnowledgeHead.model_validate(typed)
    except Exception as exc:
        raise SQLiteAuthorityError("durable knowledge head is invalid", "INVALID_SCHEMA") from exc


class SQLiteApprovalRepository:
    """Durable approval records with one-way consumption."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def get(self, approval_id: str) -> ApprovalRef | None:
        record = self.get_record(approval_id)
        return None if record is None else record.approval.model_copy(update={"consumed": record.consumed})

    def get_record(self, approval_id: str) -> ApprovalRecord | None:
        row = self.store.runtime_row("approval_record", approval_id)
        return None if row is None else _approval_record_from_row(self.store, row)

    def get_by_idempotency(self, proposal_id: str, idempotency_key: str) -> ApprovalRecord | None:
        for row in self.store.runtime_rows("approval_record"):
            record = _approval_record_from_row(self.store, row)
            if record.approval.proposal_id == proposal_id and record.idempotency_key == idempotency_key:
                return record
        return None

    def save_record(self, record: ApprovalRecord) -> None:
        if not isinstance(record, ApprovalRecord):
            raise SQLiteAuthorityError("approval repository accepts ApprovalRecord only")
        existing = self.get_record(record.approval.approval_id)
        if existing is not None and _record_fingerprint(existing) != _record_fingerprint(record):
            raise SQLiteAuthorityError(
                "approval_id is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )
        prior = self.get_by_idempotency(record.approval.proposal_id, record.idempotency_key)
        if prior is not None and prior.approval.approval_id != record.approval.approval_id:
            raise SQLiteAuthorityError(
                "approval idempotency key is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )
        self._persist_record(record)

    def _persist_record(self, record: ApprovalRecord) -> None:
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="approval_record",
                record_id=record.approval.approval_id,
                payload=record,
                work_id=record.work_id,
                branch_id=record.branch_id,
                idempotency_key=record.idempotency_key,
                status="CONSUMED" if record.consumed else "ACTIVE",
                content_hash=_record_fingerprint(record),
                immutable=False,
            )
            _mirror_approval(cursor, record.approval.approval_id, record)

    def consume(self, approval_id: str, commit_id: str) -> ApprovalRecord:
        record = self.get_record(approval_id)
        if record is None:
            raise SQLiteAuthorityError("approval not found", "APPROVAL_REQUIRED")
        if record.consumed:
            raise SQLiteAuthorityError("approval has already been consumed", "APPROVAL_REQUIRED")
        updated = replace(record, consumed_by=commit_id)
        self._persist_record(updated)
        return updated

    def restore_consumption(self, approval_id: str, consumed_by: str | None) -> None:
        record = self.get_record(approval_id)
        if record is not None:
            self._persist_record(replace(record, consumed_by=consumed_by))


class SQLiteReviewRepository:
    """Durable final review reports and their request bindings."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def get(self, report_id: str):
        record = self.get_record(report_id)
        return None if record is None else record.report.model_copy(deep=True)

    def get_record(self, report_id: str) -> ReviewRecord | None:
        row = self.store.runtime_row("review_record", report_id)
        return None if row is None else _review_record_from_row(self.store, row)

    def save(self, report: Any) -> None:
        if not hasattr(report, "report_id"):
            raise SQLiteAuthorityError("review repository accepts ReviewReport only")
        self.save_record(
            ReviewRecord(
                report=report,
                request=ReviewRequest(
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
                ),
            )
        )

    def save_record(self, record: ReviewRecord) -> None:
        if not isinstance(record, ReviewRecord):
            raise SQLiteAuthorityError("review repository accepts ReviewRecord only")
        existing = self.get_record(record.report.report_id)
        if existing is not None and existing.report.report_hash != record.report.report_hash:
            raise SQLiteAuthorityError(
                "report_id is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="review_record",
                record_id=record.report.report_id,
                payload=record,
                work_id=record.report.coordinate.work_id,
                branch_id=record.report.coordinate.branch_id,
                status=record.report.overall_status,
                content_hash=record.report.report_hash,
                immutable=True,
            )
            _mirror_review(cursor, record.report.report_id, record)


class SQLiteProposalRepository:
    """Durable proposals retaining all immutable downstream metadata."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store

    def get(self, proposal_id: str) -> Proposal | None:
        record = self.get_record(proposal_id)
        return None if record is None else record.proposal.model_copy(deep=True)

    def get_record(self, proposal_id: str) -> ProposalRecord | None:
        row = self.store.runtime_row("proposal_record", proposal_id)
        return None if row is None else _proposal_record_from_row(self.store, row)

    def save(self, proposal: Proposal) -> None:
        record = self.get_record(proposal.proposal_id)
        if record is None:
            raise SQLiteAuthorityError(
                "proposal metadata is required; use save_record",
                "INVALID_SCHEMA",
            )
        if record.proposal.proposal_hash != proposal.proposal_hash:
            raise SQLiteAuthorityError(
                "proposal_id is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )

    def save_record(self, record: ProposalRecord) -> None:
        if not isinstance(record, ProposalRecord):
            raise SQLiteAuthorityError("proposal repository accepts ProposalRecord only")
        existing = self.get_record(record.proposal.proposal_id)
        if existing is not None and existing.proposal.proposal_hash != record.proposal.proposal_hash:
            raise SQLiteAuthorityError(
                "proposal_id is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )
        coordinate = record.review.coordinate
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="proposal_record",
                record_id=record.proposal.proposal_id,
                payload=record,
                work_id=coordinate.work_id,
                branch_id=coordinate.branch_id,
                idempotency_key=record.proposal.proposal_id,
                status=record.proposal.status,
                content_hash=record.proposal.proposal_hash,
                immutable=True,
            )
            _mirror_proposal(cursor, record.proposal.proposal_id, record)


class _SQLiteKnowledgeCollections:
    """Shared persistent maps for promotion and commit repositories."""

    def _init_collections(self, store: SQLiteKnowledgeStore) -> None:
        self.store = store
        self.objects = SQLiteModelMap(
            store,
            "object",
            KnowledgeObject,
            key_from_model=lambda value: value.object_id,
            record_id=lambda key: key,
            mirror=_mirror_object,
            immutable=True,
        )
        self.versions = SQLiteModelMap(
            store,
            "knowledge_version",
            KnowledgeVersion,
            key_from_model=lambda value: value.knowledge_version,
            record_id=lambda key: key,
            mirror=_mirror_version,
            immutable=True,
        )
        self.heads = SQLiteModelMap(
            store,
            "head",
            KnowledgeHead,
            key_from_model=lambda value: (value.work_id, value.branch_id),
            record_id=lambda key: f"{key[0]}:{key[1]}",
            mirror=_mirror_head,
            immutable=False,
        )

    def get_head(self, work_id: str, branch_id: str) -> KnowledgeHead | None:
        return _head_from_store(self.store, work_id, branch_id)

    def seed_head(self, head: KnowledgeHead) -> None:
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="head",
                record_id=f"{head.work_id}:{head.branch_id}",
                payload=head,
                work_id=head.work_id,
                branch_id=head.branch_id,
                status="ACTIVE",
                immutable=False,
            )
            _mirror_head(cursor, (head.work_id, head.branch_id), head)


class SQLitePromotionRepository(_SQLiteKnowledgeCollections):
    """Durable atomic authority used by Candidate→Promotion."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        self._init_collections(store)

    def get_receipt(self, promotion_id: str) -> PromotionReceipt | None:
        return self.store.model("promotion_receipt", promotion_id, PromotionReceipt)

    def get_approval(self, approval_id: str) -> ApprovalRef | None:
        record_row = self.store.runtime_row("approval_record", approval_id)
        if record_row is not None:
            record = _approval_record_from_row(self.store, record_row)
            return record.approval.model_copy(update={"consumed": record.consumed})
        row = self.store.runtime_row("approval_ref", approval_id)
        if row is None:
            return None
        return self.store.decode(row, ApprovalRef)

    def register_approval(self, approval: ApprovalRef) -> None:
        if not isinstance(approval, ApprovalRef):
            raise SQLiteAuthorityError("approval must be an ApprovalRef", "APPROVAL_REQUIRED")
        existing = self.get_approval(approval.approval_id)
        if existing is not None and existing.approval_hash != approval.approval_hash:
            raise SQLiteAuthorityError(
                "approval_id is already bound to different content",
                "IDEMPOTENCY_CONFLICT",
            )
        scope = approval.actor.scope
        with self.store.transaction() as cursor:
            self.store.put(
                cursor,
                record_type="approval_ref",
                record_id=approval.approval_id,
                payload=approval,
                work_id=None if scope is None else scope.work_id,
                branch_id=None if scope is None else scope.branch_id,
                idempotency_key=approval.proposal_id,
                status="CONSUMED" if approval.consumed else "ACTIVE",
                immutable=False,
            )

    def commit_promotion(
        self,
        *,
        candidate_id: str,
        approval_id: str,
        expected_head: str,
        object_record: KnowledgeObject,
        version: KnowledgeVersion,
        head: KnowledgeHead,
        receipt: PromotionReceipt,
        projection_task: Mapping[str, Any],
    ) -> None:
        with self.store.transaction() as cursor:
            current = _head_from_store(self.store, head.work_id, head.branch_id)
            current_value = "genesis" if current is None else current.knowledge_version
            current_hash = None if current is None else current.version_hash
            if current_value != expected_head and current_hash != expected_head:
                raise PromotionServiceError(
                    "knowledge head changed during promotion",
                    "CAS_CONFLICT",
                )
            approval = self.get_approval(approval_id)
            if approval is None or approval.consumed:
                raise PromotionServiceError(
                    "approval is missing or already consumed",
                    "APPROVAL_REQUIRED",
                )
            self.store.put(
                cursor,
                record_type="object",
                record_id=object_record.object_id,
                payload=object_record,
                work_id=object_record.work_id,
                branch_id=object_record.branch_id,
                status=object_record.status,
                immutable=True,
            )
            _mirror_object(cursor, object_record.object_id, object_record)
            self.store.put(
                cursor,
                record_type="knowledge_version",
                record_id=version.knowledge_version,
                payload=version,
                work_id=version.work_id,
                branch_id=version.branch_id,
                status=version.status,
                immutable=True,
            )
            _mirror_version(cursor, version.knowledge_version, version)
            self.store.put(
                cursor,
                record_type="head",
                record_id=f"{head.work_id}:{head.branch_id}",
                payload=head,
                work_id=head.work_id,
                branch_id=head.branch_id,
                status="ACTIVE",
                immutable=False,
            )
            _mirror_head(cursor, (head.work_id, head.branch_id), head)
            task = dict(projection_task)
            task.setdefault("knowledge_version", version.knowledge_version)
            task_source_hash = sha256_hex({"promotion": receipt.receipt_hash, "task": task})
            self.store.put(
                cursor,
                record_type="promotion_projection_task",
                record_id=str(task["task_id"]),
                payload=task,
                work_id=head.work_id,
                branch_id=head.branch_id,
                idempotency_key=str(task["task_id"]),
                status=str(task.get("status", "PENDING")),
                immutable=False,
            )
            _mirror_projection_task(
                cursor,
                task,
                work_id=head.work_id,
                branch_id=head.branch_id,
                source_hash=task_source_hash,
            )
            self.store.put(
                cursor,
                record_type="promotion_receipt",
                record_id=receipt.promotion_id,
                payload=receipt,
                work_id=head.work_id,
                branch_id=head.branch_id,
                idempotency_key=receipt.promotion_id,
                status="PROMOTED",
                content_hash=receipt.receipt_hash,
                immutable=True,
            )
            if self.store.runtime_row("approval_record", approval_id) is not None:
                record = _approval_record_from_row(
                    self.store,
                    self.store.runtime_row("approval_record", approval_id),  # type: ignore[arg-type]
                )
                updated = replace(record, consumed_by=receipt.promotion_id)
                self.store.put(
                    cursor,
                    record_type="approval_record",
                    record_id=approval_id,
                    payload=updated,
                    work_id=updated.work_id,
                    branch_id=updated.branch_id,
                    idempotency_key=updated.idempotency_key,
                    status="CONSUMED",
                    content_hash=_record_fingerprint(updated),
                    immutable=False,
                )
                _mirror_approval(cursor, approval_id, updated)
            else:
                consumed = approval.model_copy(update={"consumed": True})
                self.store.put(
                    cursor,
                    record_type="approval_ref",
                    record_id=approval_id,
                    payload=consumed,
                    work_id=None if consumed.actor.scope is None else consumed.actor.scope.work_id,
                    branch_id=None if consumed.actor.scope is None else consumed.actor.scope.branch_id,
                    idempotency_key=consumed.proposal_id,
                    status="CONSUMED",
                    immutable=False,
                )
            _mirror_promotion(
                cursor,
                receipt.promotion_id,
                receipt,
                work_id=head.work_id,
                branch_id=head.branch_id,
            )


class SQLiteCommitRepository(SQLitePromotionRepository):
    """Durable transaction boundary used by Review→Commit."""

    def __init__(self, store: SQLiteKnowledgeStore) -> None:
        super().__init__(store)
        self.knowledge_versions = self.versions
        self.commits = SQLiteModelMap(
            store,
            "commit",
            Commit,
            key_from_model=lambda value: value.commit_id,
            record_id=lambda key: key,
            mirror=None,
            immutable=True,
        )
        self.chapters = SQLiteJsonMap(store, "commit_chapter", lambda key: str(key))
        self.projection_tasks = SQLiteJsonMap(store, "commit_projection_task", lambda key: str(key))

    def get_receipt(self, work_id: str, idempotency_key: str) -> CommitReceipt | None:
        row = self.store.runtime_row_by_idempotency("commit_record", work_id, idempotency_key)
        if row is None:
            return None
        return _commit_record_from_row(self.store, row).receipt

    def get_fingerprint(self, work_id: str, idempotency_key: str) -> str | None:
        row = self.store.runtime_row_by_idempotency("commit_record", work_id, idempotency_key)
        if row is None:
            return None
        return _commit_record_from_row(self.store, row).fingerprint

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.store.transaction() as cursor:
            yield cursor

    def save(
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
        with self.store.transaction() as cursor:
            existing_row = self.store.runtime_row_by_idempotency(
                "commit_record", work_id, idempotency_key
            )
            if existing_row is not None:
                existing = _commit_record_from_row(self.store, existing_row)
                if existing.fingerprint != fingerprint:
                    raise CommitServiceError(
                        "idempotency key is already bound to different content",
                        "IDEMPOTENCY_CONFLICT",
                    )
                return
            chapter_id = f"{work_id}:{branch_id}:{commit.chapter_version}"
            prior_chapter = self.store.runtime_row("commit_chapter", chapter_id)
            if prior_chapter is not None:
                prior_payload = _payload_mapping(self.store, prior_chapter, "chapter")
                if prior_payload.get("draft_hash") != chapter.get("draft_hash"):
                    raise CommitServiceError(
                        "chapter version is already bound to different content",
                        "IDEMPOTENCY_CONFLICT",
                    )
            record = CommitRecord(
                commit=commit,
                receipt=receipt,
                fingerprint=fingerprint,
                knowledge_version=knowledge_version,
            )
            self.store.put(
                cursor,
                record_type="knowledge_version",
                record_id=knowledge_version.knowledge_version,
                payload=knowledge_version,
                work_id=work_id,
                branch_id=branch_id,
                status=knowledge_version.status,
                immutable=True,
            )
            _mirror_version(cursor, knowledge_version.knowledge_version, knowledge_version)
            self.store.put(
                cursor,
                record_type="head",
                record_id=f"{work_id}:{branch_id}",
                payload=head,
                work_id=work_id,
                branch_id=branch_id,
                status="ACTIVE",
                immutable=False,
            )
            _mirror_head(cursor, (work_id, branch_id), head)
            self.store.put(
                cursor,
                record_type="commit_chapter",
                record_id=chapter_id,
                payload=dict(chapter),
                work_id=work_id,
                branch_id=branch_id,
                idempotency_key=commit.chapter_version,
                status="COMMITTED",
                immutable=True,
            )
            self.store.put(
                cursor,
                record_type="commit_state_changes",
                record_id=commit.commit_id,
                payload=changes,
                work_id=work_id,
                branch_id=branch_id,
                idempotency_key=commit.commit_id,
                status="COMMITTED",
                immutable=True,
            )
            self.store.put(
                cursor,
                record_type="commit",
                record_id=commit.commit_id,
                payload=commit,
                work_id=work_id,
                branch_id=branch_id,
                idempotency_key=commit.idempotency_key,
                status=commit.status,
                content_hash=commit.commit_hash,
                immutable=True,
            )
            self.store.put(
                cursor,
                record_type="commit_record",
                record_id=commit.commit_id,
                payload=record,
                work_id=work_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                status="COMMITTED",
                content_hash=fingerprint,
                immutable=True,
            )
            _mirror_commit(cursor, commit.commit_id, record, chapter, changes)
            for task in projection_tasks:
                task_data = dict(task)
                task_id = str(task_data["task_id"])
                task_data.setdefault("knowledge_version", knowledge_version.knowledge_version)
                self.store.put(
                    cursor,
                    record_type="commit_projection_task",
                    record_id=task_id,
                    payload=task_data,
                    work_id=work_id,
                    branch_id=branch_id,
                    idempotency_key=task_id,
                    status=str(task_data.get("status", "PENDING")),
                    immutable=False,
                )
                _mirror_projection_task(
                    cursor,
                    task_data,
                    work_id=work_id,
                    branch_id=branch_id,
                    source_hash=sha256_hex({"commit": fingerprint, "task": task_data}),
                )


__all__ = [
    "SQLiteAuthorityError",
    "SQLiteKnowledgeStore",
    "SQLiteModelMap",
    "SQLiteJsonMap",
    "SQLiteValueMap",
    "SQLiteSnapshotActiveMap",
    "SQLiteSourceBindingMap",
    "SQLiteSnapshotService",
    "SQLiteBranchState",
    "SQLiteContextCache",
    "SQLiteCandidateRepository",
    "SQLiteEvaluationRepository",
    "SQLiteDecisionRepository",
    "SQLiteApprovalRepository",
    "SQLiteReviewRepository",
    "SQLiteProposalRepository",
    "SQLitePromotionRepository",
    "SQLiteCommitRepository",
]


__all__ = [
    "SQLiteAuthorityError",
    "SQLiteKnowledgeStore",
    "SQLiteModelMap",
    "SQLiteJsonMap",
    "SQLiteValueMap",
    "SQLiteCandidateRepository",
    "SQLiteEvaluationRepository",
    "SQLiteDecisionRepository",
]
