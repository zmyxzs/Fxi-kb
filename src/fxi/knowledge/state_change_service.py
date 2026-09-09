"""Evidence-bound StateChangeSet construction for Fxi v3."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, Sequence

from pydantic import Field, ValidationError as PydanticValidationError, model_validator

from fxi.core.exceptions import FxiError

from .as_of import compare_as_of
from .contracts import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    ContractModel,
    EvidenceRef,
    ErrorCode,
    ReviewReport,
    Scope,
    StateChangeSet,
    _hash,
    _stable_hash,
    _token,
)
from .objects import KnowledgeHead
from .registry import DomainRegistry, RegistryError


class StateChangeServiceError(FxiError):
    """A state change set cannot be accepted as an authoritative input."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value):
        super().__init__(message, code=code)


class StateChangeRequest(ContractModel):
    """Strict input form for building a StateChangeSet."""

    review: ReviewReport
    changes: tuple[Mapping[str, Any], ...]
    source_artifact_hash: str
    change_set_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_review_alias(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        if "review" not in result and "review_report" in result:
            result["review"] = result.pop("review_report")
        return result

    @model_validator(mode="after")
    def validate_request(self) -> "StateChangeRequest":
        _hash(self.source_artifact_hash, "source_artifact_hash")
        if self.change_set_id is not None:
            _token(self.change_set_id, "change_set_id")
        return self


class StateChangeResult(ContractModel):
    """An auditable result which can explicitly remain incomplete."""

    change_set: StateChangeSet
    status: str
    diagnostics: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == "VALID"


def _review_hash(report: ReviewReport) -> str:
    payload = {
        key: getattr(report, key)
        for key in (
            "report_id",
            "draft_ref",
            "draft_hash",
            "coordinate",
            "context_hash",
            "checks",
            "overall_status",
            "blocking_findings",
        )
    }
    return _stable_hash(payload)


def _change_set_hash(change_set: StateChangeSet) -> str:
    payload = {
        key: getattr(change_set, key)
        for key in ("change_set_id", "coordinate", "source_artifact_hash", "changes", "review_ref")
    }
    return _stable_hash(payload)


class StateChangeService:
    """Validate explicit state changes without applying them."""

    contract_revision = CONTRACT_REVISION
    schema_hash = SCHEMA_HASH

    def __init__(
        self,
        *,
        domain_registry: DomainRegistry | None = None,
        id_factory: Callable[[Mapping[str, Any]], str] | None = None,
    ) -> None:
        self.domain_registry = domain_registry
        self.id_factory = id_factory or (lambda payload: f"changes-{_stable_hash(payload)}")

    def build(
        self,
        review: ReviewReport | StateChangeRequest | Mapping[str, Any],
        changes: Sequence[Mapping[str, Any]] | None = None,
        source_artifact_hash: str | None = None,
        *,
        change_set_id: str | None = None,
    ) -> StateChangeResult:
        request = self._normalize_request(review, changes, source_artifact_hash, change_set_id)
        report = request.review
        self._require_real_report(report)
        diagnostics: list[str] = []
        if report.overall_status != "PASSED":
            diagnostics.append("REVIEW_NOT_FINAL")

        normalized_changes: list[Mapping[str, Any]] = []
        for index, raw in enumerate(request.changes):
            normalized, item_diagnostics = self._normalize_change(
                raw,
                index=index,
                review=report,
                source_artifact_hash=request.source_artifact_hash,
            )
            normalized_changes.append(normalized)
            diagnostics.extend(item_diagnostics)

        payload = {
            "review_ref": report.report_id,
            "source_artifact_hash": request.source_artifact_hash,
            "coordinate": report.coordinate,
            "changes": normalized_changes,
        }
        resolved_id = request.change_set_id or change_set_id or self.id_factory(payload)
        try:
            resolved_id = _token(resolved_id, "change_set_id")
        except ValueError as exc:
            raise StateChangeServiceError("change_set_id is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
        change_set = StateChangeSet(
            change_set_id=resolved_id,
            coordinate=report.coordinate,
            source_artifact_hash=request.source_artifact_hash,
            changes=tuple(deepcopy(dict(item)) for item in normalized_changes),
            review_ref=report.report_id,
        )
        if change_set.change_set_hash != _change_set_hash(change_set):
            raise StateChangeServiceError("state change hash calculation failed")
        status = self._status(diagnostics)
        return StateChangeResult(
            change_set=change_set,
            status=status,
            diagnostics=tuple(dict.fromkeys(diagnostics)),
        )

    def from_review(
        self,
        review: ReviewReport,
        changes: Sequence[Mapping[str, Any]],
        source_artifact_hash: str,
    ) -> StateChangeResult:
        """Named alias used by API adapters and downstream ports."""

        return self.build(review, changes, source_artifact_hash)

    def create(
        self,
        review: ReviewReport | StateChangeRequest | Mapping[str, Any],
        changes: Sequence[Mapping[str, Any]] | None = None,
        source_artifact_hash: str | None = None,
        *,
        change_set_id: str | None = None,
    ) -> StateChangeSet:
        result = self.build(
            review,
            changes,
            source_artifact_hash,
            change_set_id=change_set_id,
        )
        if not result.valid:
            code = ErrorCode.NO_EVIDENCE.value if "NO_EVIDENCE" in result.diagnostics else ErrorCode.KNOWLEDGE_INSUFFICIENT.value
            if "CONFLICTED" in result.diagnostics:
                code = ErrorCode.CONFLICTING_ASSERTIONS.value
            raise StateChangeServiceError(
                "state change set is incomplete: " + ",".join(result.diagnostics),
                code,
            )
        return result.change_set

    def validate(
        self,
        change_set: StateChangeSet | Mapping[str, Any],
        review: ReviewReport | None = None,
    ) -> StateChangeResult:
        try:
            normalized = change_set if isinstance(change_set, StateChangeSet) else StateChangeSet.model_validate(change_set)
        except PydanticValidationError as exc:
            raise StateChangeServiceError("state change set is invalid", ErrorCode.INVALID_SCHEMA.value) from exc
        if normalized.change_set_hash != _change_set_hash(normalized):
            raise StateChangeServiceError("state change hash does not match canonical content", ErrorCode.INVALID_SCHEMA.value)
        if review is None:
            raise StateChangeServiceError("state change validation requires its final review", ErrorCode.MISSING_CONTEXT.value)
        result = self.build(
            review,
            normalized.changes,
            normalized.source_artifact_hash,
            change_set_id=normalized.change_set_id,
        )
        if result.change_set.change_set_hash != normalized.change_set_hash:
            raise StateChangeServiceError("state change content differs from validated review", ErrorCode.IDEMPOTENCY_CONFLICT.value)
        return result

    @staticmethod
    def _normalize_request(
        review: ReviewReport | StateChangeRequest | Mapping[str, Any],
        changes: Sequence[Mapping[str, Any]] | None,
        source_artifact_hash: str | None,
        change_set_id: str | None,
    ) -> StateChangeRequest:
        if isinstance(review, StateChangeRequest):
            return review.model_copy(deep=True)
        if isinstance(review, ReviewReport):
            data: dict[str, Any] = {
                "review": review,
                "changes": changes or (),
                "source_artifact_hash": source_artifact_hash,
                "change_set_id": change_set_id,
            }
        elif isinstance(review, Mapping):
            data = dict(review)
            if changes is not None:
                data["changes"] = changes
            if source_artifact_hash is not None:
                data["source_artifact_hash"] = source_artifact_hash
            if change_set_id is not None:
                data["change_set_id"] = change_set_id
        else:
            raise StateChangeServiceError("review must be a final ReviewReport", ErrorCode.INVALID_SCHEMA.value)
        try:
            return StateChangeRequest.model_validate(data)
        except (PydanticValidationError, TypeError) as exc:
            raise StateChangeServiceError("state change request is invalid", ErrorCode.INVALID_SCHEMA.value) from exc

    @staticmethod
    def _require_real_report(report: ReviewReport) -> None:
        if not isinstance(report, ReviewReport):
            raise StateChangeServiceError("state changes require a ReviewReport", ErrorCode.INVALID_SCHEMA.value)
        if report.report_hash != _review_hash(report):
            raise StateChangeServiceError("review report hash is not canonical", ErrorCode.INVALID_SCHEMA.value)

    def _normalize_change(
        self,
        raw: Mapping[str, Any],
        *,
        index: int,
        review: ReviewReport,
        source_artifact_hash: str,
    ) -> tuple[Mapping[str, Any], list[str]]:
        if not isinstance(raw, Mapping):
            return {"change_id": f"change-{index + 1}", "status": "INCOMPLETE"}, ["CHANGE_NOT_MAPPING"]
        item = deepcopy(dict(raw))
        diagnostics: list[str] = []
        change_id = item.get("change_id", item.get("state_change_id", item.get("id")))
        if change_id is None:
            diagnostics.append("CHANGE_ID_MISSING")
            change_id = f"change-{index + 1}"
        try:
            change_id = _token(str(change_id), "change_id")
        except ValueError:
            diagnostics.append("CHANGE_ID_INVALID")
            change_id = f"change-{index + 1}"
        item["change_id"] = change_id

        for field_name in ("type_uri", "before", "after", "delta", "scope", "narrative_order"):
            if field_name not in item:
                diagnostics.append(f"{field_name.upper()}_MISSING")
        type_uri = item.get("type_uri")
        if type_uri is not None:
            try:
                type_uri = _token(type_uri, "type_uri")
                item["type_uri"] = type_uri
            except ValueError:
                diagnostics.append("TYPE_URI_INVALID")
        if "narrative_order" in item:
            order = item["narrative_order"]
            if isinstance(order, bool) or not isinstance(order, int) or order <= 0:
                diagnostics.append("NARRATIVE_ORDER_INVALID")

        scope_value = item.get("scope")
        scope: Scope | None = None
        if scope_value is not None:
            try:
                scope = scope_value if isinstance(scope_value, Scope) else Scope.model_validate(scope_value)
            except PydanticValidationError:
                diagnostics.append("SCOPE_INVALID")
            if scope is not None:
                coordinate = review.coordinate
                if scope.work_id != coordinate.work_id or scope.branch_id != coordinate.branch_id:
                    diagnostics.append("SCOPE_FOREIGN")
                else:
                    try:
                        if compare_as_of(scope.as_of, coordinate.as_of) != 0:
                            diagnostics.append("SCOPE_AS_OF_MISMATCH")
                    except Exception:
                        diagnostics.append("SCOPE_AS_OF_INVALID")
                item["scope"] = scope.model_dump(mode="json")

        evidence_values = item.get("evidence_refs", item.get("evidence", ()))
        if evidence_values is None or evidence_values == () or evidence_values == []:
            diagnostics.append("NO_EVIDENCE")
            evidence_values = ()
        if not isinstance(evidence_values, Sequence) or isinstance(evidence_values, (str, bytes)):
            diagnostics.append("EVIDENCE_INVALID")
            evidence_values = ()
        evidence_refs: list[EvidenceRef] = []
        for value in evidence_values:
            try:
                evidence = value if isinstance(value, EvidenceRef) else EvidenceRef.model_validate(value)
            except PydanticValidationError:
                diagnostics.append("EVIDENCE_INVALID")
                continue
            coordinate = review.coordinate
            if evidence.scope is None or evidence.scope.work_id != coordinate.work_id or evidence.scope.branch_id != coordinate.branch_id:
                diagnostics.append("EVIDENCE_SCOPE_FOREIGN")
            if coordinate.source_id and evidence.source_id != coordinate.source_id:
                diagnostics.append("EVIDENCE_SOURCE_FOREIGN")
            if coordinate.source_version and evidence.source_version != coordinate.source_version:
                diagnostics.append("EVIDENCE_VERSION_STALE")
            evidence_refs.append(evidence)
        item["evidence_refs"] = tuple(ref.model_dump(mode="json") for ref in evidence_refs)

        claim_values = item.get("claim_refs", item.get("claim_ref", ()))
        if isinstance(claim_values, str):
            claim_values = (claim_values,)
        if not isinstance(claim_values, Sequence) or isinstance(claim_values, (bytes, str)):
            diagnostics.append("CLAIM_REFS_INVALID")
            claim_values = ()
        safe_claims: list[str] = []
        for value in claim_values:
            try:
                safe_claims.append(_token(value, "claim_ref"))
            except ValueError:
                diagnostics.append("CLAIM_REF_INVALID")
        item["claim_refs"] = tuple(safe_claims)

        item_hash = item.get("source_artifact_hash", item.get("artifact_hash"))
        if item_hash is None:
            diagnostics.append("SOURCE_ARTIFACT_HASH_MISSING")
        elif item_hash != source_artifact_hash:
            diagnostics.append("SOURCE_ARTIFACT_HASH_MISMATCH")
        else:
            item["source_artifact_hash"] = source_artifact_hash

        status = str(item.get("status", item.get("resolution_status", "PROPOSED"))).upper()
        if status == "UNRESOLVED" or str(item.get("resolution_status", "")).upper() == "UNRESOLVED":
            diagnostics.append("UNRESOLVED")
        if status == "CONFLICTED":
            diagnostics.append("CONFLICTED")
        item["status"] = status

        if self.domain_registry is not None and isinstance(type_uri, str) and "after" in item:
            after = item["after"] if isinstance(item["after"], Mapping) else {"value": item["after"]}
            try:
                self.domain_registry.validate(type_uri, after)
            except RegistryError:
                diagnostics.append("DOMAIN_TYPE_REJECTED")

        return item, diagnostics

    @staticmethod
    def _status(diagnostics: Sequence[str]) -> str:
        if "CONFLICTED" in diagnostics or any(item.endswith("_FOREIGN") for item in diagnostics):
            return "CONFLICTED" if "CONFLICTED" in diagnostics else "INCOMPLETE"
        return "INCOMPLETE" if diagnostics else "VALID"


__all__ = [
    "SCHEMA_HASH",
    "StateChangeRequest",
    "StateChangeResult",
    "StateChangeService",
    "StateChangeServiceError",
]
