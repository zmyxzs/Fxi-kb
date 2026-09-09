"""Lifecycle runner and lexical baseline for disposable projections."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import time
from typing import Any

from .contracts import ContextView, ErrorCode, _stable_hash
from .objects import KnowledgeVersion
from .projection_contracts import (
    ProjectionError,
    ProjectionHealth,
    ProjectionManifest,
    ProjectionProvider,
    ProjectionStatus,
)


def _by_key(values: Any, key: str) -> dict[str, Any]:
    if values is None:
        return {}
    if isinstance(values, Mapping):
        return {str(k): v for k, v in values.items()}
    return {str(getattr(value, key)): value for value in values}


def _scope_hash(snapshot: KnowledgeVersion) -> str:
    return _stable_hash({"work_id": snapshot.work_id, "branch_id": snapshot.branch_id})


def _manifest(
    snapshot: KnowledgeVersion,
    provider: ProjectionProvider,
    records: Sequence[Mapping[str, Any]] = (),
    *,
    duration_ms: float = 0.0,
    context_view: ContextView | None = None,
    status: str = ProjectionStatus.BUILT.value,
    error_code: str | None = None,
    error_message: str | None = None,
    retry_count: int = 0,
    dead_letter: bool = False,
) -> ProjectionManifest:
    normalized = tuple(dict(item) for item in records)
    projection_hash = _stable_hash(
        {
            "projection_id": provider.projection_id,
            "projection_version": provider.projection_version,
            "knowledge_version": snapshot.knowledge_version,
            "version_hash": snapshot.version_hash,
            "scope_hash": _scope_hash(snapshot),
            "context_view_hash": context_view.view_hash if context_view is not None else None,
            "records": normalized,
        }
    )
    return ProjectionManifest(
        projection_id=provider.projection_id,
        projection_version=provider.projection_version,
        work_id=snapshot.work_id,
        branch_id=snapshot.branch_id,
        knowledge_version=snapshot.knowledge_version,
        knowledge_version_hash=snapshot.version_hash,
        source_refs=snapshot.source_snapshot_refs,
        context_view_id=context_view.view_id if context_view is not None else None,
        context_view_hash=context_view.view_hash if context_view is not None else None,
        scope_hash=_scope_hash(snapshot),
        projection_hash=projection_hash,
        status=status,
        record_count=len(normalized),
        duration_ms=max(0.0, float(duration_ms)),
        error_code=error_code,
        error_message=error_message,
        retry_count=retry_count,
        dead_letter=dead_letter,
        readiness="READY" if status == ProjectionStatus.BUILT.value else "NOT_READY",
        records=normalized,
    )


class LexicalProjectionProvider:
    """FTS baseline using an injected lexical adapter.

    The default adapter indexes only opaque authority references.  Applications
    may inject a callable/object that accepts a ``KnowledgeVersion`` and
    returns mapping records for their existing FTS implementation; this class
    never writes the legacy ``ChineseFTS`` storage itself.
    """

    projection_id = "fts"
    projection_version = "v3-lexical-1"

    def __init__(
        self,
        lexical_provider: Any = None,
        *,
        records: Mapping[str, Any] | Sequence[Any] | None = None,
    ) -> None:
        self.lexical_provider = lexical_provider
        self.records = records
        self._context_view: ContextView | None = None
        self._records: dict[tuple[str, str], tuple[Mapping[str, Any], ...]] = {}

    def bind_context_view(self, context_view: ContextView | None) -> None:
        self._context_view = context_view

    @staticmethod
    def _record_id(value: Any) -> str | None:
        if isinstance(value, Mapping):
            for key in ("document_id", "object_id", "claim_id", "relation_id", "record_id", "id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate:
                    return candidate
            return None
        for key in ("object_id", "claim_id", "relation_id", "record_id", "id"):
            candidate = getattr(value, key, None)
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    @staticmethod
    def _record_status(value: Any) -> str:
        if isinstance(value, Mapping):
            return str(value.get("status", "APPROVED")).upper()
        return str(getattr(value, "status", "APPROVED")).upper()

    def _injected_records(self, snapshot: KnowledgeVersion) -> tuple[Mapping[str, Any], ...]:
        values = self.records
        if values is None:
            return ()
        if isinstance(values, Mapping):
            values = tuple(values.values())
        allowed = set(snapshot.object_refs) | set(snapshot.claim_refs) | set(snapshot.relation_refs)
        result: list[Mapping[str, Any]] = []
        for value in values:
            record_id = self._record_id(value)
            if record_id is None or record_id not in allowed:
                continue
            if self._record_status(value) not in {"APPROVED", "ASSERTED"}:
                continue
            if isinstance(value, Mapping):
                item = dict(value)
            else:
                item = value.model_dump(mode="json") if hasattr(value, "model_dump") else {"record_id": record_id}
            item.setdefault("document_id", record_id)
            item.setdefault("knowledge_version", snapshot.knowledge_version)
            item.setdefault("version_hash", snapshot.version_hash)
            item.setdefault("source_refs", snapshot.source_snapshot_refs)
            result.append(item)
        return tuple(result)

    def _context_records(self, snapshot: KnowledgeVersion) -> tuple[Mapping[str, Any], ...]:
        view = self._context_view
        if view is None:
            return ()
        result: list[Mapping[str, Any]] = []
        for block in view.blocks:
            result.append(
                {
                    "document_id": block.block_id,
                    "text": block.content,
                    "content": block.content,
                    "object_refs": block.object_refs,
                    "type_uri": block.type_uri,
                    "block_kind": block.block_kind,
                    "evidence_refs": tuple(ref.model_dump(mode="json") for ref in block.evidence_refs),
                    "knowledge_version": snapshot.knowledge_version,
                    "version_hash": snapshot.version_hash,
                    "source_refs": snapshot.source_snapshot_refs,
                }
            )
        return tuple(result)

    def _records_for(self, snapshot: KnowledgeVersion) -> tuple[Mapping[str, Any], ...]:
        provider = self.lexical_provider
        context_records = self._context_records(snapshot)
        if context_records:
            return context_records
        injected = self._injected_records(snapshot)
        if injected:
            return injected
        if provider is None:
            refs = (*snapshot.object_refs, *snapshot.claim_refs, *snapshot.relation_refs)
            return tuple(
                {
                    "document_id": ref,
                    "text": ref,
                    "knowledge_version": snapshot.knowledge_version,
                    "version_hash": snapshot.version_hash,
                    "source_refs": snapshot.source_snapshot_refs,
                }
                for ref in refs
            )
        build = getattr(provider, "build", provider)
        if not callable(build):
            raise ProjectionError("lexical provider has no build callable")
        result = build(snapshot)
        if result is None:
            return ()
        if isinstance(result, Mapping):
            result = result.values()
        try:
            records = tuple(result)
        except TypeError as exc:
            raise ProjectionError("lexical provider must return a collection") from exc
        if any(not isinstance(item, Mapping) for item in records):
            raise ProjectionError("lexical provider records must be mappings")
        return tuple(dict(item) for item in records)

    def build(self, snapshot: KnowledgeVersion) -> ProjectionManifest:
        if snapshot.status.upper() != "APPROVED":
            raise ProjectionError("projection requires an approved KnowledgeVersion", ErrorCode.APPROVAL_REQUIRED.value)
        records = self._records_for(snapshot)
        self._records[(snapshot.work_id, snapshot.knowledge_version)] = records
        return _manifest(snapshot, self, records)

    def drop(self, manifest: ProjectionManifest) -> None:
        self._records.pop((manifest.work_id, manifest.knowledge_version), None)

    def health(self, manifest: ProjectionManifest | None) -> ProjectionHealth:
        if manifest is None:
            return ProjectionHealth(
                projection_id=self.projection_id,
                projection_version=self.projection_version,
                status=ProjectionStatus.DROPPED.value,
            )
        return ProjectionHealth(
            projection_id=manifest.projection_id,
            projection_version=manifest.projection_version,
            status=manifest.status,
            ready=manifest.status == ProjectionStatus.BUILT.value,
            stale=manifest.status == ProjectionStatus.STALE.value,
            knowledge_version=manifest.knowledge_version,
            projection_hash=manifest.projection_hash,
            error_code=manifest.error_code,
            error_message=manifest.error_message,
            dead_letter=manifest.dead_letter,
            readiness=manifest.readiness,
            record_count=manifest.record_count,
            duration_ms=manifest.duration_ms,
        )

    def search(
        self,
        query: str,
        *,
        work_id: str | None = None,
        knowledge_version: str | None = None,
        limit: int = 10,
    ) -> tuple[Mapping[str, Any], ...]:
        """Search the in-memory lexical projection without becoming an authority source."""

        if not isinstance(query, str) or not query.strip() or limit <= 0:
            return ()
        terms = tuple(dict.fromkeys(item.casefold() for item in query.split() if item.strip()))
        candidates: list[tuple[int, Mapping[str, Any]]] = []
        for (record_work, record_version), records in self._records.items():
            if work_id is not None and record_work != work_id:
                continue
            if knowledge_version is not None and record_version != knowledge_version:
                continue
            for record in records:
                text = str(record.get("text", record.get("content", ""))).casefold()
                score = sum(text.count(term) for term in terms)
                if score:
                    candidates.append((score, record))
        candidates.sort(key=lambda item: (-item[0], str(item[1].get("document_id", ""))))
        return tuple(record for _, record in candidates[:limit])


FtsProjection = LexicalProjectionProvider
FTSProjection = LexicalProjectionProvider


class ProjectionRunner:
    """Run, drop, and rebuild providers without mutating authority objects."""

    def __init__(
        self,
        providers: Mapping[str, ProjectionProvider] | Sequence[ProjectionProvider] = (),
        knowledge_versions: Mapping[str, KnowledgeVersion] | Sequence[KnowledgeVersion] = (),
        *,
        context_views: Mapping[str, ContextView] | Sequence[ContextView] = (),
        max_retries: int = 2,
        retry_delay: float = 0.0,
    ) -> None:
        if isinstance(max_retries, bool) or not 0 <= max_retries <= 8:
            raise ValueError("max_retries must be between 0 and 8")
        if isinstance(retry_delay, bool) or retry_delay < 0:
            raise ValueError("retry_delay must be non-negative")
        self.providers: dict[str, ProjectionProvider] = (
            dict(providers) if isinstance(providers, Mapping) else {p.projection_id: p for p in providers}
        )
        self.knowledge_versions = _by_key(knowledge_versions, "knowledge_version")
        self.context_views = _by_key(context_views, "view_id")
        self.max_retries = max_retries
        self.retry_delay = float(retry_delay)
        self.manifests: dict[tuple[str, str], ProjectionManifest] = {}
        self.dead_letters: list[ProjectionManifest] = []
        self.readiness: dict[str, ProjectionHealth] = {}

    def register(self, provider: ProjectionProvider) -> None:
        self.providers[provider.projection_id] = provider

    def _resolve_version(self, value: str | KnowledgeVersion) -> KnowledgeVersion | None:
        if isinstance(value, KnowledgeVersion):
            return value
        return self.knowledge_versions.get(str(value))

    def _resolve_context(self, snapshot: KnowledgeVersion) -> ContextView | None:
        view = self.context_views.get(snapshot.knowledge_version)
        if view is None:
            view = self.context_views.get(f"{snapshot.work_id}:{snapshot.branch_id}")
        if view is None:
            return None
        if (view.work_id, view.branch_id) != (snapshot.work_id, snapshot.branch_id):
            raise ProjectionError("ContextView is outside KnowledgeVersion scope", ErrorCode.INVALID_SCOPE.value)
        return view

    @staticmethod
    def _error_text(exc: Exception) -> tuple[str, str]:
        code = getattr(exc, "code", ErrorCode.PROJECTION_FAILED.value)
        message = str(exc).strip() or type(exc).__name__
        return str(code), message[:500]

    @staticmethod
    def _retryable(code: str) -> bool:
        return code not in {
            ErrorCode.CAPABILITY_UNSUPPORTED.value,
            ErrorCode.APPROVAL_REQUIRED.value,
            ErrorCode.INVALID_SCHEMA.value,
            ErrorCode.INVALID_SCOPE.value,
            ErrorCode.NO_EVIDENCE.value,
            ErrorCode.NOT_FOUND.value,
        }

    @staticmethod
    def _validate_result(
        result: ProjectionManifest,
        snapshot: KnowledgeVersion,
        provider: ProjectionProvider,
        context_view: ContextView | None,
    ) -> ProjectionManifest:
        expected = {
            "projection_id": provider.projection_id,
            "projection_version": provider.projection_version,
            "work_id": snapshot.work_id,
            "branch_id": snapshot.branch_id,
            "knowledge_version": snapshot.knowledge_version,
            "knowledge_version_hash": snapshot.version_hash,
            "source_refs": snapshot.source_snapshot_refs,
            "scope_hash": _scope_hash(snapshot),
        }
        for field, value in expected.items():
            if getattr(result, field) != value:
                raise ProjectionError(
                    f"projection manifest binding mismatch: {field}",
                    ErrorCode.INVALID_SCOPE.value if field in {"work_id", "branch_id", "scope_hash"} else ErrorCode.PROJECTION_FAILED.value,
                )
        if context_view is not None:
            if result.context_view_id is not None and result.context_view_id != context_view.view_id:
                raise ProjectionError("projection manifest context view mismatch", ErrorCode.INVALID_SCOPE.value)
            if result.context_view_hash is not None and result.context_view_hash != context_view.view_hash:
                raise ProjectionError("projection manifest context hash mismatch", ErrorCode.INVALID_SCOPE.value)
        if result.status == ProjectionStatus.FAILED.value and not result.error_code:
            return result.model_copy(update={"error_code": ErrorCode.PROJECTION_FAILED.value})
        return result

    def run(self, projection_id: str, knowledge_version: str | KnowledgeVersion) -> ProjectionManifest:
        provider = self.providers.get(projection_id)
        if provider is None:
            manifest = ProjectionManifest.failed(
                self._resolve_version(knowledge_version),
                projection_id=projection_id,
                projection_version="unknown",
                code=ErrorCode.NOT_FOUND.value,
                message=f"unknown projection provider: {projection_id}",
                dead_letter=True,
            )
            self._remember(manifest)
            return manifest
        snapshot = self._resolve_version(knowledge_version)
        if snapshot is None:
            manifest = ProjectionManifest.failed(
                None,
                projection_id=provider.projection_id,
                projection_version=provider.projection_version,
                code=ErrorCode.NOT_FOUND.value,
                message=f"unknown KnowledgeVersion: {knowledge_version}",
                dead_letter=True,
            )
            self._remember(manifest)
            return manifest
        context_view: ContextView | None = None
        try:
            if snapshot.status.upper() != "APPROVED":
                raise ProjectionError("projection requires an approved KnowledgeVersion", ErrorCode.APPROVAL_REQUIRED.value)
            context_view = self._resolve_context(snapshot)
            if getattr(provider, "requires_context_view", False) and context_view is None:
                raise ProjectionError("projection requires an approved ContextView", ErrorCode.MISSING_CONTEXT.value)
            bind = getattr(provider, "bind_context_view", None)
            if callable(bind):
                bind(context_view)
        except Exception as exc:
            code, message = self._error_text(exc)
            manifest = ProjectionManifest.failed(
                snapshot,
                projection_id=provider.projection_id,
                projection_version=provider.projection_version,
                code=code,
                message=message,
                context_view=context_view,
                dead_letter=True,
            )
            self._remember(manifest)
            return manifest

        started = time.perf_counter()
        last_code = ErrorCode.PROJECTION_FAILED.value
        last_message = "projection build failed"
        for attempt in range(self.max_retries + 1):
            try:
                result = provider.build(snapshot)
                if not isinstance(result, ProjectionManifest):
                    result = ProjectionManifest.model_validate(result)
                result = self._validate_result(result, snapshot, provider, context_view)
                if result.status == ProjectionStatus.FAILED.value:
                    code = result.error_code or ErrorCode.PROJECTION_FAILED.value
                    if attempt < self.max_retries and self._retryable(code):
                        last_code = code
                        last_message = result.error_message or "projection build failed"
                        continue
                    result = result.model_copy(update={
                        "retry_count": attempt,
                        "dead_letter": result.dead_letter or attempt >= self.max_retries or not self._retryable(code),
                        "readiness": "DEAD_LETTER" if result.dead_letter or attempt >= self.max_retries or not self._retryable(code) else "NOT_READY",
                    })
                else:
                    result = result.model_copy(
                        update={
                            "projection_id": provider.projection_id,
                            "projection_version": provider.projection_version,
                            "retry_count": attempt,
                            "duration_ms": max(0.0, (time.perf_counter() - started) * 1000),
                        }
                    )
                self._remember(result)
                return result
            except Exception as exc:
                last_code, last_message = self._error_text(exc)
                if attempt >= self.max_retries or not self._retryable(last_code):
                    break
                if self.retry_delay:
                    time.sleep(self.retry_delay)
        manifest = ProjectionManifest.failed(
            snapshot,
            projection_id=provider.projection_id,
            projection_version=provider.projection_version,
            code=last_code,
            message=last_message,
            retry_count=self.max_retries,
            dead_letter=True,
            duration_ms=(time.perf_counter() - started) * 1000,
            context_view=context_view,
        )
        self._remember(manifest)
        return manifest

    def _remember(self, manifest: ProjectionManifest) -> None:
        key = (manifest.projection_id, manifest.knowledge_version)
        self.manifests[key] = manifest
        provider = self.providers.get(manifest.projection_id)
        if provider is not None:
            self.readiness[manifest.projection_id] = provider.health(manifest)
        if manifest.dead_letter and manifest not in self.dead_letters:
            self.dead_letters.append(manifest)

    def drop(self, projection_id: str, knowledge_version: str | KnowledgeVersion) -> ProjectionManifest | None:
        snapshot = self._resolve_version(knowledge_version)
        version = snapshot.knowledge_version if snapshot is not None else str(knowledge_version)
        manifest = self.manifests.get((projection_id, version))
        provider = self.providers.get(projection_id)
        if manifest is None or provider is None:
            return manifest
        provider.drop(manifest)
        dropped = manifest.model_copy(
            update={"status": ProjectionStatus.DROPPED.value, "readiness": "NOT_READY", "records": ()}
        )
        self._remember(dropped)
        return dropped

    def rebuild(self, projection_id: str, knowledge_version: str | KnowledgeVersion) -> ProjectionManifest:
        self.drop(projection_id, knowledge_version)
        return self.run(projection_id, knowledge_version)

    def mark_stale(self, projection_id: str, knowledge_version: str | KnowledgeVersion) -> ProjectionManifest | None:
        snapshot = self._resolve_version(knowledge_version)
        version = snapshot.knowledge_version if snapshot is not None else str(knowledge_version)
        manifest = self.manifests.get((projection_id, version))
        if manifest is None:
            return None
        stale = manifest.model_copy(update={
            "status": ProjectionStatus.STALE.value,
            "readiness": "NOT_READY",
        })
        self._remember(stale)
        return stale

    invalidate = mark_stale

    def health(self, projection_id: str, knowledge_version: str | KnowledgeVersion | None = None) -> ProjectionHealth:
        if knowledge_version is None:
            return self.readiness.get(
                projection_id,
                ProjectionHealth(projection_id=projection_id, projection_version="unknown", status=ProjectionStatus.DROPPED.value),
            )
        snapshot = self._resolve_version(knowledge_version)
        version = snapshot.knowledge_version if snapshot is not None else str(knowledge_version)
        manifest = self.manifests.get((projection_id, version))
        provider = self.providers.get(projection_id)
        if provider is None:
            return ProjectionHealth(projection_id=projection_id, projection_version="unknown", status=ProjectionStatus.FAILED.value)
        return provider.health(manifest)


__all__ = [
    "FTSProjection",
    "FtsProjection",
    "LexicalProjectionProvider",
    "ProjectionRunner",
]
