"""A small, disposable, domain-neutral graph projection.

The graph is deliberately a read model.  It accepts already validated kernel
records, copies only records referenced by an approved ``KnowledgeVersion``
or an approved ``ContextView``, and never exposes a write path for facts.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from fxi.core.exceptions import FxiError

from .as_of import AsOfError, compare_as_of
from .conflict_sets import ConflictSet
from .contracts import (
    ContextView,
    ContractModel,
    ErrorCode,
    _hash,
    _non_empty,
    _stable_hash,
    _token,
)
from .objects import Claim, KnowledgeObject, KnowledgeVersion, Relation
from .projection_runner import ProjectionProvider, _manifest
from .projection_contracts import ProjectionHealth, ProjectionManifest, ProjectionStatus


class OAGQueryError(FxiError):
    """A fail-closed graph query error with a public v3 error code."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value) -> None:
        super().__init__(message, code=code)


class GraphQuery(ContractModel):
    """Portable query metadata; graph semantics remain generic."""

    start_ref: str
    target_ref: str | None = None
    work_id: str | None = None
    branch_id: str | None = None
    knowledge_version: str | None = None
    as_of: int | str | None = None
    mode: Literal["path", "neighbors", "conflicts"] = "path"
    relation_types: tuple[str, ...] = ()
    include_conflicts: bool = False
    max_hops: int = Field(default=4, gt=0, le=16)

    @field_validator("start_ref", "target_ref", "work_id", "branch_id", "knowledge_version")
    @classmethod
    def validate_query_refs(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @field_validator("relation_types")
    @classmethod
    def validate_relation_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item, "relation_type") for item in value)

    @field_validator("as_of")
    @classmethod
    def validate_query_as_of(cls, value: int | str | None) -> int | str | None:
        if value is None:
            return None
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("as_of must be a non-negative integer or non-empty string")
        if isinstance(value, str) and not value.strip():
            raise ValueError("as_of cannot be empty")
        return value


class GraphQueryResult(ContractModel):
    query_hash: str
    work_id: str
    branch_id: str
    knowledge_version: str
    status: str = "OK"
    code: str = "OK"
    result_refs: tuple[str, ...] = ()
    paths: tuple[tuple[str, ...], ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence_refs: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[str, ...] = ()
    result_hash: str = ""

    @field_validator("query_hash")
    @classmethod
    def validate_query_hash(cls, value: str) -> str:
        return _hash(value, "query_hash")

    @field_validator("work_id", "branch_id", "knowledge_version")
    @classmethod
    def validate_result_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("status", "code")
    @classmethod
    def validate_result_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_result_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {
            key: result.get(key)
            for key in (
                "query_hash",
                "work_id",
                "branch_id",
                "knowledge_version",
                "status",
                "code",
                "result_refs",
                "paths",
                "conflicts",
                "evidence_refs",
                "diagnostics",
            )
        }
        expected = _stable_hash(payload)
        supplied = result.get("result_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("result_hash does not match canonical graph result")
        result["result_hash"] = expected
        return result


OAGQuery = GraphQuery
OAGQueryResult = GraphQueryResult


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _record_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("record_id", "object_id", "claim_id", "relation_id", "conflict_id", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        return None
    for key in ("object_id", "claim_id", "relation_id", "conflict_id", "id"):
        candidate = getattr(value, key, None)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _scope(value: Any) -> Mapping[str, Any] | None:
    raw = value.get("scope") if isinstance(value, Mapping) else getattr(value, "scope", None)
    if raw is None:
        return None
    return _dump(raw)


def _scope_pair(value: Any) -> tuple[str, str] | None:
    raw = _scope(value)
    if not isinstance(raw, Mapping):
        return None
    work_id, branch_id = raw.get("work_id"), raw.get("branch_id")
    if isinstance(work_id, str) and isinstance(branch_id, str):
        return work_id, branch_id
    return None


def _evidence(value: Any) -> tuple[Mapping[str, Any], ...]:
    raw = value.get("evidence_refs", ()) if isinstance(value, Mapping) else getattr(value, "evidence_refs", ())
    result: list[Mapping[str, Any]] = []
    for ref in raw or ():
        dumped = _dump(ref)
        if isinstance(dumped, Mapping):
            result.append(dict(dumped))
    return tuple(result)


def _is_domain_rule(value: KnowledgeObject) -> bool:
    type_root = value.type_uri.split("@", 1)[0].rsplit(".", 1)[-1].casefold()
    payload = value.payload
    marker = payload.get("kind", payload.get("record_kind", "")) if isinstance(payload, Mapping) else ""
    return type_root in {"rule", "domain_rule"} or str(marker).casefold() == "domain_rule"


def _record_for(
    value: Any,
    snapshot: KnowledgeVersion,
    *,
    include_objects: bool = False,
) -> Mapping[str, Any] | None:
    """Convert a validated authority record to generic graph data."""

    record_id = _record_id(value)
    if record_id is None:
        return None
    scope_pair = _scope_pair(value)
    if scope_pair != (snapshot.work_id, snapshot.branch_id):
        return None
    allowed = set(snapshot.object_refs) | set(snapshot.claim_refs) | set(snapshot.relation_refs)
    if isinstance(value, ConflictSet):
        if not set(value.claim_refs).intersection(snapshot.claim_refs):
            return None
        return {
            "record_id": record_id,
            "kind": "CONFLICT",
            "claim_refs": value.claim_refs,
            "scope": _dump(value.scope),
            "status": value.status,
            "conflict_type": value.conflict_type,
            "conflict_hash": value.conflict_hash,
            "decision_hash": value.decision_hash,
            "evidence_refs": _evidence(value),
        }
    if record_id not in allowed:
        return None
    status = str(getattr(value, "status", "")).upper()
    if isinstance(value, KnowledgeObject):
        if status != "APPROVED" or (not include_objects and not _is_domain_rule(value)):
            return None
        return {
            "record_id": value.object_id,
            "kind": "DOMAIN_RULE" if _is_domain_rule(value) else "OBJECT",
            "type_uri": value.type_uri,
            "payload": _dump(value.payload),
            "scope": _dump(value.scope),
            "status": status,
            "evidence_refs": _evidence(value),
        }
    if isinstance(value, Claim):
        if status not in {"ASSERTED", "APPROVED"}:
            return None
        target = value.value if isinstance(value.value, str) and value.value else None
        return {
            "record_id": value.claim_id,
            "kind": "CLAIM",
            "subject_ref": value.subject_ref,
            "target_ref": target,
            "predicate": value.predicate,
            "value": _dump(value.value),
            "type_uri": value.object_type_uri,
            "scope": _dump(value.scope),
            "status": status,
            "claim_hash": value.claim_hash,
            "evidence_refs": _evidence(value),
        }
    if isinstance(value, Relation):
        if status not in {"ASSERTED", "APPROVED"}:
            return None
        return {
            "record_id": value.relation_id,
            "kind": "RELATION",
            "subject_ref": value.subject_ref,
            "target_ref": value.object_ref,
            "relation_type": value.relation_type,
            "scope": _dump(value.scope),
            "status": status,
            "relation_hash": value.relation_hash,
            "evidence_refs": _evidence(value),
        }
    return None


def _context_records(view: ContextView, snapshot: KnowledgeVersion) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for block in view.blocks:
        content = block.content if isinstance(block.content, Mapping) else {}
        record_id = next(
            (
                content.get(key)
                for key in ("claim_id", "relation_id", "object_id", "record_id")
                if isinstance(content.get(key), str)
            ),
            block.object_refs[0] if block.object_refs else block.block_id,
        )
        kind = "CLAIM" if "claim_id" in content else "RELATION" if "relation_id" in content else "CONTEXT"
        record: dict[str, Any] = {
            "record_id": record_id,
            "kind": kind,
            "type_uri": block.type_uri,
            "object_refs": block.object_refs,
            "content": _dump(block.content),
            "scope": _dump(block.scope),
            "status": "APPROVED",
            "evidence_refs": _evidence({"evidence_refs": block.evidence_refs}),
            "knowledge_version": snapshot.knowledge_version,
        }
        if kind == "CLAIM":
            record.update(
                {
                    "subject_ref": content.get("subject_ref", block.object_refs[0] if block.object_refs else record_id),
                    "target_ref": content.get("value") if isinstance(content.get("value"), str) else None,
                    "predicate": content.get("predicate", "claim"),
                }
            )
        elif kind == "RELATION":
            record.update(
                {
                    "subject_ref": content.get("subject_ref", block.object_refs[0] if block.object_refs else record_id),
                    "target_ref": content.get("object_ref", block.object_refs[1] if len(block.object_refs) > 1 else None),
                    "relation_type": content.get("relation_type", "relation"),
                }
            )
        result.append(record)
    return tuple(result)


class OAGLiteProjectionProvider:
    """Projection provider for generic claims, relations, rules and conflicts."""

    projection_id = "oag-lite"
    projection_version = "v3-oag-lite-1"

    def __init__(
        self,
        records: Mapping[str, Any] | Sequence[Any] = (),
        *,
        conflicts: Sequence[ConflictSet] = (),
    ) -> None:
        if isinstance(records, Mapping):
            records = tuple(records.values())
        self.records = tuple(records)
        self.conflicts = tuple(conflicts)
        self._context_view: ContextView | None = None
        self._graphs: dict[tuple[str, str], tuple[Mapping[str, Any], ...]] = {}

    def bind_context_view(self, context_view: ContextView | None) -> None:
        self._context_view = context_view

    def _records_for(self, snapshot: KnowledgeVersion) -> tuple[Mapping[str, Any], ...]:
        if self._context_view is not None:
            if (self._context_view.work_id, self._context_view.branch_id) != (snapshot.work_id, snapshot.branch_id):
                raise OAGQueryError("ContextView is outside KnowledgeVersion scope", ErrorCode.INVALID_SCOPE.value)
            selected = list(_context_records(self._context_view, snapshot))
            allowed_ids = {item["record_id"] for item in selected}
            for conflict in self.conflicts:
                if set(conflict.claim_refs).intersection(allowed_ids):
                    selected.append(_record_for(conflict, snapshot) or {})
            return tuple(item for item in selected if item)
        selected: list[Mapping[str, Any]] = []
        for value in (*self.records, *self.conflicts):
            item = _record_for(value, snapshot)
            if item is not None:
                selected.append(item)
        selected.sort(key=lambda item: (str(item.get("kind", "")), str(item.get("record_id", ""))))
        return tuple(selected)

    def build(self, snapshot: KnowledgeVersion) -> ProjectionManifest:
        if snapshot.status.upper() != "APPROVED":
            raise OAGQueryError("projection requires an approved KnowledgeVersion", ErrorCode.APPROVAL_REQUIRED.value)
        records = self._records_for(snapshot)
        self._graphs[(snapshot.work_id, snapshot.knowledge_version)] = records
        return _manifest(snapshot, self, records, context_view=self._context_view)

    def drop(self, manifest: ProjectionManifest) -> None:
        self._graphs.pop((manifest.work_id, manifest.knowledge_version), None)

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

    def _graph_records(self, work_id: str, knowledge_version: str) -> tuple[Mapping[str, Any], ...]:
        try:
            return self._graphs[(work_id, knowledge_version)]
        except KeyError as exc:
            raise OAGQueryError("graph projection is stale or not built", ErrorCode.PROJECTION_STALE.value) from exc

    @staticmethod
    def _edge_allowed(record: Mapping[str, Any], query: GraphQuery) -> bool:
        if record.get("kind") not in {"CLAIM", "RELATION"}:
            return False
        if query.relation_types:
            relation_type = str(record.get("relation_type", record.get("predicate", "")))
            if relation_type not in query.relation_types:
                return False
        if query.as_of is not None:
            scope = record.get("scope")
            record_as_of = scope.get("as_of") if isinstance(scope, Mapping) else None
            if record_as_of is not None:
                try:
                    if compare_as_of(record_as_of, query.as_of) > 0:
                        return False
                except AsOfError:
                    return False
        return bool(record.get("subject_ref") and record.get("target_ref"))

    @staticmethod
    def _conflict_ids(records: Sequence[Mapping[str, Any]]) -> tuple[set[str], dict[str, set[str]]]:
        claim_ids: set[str] = set()
        by_claim: dict[str, set[str]] = defaultdict(set)
        for record in records:
            if record.get("kind") != "CONFLICT" or str(record.get("status", "")).upper() == "RESOLVED":
                continue
            conflict_id = str(record.get("record_id", ""))
            for claim_id in record.get("claim_refs", ()):
                claim_ids.add(str(claim_id))
                by_claim[str(claim_id)].add(conflict_id)
        return claim_ids, by_claim

    def query(
        self,
        request: GraphQuery | Mapping[str, Any] | str,
        target_ref: str | None = None,
        *,
        work_id: str | None = None,
        knowledge_version: str | None = None,
        **overrides: Any,
    ) -> GraphQueryResult:
        if isinstance(request, str):
            payload: dict[str, Any] = {"start_ref": request}
            if target_ref is not None:
                payload["target_ref"] = target_ref
        elif isinstance(request, GraphQuery):
            payload = request.model_dump(mode="python")
        elif isinstance(request, Mapping):
            payload = dict(request)
        else:
            raise OAGQueryError("graph query must be a mapping or GraphQuery", ErrorCode.INVALID_SCHEMA.value)
        for key, value in (("work_id", work_id), ("knowledge_version", knowledge_version), *overrides.items()):
            if value is not None:
                payload[key] = value
        query = GraphQuery.model_validate(payload)
        if query.work_id is None or query.knowledge_version is None:
            raise OAGQueryError("graph query requires work_id and knowledge_version", ErrorCode.INVALID_SCOPE.value)
        records = self._graph_records(query.work_id, query.knowledge_version)
        query_hash = _stable_hash(query.model_dump(mode="json"))
        conflict_claims, conflict_by_claim = self._conflict_ids(records)
        conflict_ids = tuple(sorted({item for values in conflict_by_claim.values() for item in values}))
        if query.mode == "conflicts":
            selected = tuple(
                str(record["record_id"])
                for record in records
                if record.get("kind") == "CONFLICT"
                and (query.start_ref in record.get("claim_refs", ()) or query.start_ref == record.get("record_id"))
            )
            return GraphQueryResult(
                query_hash=query_hash,
                work_id=query.work_id,
                branch_id=query.branch_id or self._branch_id(records),
                knowledge_version=query.knowledge_version,
                status="CONFLICTED" if selected else "EMPTY",
                code=ErrorCode.CONFLICTING_ASSERTIONS.value if selected else ErrorCode.NOT_FOUND.value,
                result_refs=selected,
                conflicts=selected,
                evidence_refs=self._evidence_for(selected, records),
            )

        adjacency: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for record in records:
            if not self._edge_allowed(record, query):
                continue
            if not query.include_conflicts and (
                record.get("record_id") in conflict_claims or conflict_by_claim.get(str(record.get("record_id")))
            ):
                continue
            adjacency[str(record["subject_ref"])].append(record)

        paths: list[tuple[str, ...]] = []
        result_refs: set[str] = set()
        queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque([(query.start_ref, (query.start_ref,), ())])
        visited: set[tuple[str, int]] = {(query.start_ref, 0)}
        while queue and len(paths) < 128:
            node, path, edge_refs = queue.popleft()
            if query.target_ref is not None and node == query.target_ref and len(path) > 1:
                paths.append(path)
                result_refs.update(edge_refs)
                continue
            if query.mode == "neighbors" and len(path) > 1:
                paths.append(path)
                result_refs.update(edge_refs)
            if len(edge_refs) >= query.max_hops:
                continue
            for edge in adjacency.get(node, ()):
                target = str(edge["target_ref"])
                state = (target, len(edge_refs) + 1)
                if state in visited or target in path:
                    continue
                visited.add(state)
                queue.append((target, (*path, target), (*edge_refs, str(edge["record_id"]))))

        status = "OK" if paths else "EMPTY"
        code = "OK" if paths else ErrorCode.NOT_FOUND.value
        if not paths and query.include_conflicts and conflict_ids:
            status, code = "CONFLICTED", ErrorCode.CONFLICTING_ASSERTIONS.value
        return GraphQueryResult(
            query_hash=query_hash,
            work_id=query.work_id,
            branch_id=query.branch_id or self._branch_id(records),
            knowledge_version=query.knowledge_version,
            status=status,
            code=code,
            result_refs=tuple(sorted(result_refs)),
            paths=tuple(paths),
            conflicts=conflict_ids if query.include_conflicts else (),
            evidence_refs=self._evidence_for(tuple(sorted(result_refs)), records),
        )

    find_path = query

    @staticmethod
    def _branch_id(records: Sequence[Mapping[str, Any]]) -> str:
        for record in records:
            scope = record.get("scope")
            if isinstance(scope, Mapping) and isinstance(scope.get("branch_id"), str):
                return scope["branch_id"]
        return "branch-unknown"

    @staticmethod
    def _evidence_for(refs: Sequence[str], records: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        wanted = set(refs)
        result: dict[str, Mapping[str, Any]] = {}
        for record in records:
            if str(record.get("record_id")) not in wanted:
                continue
            for evidence in record.get("evidence_refs", ()):
                if isinstance(evidence, Mapping):
                    key = str(evidence.get("evidence_id", _stable_hash(evidence)))
                    result[key] = evidence
        return tuple(result[key] for key in sorted(result))


OagLiteProjectionProvider = OAGLiteProjectionProvider
OAGProjectionProvider = OAGLiteProjectionProvider
OagProjection = OAGLiteProjectionProvider


__all__ = [
    "GraphQuery",
    "GraphQueryResult",
    "OAGLiteProjectionProvider",
    "OAGProjectionProvider",
    "OAGQuery",
    "OAGQueryError",
    "OAGQueryResult",
    "OagLiteProjectionProvider",
    "OagProjection",
]
