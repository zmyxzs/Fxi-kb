"""Selector-aware, read-only query dispatch with a lexical baseline."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .contracts import EvidenceRef, ErrorCode, QueryRequest, QueryResult, _stable_hash, _token


class QueryServiceError(FxiError):
    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value) -> None:
        super().__init__(message, code=code)


class QueryService:
    """Dispatch registered intents without silently substituting retrieval modes."""

    def __init__(
        self,
        lexical_baseline: Callable[[QueryRequest], Any] | None = None,
        *,
        fts: Callable[[QueryRequest], Any] | None = None,
        intents: Mapping[str, Any] | None = None,
        capabilities: Sequence[str] = ("fts", "lexical"),
        version: str = "fts-baseline.v1",
    ) -> None:
        if lexical_baseline is not None and fts is not None:
            raise QueryServiceError("only one lexical baseline may be supplied")
        self._lexical = lexical_baseline or fts
        self._intents: dict[str, tuple[Callable[..., Any], tuple[str, ...]]] = {}
        self._capabilities = set(capabilities)
        self._version = _token(version, "version")
        if self._lexical is not None:
            self.register_intent("lexical", self._lexical, capabilities=("lexical",))
            self.register_intent("fts", self._lexical, capabilities=("fts",))
        for name, handler in (intents or {}).items():
            if isinstance(handler, tuple) and len(handler) == 2:
                self.register_intent(name, handler[0], capabilities=handler[1])
            else:
                self.register_intent(name, handler)

    def register_intent(
        self,
        intent: str,
        handler: Callable[..., Any],
        *,
        capabilities: Sequence[str] = (),
    ) -> None:
        try:
            intent = _token(intent, "intent")
        except ValueError as exc:
            raise QueryServiceError("intent must be a safe identifier") from exc
        if not callable(handler):
            raise QueryServiceError("query intent handler must be callable")
        try:
            required = tuple(_token(item, "capability") for item in capabilities)
        except ValueError as exc:
            raise QueryServiceError("intent capabilities are invalid") from exc
        self._intents[intent] = (handler, required)

    register = register_intent

    @staticmethod
    def _result_base(request: QueryRequest, *, status: str, code: str, diagnostics: Sequence[str] = ()) -> dict[str, Any]:
        query_hash = sha256_hex(request.model_dump(mode="json"))
        return {
            "trace_id": "trace-" + query_hash[:32],
            "query_hash": query_hash,
            "work_id": request.work_id,
            "branch_id": request.branch_id,
            "status": status,
            "code": code,
            "diagnostics": tuple(diagnostics),
        }

    def _error(self, request: QueryRequest, code: str, message: str) -> QueryResult:
        return QueryResult.model_validate(self._result_base(request, status="ERROR", code=code, diagnostics=(message,)))

    def _invoke(self, handler: Callable[..., Any], request: QueryRequest) -> Any:
        try:
            return handler(request)
        except TypeError as first:
            try:
                return handler(request.query, request)
            except TypeError:
                raise first

    @staticmethod
    def _normalise(value: Any) -> dict[str, Any]:
        if isinstance(value, QueryResult):
            return value.model_dump(mode="python")
        if isinstance(value, Mapping):
            data = dict(value)
            if "refs" in data and "result_refs" not in data:
                data["result_refs"] = data.pop("refs")
            return data
        if isinstance(value, (str, bytes)):
            return {"result_refs": (value.decode() if isinstance(value, bytes) else value,)}
        try:
            return {"result_refs": tuple(value)}
        except TypeError as exc:
            raise QueryServiceError("query intent returned an unsupported result") from exc

    @staticmethod
    def _safe_refs(values: Sequence[str], label: str) -> tuple[str, ...]:
        try:
            return tuple(_token(value, label) for value in values)
        except (TypeError, ValueError) as exc:
            raise QueryServiceError(f"query result {label} are invalid") from exc

    def _query_evidence(self, values: Sequence[EvidenceRef], request: QueryRequest) -> tuple[EvidenceRef, ...]:
        result: list[EvidenceRef] = []
        for ref in values:
            if not isinstance(ref, EvidenceRef):
                raise QueryServiceError("query evidence must contain EvidenceRef instances", ErrorCode.INVALID_EVIDENCE.value)
            if ref.scope is None:
                raise QueryServiceError("query evidence must carry an explicit scope", ErrorCode.INVALID_EVIDENCE.value)
            if (ref.scope.work_id, ref.scope.branch_id) != (request.work_id, request.branch_id):
                continue
            if request.source_ids and ref.source_id not in request.source_ids:
                continue
            result.append(ref)
        unique = {_stable_hash(ref): ref for ref in result}
        return tuple(sorted(unique.values(), key=lambda ref: (ref.source_id, ref.document_id, ref.start)))

    def query(self, request: QueryRequest | Mapping[str, Any], *, intent: str | None = None) -> QueryResult:
        try:
            request = request if isinstance(request, QueryRequest) else QueryRequest.model_validate(request)
        except Exception as exc:
            raise QueryServiceError("invalid query request", ErrorCode.INVALID_SCHEMA.value) from exc
        selected_intent = intent or request.purpose
        try:
            selected_intent = _token(selected_intent, "intent")
        except ValueError:
            return self._error(request, ErrorCode.INVALID_SCHEMA.value, "query intent is invalid")
        entry = self._intents.get(selected_intent)
        if entry is None:
            return self._error(request, ErrorCode.NOT_FOUND.value, f"unknown query intent: {selected_intent}")
        handler, required_capabilities = entry
        requested = set(request.capabilities)
        missing = (set(required_capabilities) | requested) - self._capabilities
        if missing:
            code = ErrorCode.CAPABILITY_UNSUPPORTED.value
            return self._error(request, code, "unsupported query capability: " + ",".join(sorted(missing)))
        try:
            data = self._normalise(self._invoke(handler, request))
            refs = self._safe_refs(data.get("result_refs", ()), "result_refs")
            evidence = self._query_evidence(data.get("evidence_refs", ()), request)
            versions = self._safe_refs(data.get("source_versions", ()), "source_versions")
            hits = self._safe_refs(
                data.get("capability_hits", tuple(sorted(set(required_capabilities) | requested))),
                "capability_hits",
            )
            diagnostics = tuple(str(item) for item in data.get("diagnostics", ()))
            if not versions:
                versions = tuple(sorted({ref.source_version for ref in evidence}))
            return QueryResult.model_validate({
                **self._result_base(request, status="OK", code="OK", diagnostics=diagnostics),
                "source_versions": versions,
                "result_refs": refs,
                "evidence_refs": evidence,
                "capability_hits": hits,
            })
        except QueryServiceError as exc:
            return self._error(request, exc.code, str(exc))
        except Exception as exc:
            return self._error(request, ErrorCode.INVALID_SCHEMA.value, f"query intent failed: {type(exc).__name__}")


__all__ = ["QueryService", "QueryServiceError"]
