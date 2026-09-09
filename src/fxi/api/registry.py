"""Explicit work and source registration services for API boundaries.

The API must never infer a work from a source directory name or from a
``*_fanfic`` convention.  ``WorkRegistry`` keeps that relationship in an
explicit SQLite table and exposes only work-scoped lookups.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any, Callable, Iterator


WORK_SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_sources (
    source_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    source_dir TEXT,
    source_version TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_id, work_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE INDEX IF NOT EXISTS idx_work_sources_work_id
    ON work_sources(work_id);
"""


class RegistryError(RuntimeError):
    """Base error for registry failures that callers must handle explicitly."""

    code = "REGISTRY_UNAVAILABLE"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class RegistryUnavailableError(RegistryError):
    """The database or required registry schema could not be used."""


class WorkNotFoundError(RegistryError):
    """The requested work is not registered."""

    code = "WORK_NOT_FOUND"


class SourceNotFoundError(RegistryError):
    """The source is not explicitly bound to the requested work."""

    code = "SOURCE_NOT_FOUND"


class SourceScopeRequiredError(RegistryError):
    """More than one source is bound and the caller did not choose one."""

    code = "SOURCE_SCOPE_REQUIRED"


class SourceVersionMismatchError(RegistryError):
    """A requested source version does not match the registered binding."""

    code = "SOURCE_VERSION_MISMATCH"


class SourceBindingConflictError(RegistryError):
    """A source is already bound to a different work or metadata."""

    code = "SOURCE_BINDING_CONFLICT"


@dataclass(frozen=True)
class WorkRecord:
    """The small, stable work identity used by API authorization."""

    work_id: str
    owner_id: str
    slug: str
    title: str
    source_dir: str | None = None
    skill_root: str | None = None


@dataclass(frozen=True)
class SourceBinding:
    """An explicit source-to-work registration."""

    source_id: str
    work_id: str
    source_dir: str | None = None
    source_version: str | None = None
    created_at: str = ""


ConnectionFactory = Callable[[], sqlite3.Connection]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return row[index]


def _db_error(message: str, exc: sqlite3.Error) -> RegistryUnavailableError:
    return RegistryUnavailableError(message)


def ensure_registry_schema(connection: sqlite3.Connection) -> None:
    """Create only the explicit source binding table on a supplied connection."""

    try:
        connection.executescript(WORK_SOURCE_SCHEMA)
        connection.commit()
    except sqlite3.Error as exc:
        raise _db_error("registry schema is unavailable", exc) from exc


class WorkRegistry:
    """Read and write explicit work/source registrations.

    ``connection`` may be an existing SQLite connection, a callable returning
    one, or an object exposing ``get_connection`` (such as Fxi's
    ``DatabaseClient``).  No filesystem lookup is performed by this class.
    """

    def __init__(self, connection: Any, *, initialize_schema: bool = True):
        if hasattr(connection, "get_connection") and callable(connection.get_connection):
            self._connection: sqlite3.Connection | None = None
            self._connection_factory: ConnectionFactory = connection.get_connection
        elif callable(connection) and not hasattr(connection, "execute"):
            self._connection = None
            self._connection_factory = connection
        else:
            self._connection = connection
            self._connection_factory = None

        if initialize_schema:
            with self._connections() as conn:
                ensure_registry_schema(conn)

    @contextmanager
    def _connections(self) -> Iterator[sqlite3.Connection]:
        if self._connection is not None:
            yield self._connection
            return

        if self._connection_factory is None:
            raise RegistryUnavailableError("registry has no database connection")

        conn = self._connection_factory()
        try:
            yield conn
        finally:
            conn.close()

    @classmethod
    def from_database_client(cls, client: Any, *, initialize_schema: bool = True) -> "WorkRegistry":
        """Build a registry from an Fxi ``DatabaseClient``-compatible object."""

        return cls(client, initialize_schema=initialize_schema)

    def get_work(self, work_id: str) -> WorkRecord | None:
        work_id = _require_identifier(work_id, "work_id")
        try:
            with self._connections() as conn:
                row = conn.execute(
                    """
                    SELECT work_id, owner_id, slug, title, source_dir, skill_root
                    FROM works
                    WHERE work_id = ?
                    """,
                    (work_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise _db_error("work registry is unavailable", exc) from exc

        if row is None:
            return None
        return WorkRecord(
            work_id=str(_row_value(row, "work_id", 0)),
            owner_id=str(_row_value(row, "owner_id", 1)),
            slug=str(_row_value(row, "slug", 2)),
            title=str(_row_value(row, "title", 3)),
            source_dir=_row_value(row, "source_dir", 4),
            skill_root=_row_value(row, "skill_root", 5),
        )

    def require_work(self, work_id: str) -> WorkRecord:
        work = self.get_work(work_id)
        if work is None:
            raise WorkNotFoundError("work is not registered")
        return work

    def create_work(
        self,
        work_id: str,
        *,
        owner_id: str,
        slug: str | None = None,
        title: str | None = None,
        source_dir: str | None = None,
        skill_root: str | None = None,
    ) -> WorkRecord:
        """Create or replay one explicit work registration.

        The caller supplies the authenticated owner identity.  This method
        intentionally does not inspect project directories or infer a work
        from a path; the API adapter is responsible for path validation.
        """

        work_id = _require_identifier(work_id, "work_id")
        owner_id = _require_identifier(owner_id, "owner_id")
        slug = _require_identifier(slug or work_id, "slug")
        title = _require_identifier(title or work_id, "title")
        if source_dir is not None:
            source_dir = _require_identifier(source_dir, "source_dir")
        if skill_root is not None:
            skill_root = _require_identifier(skill_root, "skill_root")

        try:
            with self._connections() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO authors
                        (owner_id, slug, display_name, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (owner_id, owner_id, owner_id, _now()),
                )
                row = conn.execute(
                    """
                    SELECT work_id, owner_id, slug, title, source_dir, skill_root
                    FROM works
                    WHERE work_id = ?
                    """,
                    (work_id,),
                ).fetchone()
                if row is not None:
                    existing = WorkRecord(
                        work_id=str(_row_value(row, "work_id", 0)),
                        owner_id=str(_row_value(row, "owner_id", 1)),
                        slug=str(_row_value(row, "slug", 2)),
                        title=str(_row_value(row, "title", 3)),
                        source_dir=_row_value(row, "source_dir", 4),
                        skill_root=_row_value(row, "skill_root", 5),
                    )
                    requested = WorkRecord(
                        work_id=work_id,
                        owner_id=owner_id,
                        slug=slug,
                        title=title,
                        source_dir=source_dir,
                        skill_root=skill_root,
                    )
                    if existing != requested:
                        raise RegistryError("work_id is already bound to different content", "IDEMPOTENCY_CONFLICT")
                    return existing
                conn.execute(
                    """
                    INSERT INTO works
                        (work_id, owner_id, slug, title, source_dir, skill_root,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        work_id,
                        owner_id,
                        slug,
                        title,
                        source_dir,
                        skill_root,
                        _now(),
                        _now(),
                    ),
                )
                conn.commit()
        except RegistryError:
            raise
        except sqlite3.IntegrityError as exc:
            raise RegistryError("work registration conflicts with an existing owner or slug", "IDEMPOTENCY_CONFLICT") from exc
        except sqlite3.Error as exc:
            raise _db_error("work registry is unavailable", exc) from exc

        return WorkRecord(
            work_id=work_id,
            owner_id=owner_id,
            slug=slug,
            title=title,
            source_dir=source_dir,
            skill_root=skill_root,
        )

    def get_source(self, work_id: str, source_id: str) -> SourceBinding | None:
        work_id = _require_identifier(work_id, "work_id")
        source_id = _require_identifier(source_id, "source_id")
        try:
            with self._connections() as conn:
                row = conn.execute(
                    """
                    SELECT source_id, work_id, source_dir, source_version, created_at
                    FROM work_sources
                    WHERE work_id = ? AND source_id = ?
                    """,
                    (work_id, source_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise _db_error("source registry is unavailable", exc) from exc

        if row is None:
            return None
        return SourceBinding(
            source_id=str(_row_value(row, "source_id", 0)),
            work_id=str(_row_value(row, "work_id", 1)),
            source_dir=_row_value(row, "source_dir", 2),
            source_version=_row_value(row, "source_version", 3),
            created_at=str(_row_value(row, "created_at", 4)),
        )

    def require_source(self, work_id: str, source_id: str) -> SourceBinding:
        self.require_work(work_id)
        binding = self.get_source(work_id, source_id)
        if binding is None:
            raise SourceNotFoundError("source is not explicitly bound to work")
        return binding

    def resolve(self, work_id: str, source_id: str | None = None) -> SourceBinding:
        """Resolve one explicit source binding for a work.

        A caller may omit ``source_id`` only when the work has exactly one
        registered source.  Directory names and source manifests are not
        consulted here; this method is deliberately a database-owned scope
        boundary.
        """

        work_id = _require_identifier(work_id, "work_id")
        self.require_work(work_id)
        if source_id is not None:
            return self.require_source(work_id, source_id)
        bindings = self.list_sources(work_id)
        if not bindings:
            raise SourceNotFoundError("work has no registered source")
        if len(bindings) > 1:
            raise SourceScopeRequiredError("multiple sources are bound; source_id is required")
        return bindings[0]

    def list_sources(self, work_id: str) -> tuple[SourceBinding, ...]:
        self.require_work(work_id)
        try:
            with self._connections() as conn:
                rows = conn.execute(
                    """
                    SELECT source_id, work_id, source_dir, source_version, created_at
                    FROM work_sources
                    WHERE work_id = ?
                    ORDER BY source_id
                    """,
                    (work_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise _db_error("source registry is unavailable", exc) from exc

        return tuple(
            SourceBinding(
                source_id=str(_row_value(row, "source_id", 0)),
                work_id=str(_row_value(row, "work_id", 1)),
                source_dir=_row_value(row, "source_dir", 2),
                source_version=_row_value(row, "source_version", 3),
                created_at=str(_row_value(row, "created_at", 4)),
            )
            for row in rows
        )

    def register_source(
        self,
        work_id: str,
        source_id: str,
        *,
        source_dir: str | None = None,
        source_version: str | None = None,
    ) -> SourceBinding:
        """Register a source explicitly and idempotently for one existing work."""

        work_id = _require_identifier(work_id, "work_id")
        source_id = _require_identifier(source_id, "source_id")
        if source_dir is not None:
            source_dir = _require_identifier(source_dir, "source_dir")
        if source_version is not None:
            source_version = _require_identifier(source_version, "source_version")

        self.require_work(work_id)
        try:
            with self._connections() as conn:
                existing_row = conn.execute(
                    """
                    SELECT source_id, work_id, source_dir, source_version, created_at
                    FROM work_sources
                    WHERE source_id = ? AND work_id = ?
                    """,
                    (source_id, work_id),
                ).fetchone()
                other_binding = conn.execute(
                    """
                    SELECT work_id
                    FROM work_sources
                    WHERE source_id = ? AND work_id <> ?
                    LIMIT 1
                    """,
                    (source_id, work_id),
                ).fetchone()
                if other_binding is not None:
                    raise SourceBindingConflictError(
                        "source is already bound to a different work"
                    )
                if existing_row is not None:
                    existing = SourceBinding(
                        source_id=str(_row_value(existing_row, "source_id", 0)),
                        work_id=str(_row_value(existing_row, "work_id", 1)),
                        source_dir=_row_value(existing_row, "source_dir", 2),
                        source_version=_row_value(existing_row, "source_version", 3),
                        created_at=str(_row_value(existing_row, "created_at", 4)),
                    )
                    if source_dir is not None and existing.source_dir != source_dir:
                        raise SourceBindingConflictError("source directory binding differs")
                    if source_version is not None and existing.source_version != source_version:
                        raise SourceBindingConflictError("source version binding differs")
                    return existing

                created_at = _now()
                conn.execute(
                    """
                    INSERT INTO work_sources
                        (source_id, work_id, source_dir, source_version, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source_id, work_id, source_dir, source_version, created_at),
                )
                conn.commit()
        except SourceBindingConflictError:
            raise
        except sqlite3.IntegrityError as exc:
            raise SourceBindingConflictError("source binding could not be registered") from exc
        except sqlite3.Error as exc:
            raise _db_error("source registry is unavailable", exc) from exc

        return SourceBinding(
            source_id=source_id,
            work_id=work_id,
            source_dir=source_dir,
            source_version=source_version,
            created_at=created_at,
        )


__all__ = [
    "WORK_SOURCE_SCHEMA",
    "RegistryError",
    "RegistryUnavailableError",
    "WorkNotFoundError",
    "SourceNotFoundError",
    "SourceScopeRequiredError",
    "SourceVersionMismatchError",
    "SourceBindingConflictError",
    "WorkRecord",
    "SourceBinding",
    "ensure_registry_schema",
    "WorkRegistry",
]
