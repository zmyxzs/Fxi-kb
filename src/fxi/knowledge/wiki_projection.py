"""Approved-only, disposable Wiki projection.

Pages are generated from the same generic records as the graph projection.
They are presentation artifacts: an annotation can only become a candidate
envelope and can never mutate a page or an authority object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import Field, field_validator, model_validator

from fxi.core.exceptions import FxiError

from .conflict_sets import ConflictSet
from .contracts import (
    Actor,
    CandidateEnvelope,
    ContextView,
    ContractModel,
    ErrorCode,
    EvidenceRef,
    SCHEMA_VERSION,
    _hash,
    _non_empty,
    _stable_hash,
    _token,
)
from .objects import KnowledgeVersion
from .oag_projection import _context_records, _record_for
from .projection_contracts import ProjectionHealth, ProjectionManifest, ProjectionStatus
from .projection_runner import ProjectionProvider, _manifest
from .wiki_renderer import WikiRenderer


class WikiProjectionError(FxiError):
    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value) -> None:
        super().__init__(message, code=code)


class WikiPage(ContractModel):
    page_id: str
    object_ref: str
    work_id: str
    branch_id: str
    type_uri: str
    knowledge_version: str
    knowledge_version_hash: str
    evidence_refs: tuple[Mapping[str, Any], ...] = ()
    decision_ref: str | None = None
    conflict_refs: tuple[str, ...] = ()
    staleness: str = "FRESH"
    status: str = "APPROVED"
    content: Mapping[str, Any] = Field(default_factory=dict)
    body: str
    page_hash: str = ""

    @field_validator(
        "page_id",
        "object_ref",
        "work_id",
        "branch_id",
        "type_uri",
        "knowledge_version",
    )
    @classmethod
    def validate_page_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("knowledge_version_hash", "page_hash")
    @classmethod
    def validate_page_hashes(cls, value: str, info: Any) -> str:
        return _hash(value, info.field_name)

    @field_validator("decision_ref")
    @classmethod
    def validate_decision_ref(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "decision_ref")

    @field_validator("conflict_refs")
    @classmethod
    def validate_conflict_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "conflict_ref") for item in value)

    @field_validator("staleness", "status")
    @classmethod
    def validate_page_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_page_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {
            key: result.get(key)
            for key in (
                "page_id",
                "object_ref",
                "work_id",
                "branch_id",
                "type_uri",
                "knowledge_version",
                "knowledge_version_hash",
                "evidence_refs",
                "decision_ref",
                "conflict_refs",
                "staleness",
                "status",
                "content",
                "body",
            )
        }
        expected = _stable_hash(payload)
        supplied = result.get("page_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("page_hash does not match canonical Wiki page")
        result["page_hash"] = expected
        return result


class WikiProjectionProvider:
    projection_id = "wiki"
    projection_version = "v3-wiki-1"

    def __init__(
        self,
        records: Mapping[str, Any] | Sequence[Any] = (),
        *,
        conflicts: Sequence[ConflictSet] = (),
        renderer: WikiRenderer | None = None,
    ) -> None:
        if isinstance(records, Mapping):
            records = tuple(records.values())
        self.records = tuple(records)
        self.conflicts = tuple(conflicts)
        self.renderer = renderer or WikiRenderer()
        self._context_view: ContextView | None = None
        self._pages: dict[tuple[str, str, str], WikiPage] = {}

    def bind_context_view(self, context_view: ContextView | None) -> None:
        self._context_view = context_view

    def _records_for(self, snapshot: KnowledgeVersion) -> tuple[Mapping[str, Any], ...]:
        if self._context_view is not None:
            if (self._context_view.work_id, self._context_view.branch_id) != (snapshot.work_id, snapshot.branch_id):
                raise WikiProjectionError("ContextView is outside KnowledgeVersion scope", ErrorCode.INVALID_SCOPE.value)
            selected = list(_context_records(self._context_view, snapshot))
            selected_ids = {str(item.get("record_id")) for item in selected}
            for conflict in self.conflicts:
                if set(conflict.claim_refs).intersection(selected_ids):
                    item = _record_for(conflict, snapshot)
                    if item is not None:
                        selected.append(item)
            return tuple(selected)
        selected: list[Mapping[str, Any]] = []
        for value in (*self.records, *self.conflicts):
            item = _record_for(value, snapshot, include_objects=True)
            if item is not None:
                selected.append(item)
        selected.sort(key=lambda item: (str(item.get("kind", "")), str(item.get("record_id", ""))))
        return tuple(selected)

    @staticmethod
    def _conflicts_by_claim(records: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
        result: dict[str, list[str]] = {}
        for record in records:
            if record.get("kind") != "CONFLICT":
                continue
            conflict_id = str(record.get("record_id", ""))
            for claim_ref in record.get("claim_refs", ()):
                result.setdefault(str(claim_ref), []).append(conflict_id)
        return {key: tuple(sorted(set(value))) for key, value in result.items()}

    def build(self, snapshot: KnowledgeVersion) -> ProjectionManifest:
        if snapshot.status.upper() != "APPROVED":
            raise WikiProjectionError("projection requires an approved KnowledgeVersion", ErrorCode.APPROVAL_REQUIRED.value)
        records = self._records_for(snapshot)
        conflicts_by_claim = self._conflicts_by_claim(records)
        pages: list[WikiPage] = []
        for record in records:
            object_ref = str(record.get("record_id", ""))
            if not object_ref:
                continue
            kind = str(record.get("kind", "RECORD"))
            type_uri = str(record.get("type_uri", "knowledge.conflict@1" if kind == "CONFLICT" else "knowledge.record@1"))
            if kind == "CONFLICT":
                conflict_refs = (object_ref,)
                decision_ref = record.get("decision_hash")
            else:
                conflict_refs = conflicts_by_claim.get(object_ref, ())
                decision_ref = None
            render_record = {
                **record,
                "object_ref": object_ref,
                "type_uri": type_uri,
                "work_id": snapshot.work_id,
                "branch_id": snapshot.branch_id,
                "version_hash": snapshot.version_hash,
                "conflict_refs": conflict_refs,
                "decision": decision_ref,
            }
            body = self.renderer.render(
                render_record,
                knowledge_version=snapshot.knowledge_version,
                knowledge_version_hash=snapshot.version_hash,
                decision=str(decision_ref) if decision_ref else None,
                conflicts=conflict_refs,
                staleness=self._context_view.staleness if self._context_view is not None else "FRESH",
            )
            page_id = "wiki-" + _stable_hash(
                {"work_id": snapshot.work_id, "branch_id": snapshot.branch_id, "object_ref": object_ref}
            )[:32]
            page = WikiPage(
                page_id=page_id,
                object_ref=object_ref,
                work_id=snapshot.work_id,
                branch_id=snapshot.branch_id,
                type_uri=type_uri,
                knowledge_version=snapshot.knowledge_version,
                knowledge_version_hash=snapshot.version_hash,
                evidence_refs=tuple(record.get("evidence_refs", ())),
                decision_ref=decision_ref,
                conflict_refs=conflict_refs,
                staleness=self._context_view.staleness if self._context_view is not None else "FRESH",
                status="APPROVED",
                content={"kind": kind, "record": record},
                body=body,
            )
            pages.append(page)
            self._pages[(snapshot.work_id, snapshot.knowledge_version, page_id)] = page
        return _manifest(
            snapshot,
            self,
            tuple(page.model_dump(mode="json") for page in pages),
            context_view=self._context_view,
        )

    def drop(self, manifest: ProjectionManifest) -> None:
        prefix = (manifest.work_id, manifest.knowledge_version)
        for key in tuple(self._pages):
            if key[:2] == prefix:
                self._pages.pop(key, None)

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

    def _resolve_page(self, page_id: str, work_id: str | None, knowledge_version: str | None) -> WikiPage:
        page_id = _token(page_id, "page_id")
        matches = [
            page
            for (page_work, page_version, page_key), page in self._pages.items()
            if page_key == page_id
            and (work_id is None or page_work == work_id)
            and (knowledge_version is None or page_version == knowledge_version)
        ]
        if len(matches) != 1:
            raise WikiProjectionError("Wiki page not found in the requested scope", ErrorCode.NOT_FOUND.value)
        return matches[0].model_copy(deep=True)

    def get_page(
        self,
        page_id: str,
        *,
        work_id: str | None = None,
        knowledge_version: str | None = None,
    ) -> WikiPage:
        return self._resolve_page(page_id, work_id, knowledge_version)

    @property
    def pages(self) -> tuple[WikiPage, ...]:
        return tuple(page.model_copy(deep=True) for page in self._pages.values())

    def annotate(
        self,
        page_id: str,
        annotation: Mapping[str, Any],
        *,
        actor: Actor | Mapping[str, Any],
        candidate_id: str | None = None,
        extractor_id: str = "wiki-annotation",
        model_route: str | None = None,
    ) -> CandidateEnvelope:
        if not isinstance(annotation, Mapping):
            raise WikiProjectionError("Wiki annotation must be a mapping", ErrorCode.INVALID_SCHEMA.value)
        page = self._resolve_page(page_id, None, None)
        actor_model = actor if isinstance(actor, Actor) else Actor.model_validate(actor)
        if actor_model.scope is None:
            raise WikiProjectionError("annotation actor requires explicit scope", ErrorCode.INVALID_SCOPE.value)
        if (actor_model.scope.work_id, actor_model.scope.branch_id) != (page.work_id, page.branch_id):
            raise WikiProjectionError("annotation actor is outside page scope", ErrorCode.INVALID_SCOPE.value)
        evidence = tuple(EvidenceRef.model_validate(ref) for ref in page.evidence_refs)
        if not evidence or not evidence[0].source_snapshot_ref:
            raise WikiProjectionError("Wiki annotation requires source evidence", ErrorCode.NO_EVIDENCE.value)
        normalized_id = candidate_id or "candidate-" + _stable_hash(
            {"page_id": page.page_id, "annotation": annotation, "actor": actor_model.actor_id}
        )[:32]
        payload = {"page_ref": page.page_id, "page_hash": page.page_hash, "annotation": dict(annotation)}
        return CandidateEnvelope(
            candidate_id=normalized_id,
            artifact_kind="wiki.annotation",
            status="CANDIDATE",
            work_id=page.work_id,
            branch_id=page.branch_id,
            source_snapshot_ref=evidence[0].source_snapshot_ref,
            evidence_refs=evidence,
            input_hash=_stable_hash({"page_hash": page.page_hash, "body": page.body}),
            extractor_id=extractor_id,
            schema_version=SCHEMA_VERSION,
            domain_package_version="projection-wiki.v3",
            model_route=model_route,
            policy_hash=_stable_hash({"artifact_kind": "wiki.annotation", "read_only": True}),
            payload=payload,
            actor=actor_model,
        )

    def search(self, text: str, *, work_id: str | None = None) -> tuple[WikiPage, ...]:
        if not isinstance(text, str) or not text.strip():
            return ()
        needle = text.casefold()
        result = [page for page in self._pages.values() if (work_id is None or page.work_id == work_id) and needle in page.body.casefold()]
        result.sort(key=lambda page: page.page_id)
        return tuple(page.model_copy(deep=True) for page in result)


WikiProvider = WikiProjectionProvider
LLMWikiProjectionProvider = WikiProjectionProvider


__all__ = [
    "LLMWikiProjectionProvider",
    "WikiPage",
    "WikiProjectionError",
    "WikiProjectionProvider",
    "WikiProvider",
]
