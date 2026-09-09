"""Fail-closed sufficiency checks for selector-compiled views."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fxi.core.exceptions import FxiError

from .contracts import ContextView, ErrorCode, SufficiencyReport
from .view_selector import ViewSelector


class KnowledgeSufficiencyError(FxiError):
    def __init__(self, message: str, code: str = ErrorCode.KNOWLEDGE_INSUFFICIENT.value) -> None:
        super().__init__(message, code=code)


def _capabilities(source: Any, work_id: str) -> set[str]:
    if source is None:
        return set()
    value = source
    method = getattr(source, "capabilities", None)
    if callable(method):
        try:
            value = method(work_id)
        except TypeError:
            value = method()
    if isinstance(value, Mapping):
        result = set(value)
        for key in ("capabilities", "model_capabilities", "projections", "views"):
            nested = value.get(key, ())
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
                result.update(str(item) for item in nested)
        return result
    if isinstance(value, str):
        return {value}
    try:
        return {str(item) for item in value}
    except TypeError:
        return set()


def _ref_ids(ref: Any) -> set[str]:
    return {
        value
        for value in (
            getattr(ref, "evidence_id", None),
            getattr(ref, "source_snapshot_ref", None),
            getattr(ref, "source_id", None),
        )
        if value
    }


class KnowledgeSufficiencyService:
    """Assess the delivered view; it never fills gaps or invents knowledge."""

    def __init__(self, *, capabilities: Any = None) -> None:
        self._capabilities = capabilities

    def assess(self, selector: ViewSelector, view: ContextView) -> SufficiencyReport:
        if not isinstance(selector, ViewSelector) or not isinstance(view, ContextView):
            raise KnowledgeSufficiencyError("selector and view must use public contracts", ErrorCode.INVALID_SCHEMA.value)
        missing_required: set[str] = set()
        block_types = {block.type_uri for block in view.blocks}
        block_refs = {ref for block in view.blocks for ref in block.object_refs}
        claim_refs: set[str] = set()
        for block in view.blocks:
            content = block.content
            if isinstance(content, Mapping):
                claim_ref = content.get("claim_id")
                if isinstance(claim_ref, str):
                    claim_refs.add(claim_ref)
        for type_uri in selector.type_uris:
            if type_uri not in block_types:
                missing_required.add("type_uri:" + type_uri)
        for object_ref in selector.object_refs:
            if object_ref not in block_refs:
                missing_required.add("object_ref:" + object_ref)
        for claim_ref in selector.required_claim_refs:
            if claim_ref not in claim_refs and claim_ref not in block_refs:
                missing_required.add("claim_ref:" + claim_ref)
        available_evidence = set()
        for ref in view.evidence_refs:
            available_evidence.update(_ref_ids(ref))
        missing_evidence = tuple(
            sorted("evidence:" + ref for ref in selector.required_evidence_refs if ref not in available_evidence)
        )
        available_capabilities = _capabilities(self._capabilities, selector.work_id)
        missing_capabilities = tuple(sorted(set(selector.capabilities) - available_capabilities))
        diagnostics: list[str] = []
        if view.completeness.upper() != "COMPLETE":
            diagnostics.append("view is incomplete")
        if view.staleness.upper() != "FRESH":
            diagnostics.append("view is stale")
        if view.conflicts:
            diagnostics.append("unresolved conflicts are present")
        diagnostics.extend(view.forbidden_refs)
        if missing_required or missing_evidence or missing_capabilities:
            diagnostics.append(ErrorCode.KNOWLEDGE_INSUFFICIENT.value)
        if view.conflicts:
            status = "CONFLICTED"
        elif missing_required or missing_evidence or missing_capabilities or diagnostics:
            status = "INCOMPLETE"
        else:
            status = "SUFFICIENT"
        return SufficiencyReport(
            status=status,
            missing_required=tuple(sorted(missing_required)),
            missing_capabilities=missing_capabilities,
            missing_evidence=missing_evidence,
            diagnostics=tuple(diagnostics),
        )


KnowledgeSufficiency = KnowledgeSufficiencyService

__all__ = ["KnowledgeSufficiency", "KnowledgeSufficiencyError", "KnowledgeSufficiencyService"]
