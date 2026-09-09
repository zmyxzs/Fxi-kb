"""Read-only compilation of approved knowledge into a canonical ContextView."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import AliasChoices, Field, field_validator

from fxi.core.canonical import canonical_json
from fxi.core.exceptions import FxiError

from .as_of import AsOfError, compare_as_of, validity_contains
from .branch_service import BranchContextRef
from .conflict_sets import ConflictSet
from .contracts import ContextBlock, ContextView, ContractModel, ErrorCode, Scope, _non_empty, _stable_hash, _token
from .objects import Claim, KnowledgeHead, KnowledgeObject, Relation
from .source_graph import SourceCompositeRef
from .context_blocks import ContextBlockError, make_context_block, recompute_block_hash
from .view_selector import ViewSelector


class ContextCompilationError(FxiError):
    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value) -> None:
        super().__init__(message, code=code)


class ContextRequest(ViewSelector):
    """Compilation request; both immutable source/head anchors are explicit."""

    source_composite_ref: SourceCompositeRef = Field(
        validation_alias=AliasChoices("source_composite_ref", "source_composite")
    )
    knowledge_head: KnowledgeHead = Field(
        validation_alias=AliasChoices("knowledge_head", "knowledge_head_ref")
    )
    actor: str = "context-compiler"

    @field_validator("actor")
    @classmethod
    def validate_actor(cls, value: str) -> str:
        return _token(value, "actor")


def _as_model(request: ContextRequest | Mapping[str, Any]) -> ContextRequest:
    if isinstance(request, ContextRequest):
        return request
    try:
        return ContextRequest.model_validate(request)
    except Exception as exc:
        raise ContextCompilationError("invalid context request", ErrorCode.INVALID_SCHEMA.value) from exc


def _read_collection(provider: Any, name: str, fallback: Sequence[Any] = ()) -> tuple[Any, ...]:
    if provider is None:
        return tuple(fallback)
    value = getattr(provider, name, None)
    if value is None and isinstance(provider, Mapping):
        value = provider.get(name)
    if callable(value):
        try:
            value = value()
        except TypeError as exc:
            raise ContextCompilationError(
                f"provider.{name} could not be called",
                ErrorCode.INVALID_SCHEMA.value,
            ) from exc
    if value is None:
        return tuple(fallback)
    if isinstance(value, Mapping):
        value = value.values()
    if isinstance(value, (str, bytes)):
        raise ContextCompilationError(f"provider.{name} must be a collection")
    try:
        return tuple(value)
    except TypeError as exc:
        raise ContextCompilationError(f"provider.{name} must be a collection") from exc


def _call_filter(filter_port: Any, value: Any, request: ContextRequest) -> Any:
    if filter_port is None:
        return value
    if callable(filter_port):
        for args in ((value, request), (value, request.pov_id), (value,)):
            try:
                return filter_port(*args)
            except TypeError:
                continue
    for name in ("filter", "sanitize", "sanitize_context_for_pov"):
        method = getattr(filter_port, name, None)
        if method is None:
            continue
        for args in ((value, request), (value, request.pov_id), (value,)):
            try:
                return method(*args)
            except TypeError:
                continue
    raise ContextCompilationError("POV filter has no supported call shape")


def _capability_set(source: Any, work_id: str) -> set[str] | None:
    if source is None:
        return None
    value = source
    if hasattr(source, "capabilities") and callable(source.capabilities):
        try:
            value = source.capabilities(work_id)
        except TypeError:
            value = source.capabilities()
    if isinstance(value, Mapping):
        names = set(value)
        for key in ("model_capabilities", "projections", "views", "capabilities"):
            nested = value.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                names.update(str(item) for item in nested)
        return names
    for attr in ("model_capabilities", "projections", "views"):
        nested = getattr(value, attr, None)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            if not isinstance(value, set):
                value = set()
            value.update(str(item) for item in nested)
    if isinstance(value, str):
        return {value}
    try:
        return {str(item) for item in value}
    except TypeError:
        return None


class ContextCompiler:
    """Compile a view from injected read-only collections and policy ports."""

    def __init__(
        self,
        provider: Any = None,
        *,
        knowledge_provider: Any = None,
        objects: Sequence[KnowledgeObject] = (),
        claims: Sequence[Claim] = (),
        relations: Sequence[Relation] = (),
        conflicts: Sequence[ConflictSet] = (),
        registry: Any = None,
        evidence_validator: Callable[..., Any] | None = None,
        pov_filter: Any = None,
        token_counter: Callable[[Any], int] | None = None,
        token_estimator: Callable[[Any], int] | None = None,
        capabilities: Any = None,
    ) -> None:
        if provider is not None and knowledge_provider is not None:
            raise ContextCompilationError("only one knowledge provider may be supplied")
        self._provider = provider if provider is not None else knowledge_provider
        self._objects = tuple(objects)
        self._claims = tuple(claims)
        self._relations = tuple(relations)
        self._conflicts = tuple(conflicts)
        self._registry = registry
        self._evidence_validator = evidence_validator
        self._pov_filter = pov_filter
        self._token_counter = token_counter or token_estimator or self._default_tokens
        self._capabilities = capabilities

    @staticmethod
    def _default_tokens(value: Any) -> int:
        if isinstance(value, str):
            return max(1, (len(value) + 3) // 4)
        return max(1, (len(canonical_json(value)) + 3) // 4)

    @staticmethod
    def _in_scope(item: Any, request: ContextRequest) -> bool:
        scope = getattr(item, "scope", None)
        if scope is None or (scope.work_id, scope.branch_id) != (request.work_id, request.branch_id):
            return False
        try:
            return compare_as_of(scope.as_of, request.as_of) <= 0
        except AsOfError:
            return False

    @staticmethod
    def _source_allowed(item: Any, request: ContextRequest) -> bool:
        evidence = tuple(getattr(item, "evidence_refs", ()))
        if not evidence:
            return False
        composite = request.source_composite_ref
        allowed_versions = {
            (part.source_id, part.source_version)
            for part in composite.contributions
        } if composite is not None else None
        for ref in evidence:
            if ref.scope is None:
                return False
            if (ref.scope.work_id, ref.scope.branch_id) != (request.work_id, request.branch_id):
                return False
            if allowed_versions is not None and (ref.source_id, ref.source_version) not in allowed_versions:
                return False
        if request.source_ids and not any(ref.source_id in request.source_ids for ref in evidence):
            return False
        return True

    def _approved_records(self, request: ContextRequest) -> tuple[Any, ...]:
        objects = _read_collection(self._provider, "objects", self._objects)
        claims = _read_collection(self._provider, "claims", self._claims)
        relations = _read_collection(self._provider, "relations", self._relations)
        branch_context = request.branch_context_ref
        filtered_claims = set(branch_context.filtered_claim_refs) if branch_context else set()
        records: list[Any] = []
        for item in (*objects, *claims, *relations):
            if not isinstance(item, (KnowledgeObject, Claim, Relation)):
                continue
            status = getattr(item, "status", "").upper()
            required_status = "APPROVED" if isinstance(item, KnowledgeObject) else "ASSERTED"
            if status != required_status or not self._in_scope(item, request) or not self._source_allowed(item, request):
                continue
            if isinstance(item, KnowledgeObject) and not validity_contains(item.validity, request.as_of):
                continue
            if (
                isinstance(item, KnowledgeObject)
                and item.knowledge_version is not None
                and request.knowledge_head is not None
                and item.knowledge_version != request.knowledge_head.knowledge_version
            ):
                continue
            if isinstance(item, Claim) and item.claim_id in filtered_claims:
                continue
            if self._conflicted(item):
                continue
            records.append(item)
        records.sort(key=lambda item: (self._record_id(item), getattr(item, "type_uri", getattr(item, "object_type_uri", ""))))
        return tuple(records)

    def _conflicted(self, item: Any) -> bool:
        refs = {getattr(item, "claim_id", "")}
        for conflict in (*self._conflicts, *_read_collection(self._provider, "conflicts", ())):
            if not isinstance(conflict, ConflictSet) or conflict.status.upper() == "RESOLVED":
                continue
            if refs.intersection(conflict.claim_refs):
                return True
        return False

    @staticmethod
    def _record_id(item: Any) -> str:
        return getattr(item, "object_id", getattr(item, "claim_id", getattr(item, "relation_id", "")))

    def _render(self, item: Any, request: ContextRequest) -> Any:
        if self._registry is not None:
            try:
                package = self._registry.resolve(
                    getattr(item, "type_uri", getattr(item, "object_type_uri", ""))
                )
            except Exception as exc:
                raise ContextCompilationError("type URI is not registered", ErrorCode.UNREGISTERED_TYPE.value) from exc
            try:
                rendered = package.render_context(item, request.to_view_spec())
            except Exception as exc:
                raise ContextCompilationError("domain package rejected context rendering", ErrorCode.INVALID_SCHEMA.value) from exc
            return rendered
        if isinstance(item, KnowledgeObject):
            return make_context_block(
                type_uri=item.type_uri,
                object_refs=(item.object_id,),
                content=item.model_dump(mode="json"),
                evidence_refs=item.evidence_refs,
                scope=item.scope,
                required=item.object_id in request.object_refs,
                evidence_validator=self._evidence_validator,
            )
        if isinstance(item, Claim):
            return make_context_block(
                type_uri=item.object_type_uri,
                object_refs=(item.subject_ref,),
                content=item.model_dump(mode="json"),
                evidence_refs=item.evidence_refs,
                scope=item.scope,
                required=item.claim_id in (*request.object_refs, *request.required_claim_refs),
                evidence_validator=self._evidence_validator,
            )
        return make_context_block(
            type_uri=item.relation_type,
            object_refs=(item.subject_ref, item.object_ref),
            content=item.model_dump(mode="json"),
            evidence_refs=item.evidence_refs,
            scope=item.scope,
            required=item.relation_id in request.object_refs,
            evidence_validator=self._evidence_validator,
        )

    def _select(self, records: Sequence[Any], request: ContextRequest) -> tuple[Any, ...]:
        selected: list[Any] = []
        for item in records:
            record_id = self._record_id(item)
            type_uri = getattr(item, "type_uri", getattr(item, "object_type_uri", getattr(item, "relation_type", "")))
            if request.object_refs and record_id not in request.object_refs and not (
                isinstance(item, Claim) and item.claim_id in request.required_claim_refs
            ):
                continue
            if request.type_uris and type_uri not in request.type_uris:
                continue
            if self._pov_filter is not None and request.pov_id is not None:
                decision = _call_filter(self._pov_filter, item, request)
                if decision is False or decision is None:
                    continue
                if isinstance(decision, (KnowledgeObject, Claim, Relation)):
                    item = decision
            selected.append(item)
        return tuple(selected)

    def _check_capabilities(self, request: ContextRequest) -> None:
        if not request.capabilities:
            return
        available = _capability_set(self._capabilities, request.work_id)
        if available is None and self._registry is not None:
            available = _capability_set(self._registry, request.work_id)
        missing = set(request.capabilities) if available is None else set(request.capabilities) - available
        if missing:
            raise ContextCompilationError(
                "required context capability is unavailable: " + ",".join(sorted(missing)),
                ErrorCode.KNOWLEDGE_INSUFFICIENT.value,
            )

    def compile(self, request: ContextRequest | Mapping[str, Any]) -> ContextView:
        request = _as_model(request)
        self._check_capabilities(request)
        scope = Scope(
            work_id=request.work_id,
            branch_id=request.branch_id,
            as_of=request.as_of,
            purpose=request.purpose,
            actor=request.actor,
        )
        records = self._select(self._approved_records(request), request)
        by_id = {self._record_id(item): item for item in records}
        missing_claims = [ref for ref in request.required_claim_refs if ref not in by_id]
        missing_objects = [ref for ref in request.object_refs if ref not in by_id]
        if missing_claims or missing_objects:
            missing = tuple(missing_claims + missing_objects)
            raise ContextCompilationError(
                "required knowledge is unavailable: " + ",".join(missing),
                ErrorCode.KNOWLEDGE_INSUFFICIENT.value,
            )
        blocks: list[Any] = []
        for item in records:
            try:
                block = self._render(item, request)
                if not isinstance(block, ContextBlock):
                    raise ContextBlockError("domain renderer must return ContextBlock")
                if block.scope.work_id != request.work_id or block.scope.branch_id != request.branch_id:
                    raise ContextBlockError("rendered block is outside selector scope", ErrorCode.INVALID_SCOPE.value)
                if not block.evidence_refs:
                    raise ContextBlockError("rendered block has no evidence", ErrorCode.NO_EVIDENCE.value)
                if block.required != (
                    self._record_id(item) in request.object_refs
                    or isinstance(item, Claim) and self._record_id(item) in request.required_claim_refs
                ):
                    block = ContextBlock.model_validate({
                        **block.model_dump(mode="python"),
                        "required": self._record_id(item) in request.object_refs
                        or isinstance(item, Claim) and self._record_id(item) in request.required_claim_refs,
                        "block_hash": "",
                    })
                if recompute_block_hash(block) != block.block_hash:
                    raise ContextBlockError("rendered block hash is not canonical")
                blocks.append(block)
            except ContextBlockError as exc:
                raise ContextCompilationError(str(exc), exc.code) from exc
        required = [block for block in blocks if block.required]
        optional = [block for block in blocks if not block.required]
        budget = request.budget_tokens
        if budget is not None:
            required_cost = sum(self._token_counter(block.content) for block in required)
            if required_cost > budget:
                raise ContextCompilationError(
                    "required context blocks exceed token budget",
                    ErrorCode.TOKEN_BUDGET_EXCEEDED.value,
                )
            kept = list(required)
            used = required_cost
            omitted: list[str] = []
            for block in optional:
                cost = self._token_counter(block.content)
                if used + cost <= budget:
                    kept.append(block)
                    used += cost
                else:
                    omitted.extend(block.object_refs or (block.block_id,))
            blocks = kept
        else:
            used = sum(self._token_counter(block.content) for block in blocks)
            omitted = []
        forbidden = set(omitted)
        for conflict in (*self._conflicts, *_read_collection(self._provider, "conflicts", ())):
            if isinstance(conflict, ConflictSet) and conflict.status.upper() != "RESOLVED":
                forbidden.add(conflict.conflict_id)
        diagnostics = tuple(sorted(f"optional block omitted: {ref}" for ref in forbidden if ref in omitted))
        evidence_by_hash = {
            _stable_hash(ref): ref for block in blocks for ref in block.evidence_refs
        }
        evidence = tuple(sorted(evidence_by_hash.values(), key=lambda ref: (ref.source_id, ref.document_id, ref.start)))
        completeness = "PARTIAL" if omitted else "COMPLETE"
        seed = {"request": request.model_dump(mode="json"), "block_hashes": [block.block_hash for block in blocks]}
        view_id = "view-" + _stable_hash(seed)[:32]
        try:
            return ContextView(
                view_id=view_id,
                work_id=request.work_id,
                branch_id=request.branch_id,
                as_of=request.as_of,
                purpose=request.purpose,
                scope=scope,
                blocks=tuple(blocks),
                evidence_refs=evidence,
                forbidden_refs=tuple(sorted(forbidden)),
                conflicts=tuple(sorted(conflict.conflict_id for conflict in (*self._conflicts, *_read_collection(self._provider, "conflicts", ())) if isinstance(conflict, ConflictSet) and conflict.status.upper() != "RESOLVED")),
                staleness="FRESH",
                completeness=completeness,
                budget={"limit_tokens": budget or 0, "used_tokens": used, "required_tokens": sum(self._token_counter(block.content) for block in required)},
            )
        except Exception as exc:
            if isinstance(exc, ContextCompilationError):
                raise
            raise ContextCompilationError("failed to construct ContextView", ErrorCode.INVALID_SCHEMA.value) from exc


__all__ = ["ContextCompilationError", "ContextRequest", "ContextCompiler", "BranchContextRef", "KnowledgeHead", "SourceCompositeRef"]
