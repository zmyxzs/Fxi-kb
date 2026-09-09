"""Evidence-bound construction and validation for context blocks."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from fxi.core.exceptions import FxiError

from .contracts import ContextBlock, EvidenceRef, ErrorCode, Scope, _stable_hash, _token


class ContextBlockError(FxiError):
    def __init__(self, message: str, code: str = ErrorCode.INVALID_SCHEMA.value) -> None:
        super().__init__(message, code=code)


def block_hash_payload(block: ContextBlock) -> Mapping[str, Any]:
    """Return the exact payload hashed by the frozen ``ContextBlock`` contract."""

    return {
        key: value
        for key, value in (
            ("block_id", block.block_id),
            ("block_kind", block.block_kind),
            ("type_uri", block.type_uri),
            ("object_refs", block.object_refs),
            ("content", block.content),
            ("evidence_refs", block.evidence_refs),
            ("scope", block.scope),
            ("required", block.required),
        )
    }


def recompute_block_hash(block: ContextBlock) -> str:
    if not isinstance(block, ContextBlock):
        raise ContextBlockError("block must be a ContextBlock")
    return _stable_hash(block_hash_payload(block))


def _validate_evidence(
    refs: Sequence[EvidenceRef],
    scope: Scope,
    validator: Callable[..., Any] | None,
) -> tuple[EvidenceRef, ...]:
    if not refs:
        raise ContextBlockError("non-forbidden context blocks require evidence", ErrorCode.NO_EVIDENCE.value)
    checked: list[EvidenceRef] = []
    for ref in refs:
        if not isinstance(ref, EvidenceRef):
            raise ContextBlockError("evidence_refs must contain EvidenceRef instances", ErrorCode.INVALID_EVIDENCE.value)
        if ref.scope is None:
            raise ContextBlockError("evidence must carry an explicit scope", ErrorCode.INVALID_EVIDENCE.value)
        if ref.scope.work_id != scope.work_id or ref.scope.branch_id != scope.branch_id:
            raise ContextBlockError("evidence is outside block scope", ErrorCode.INVALID_SCOPE.value)
        if validator is not None:
            try:
                result = validator(ref)
            except TypeError:
                result = validator(ref, scope=scope)
            if result is False:
                raise ContextBlockError("evidence validator rejected reference", ErrorCode.INVALID_EVIDENCE.value)
            if isinstance(result, EvidenceRef):
                ref = result
        checked.append(ref)
    return tuple(checked)


def make_context_block(
    *,
    type_uri: str,
    object_refs: Sequence[str],
    content: Any,
    evidence_refs: Sequence[EvidenceRef],
    scope: Scope,
    block_kind: str = "FACT",
    required: bool = False,
    block_id: str | None = None,
    evidence_validator: Callable[..., Any] | None = None,
) -> ContextBlock:
    """Build a deterministic block and fail closed on missing evidence.

    ``FORBIDDEN`` and ``DIAGNOSTIC`` are control-plane entries; they are never
    accepted as fact blocks by this helper.  The compiler keeps forbidden
    references in ``ContextView.forbidden_refs`` instead of promoting them to
    knowledge.
    """

    if not isinstance(scope, Scope):
        raise ContextBlockError("scope must be a Scope", ErrorCode.INVALID_SCOPE.value)
    if block_kind not in {"FACT", "METHOD", "EXPRESSION", "FORBIDDEN", "DIAGNOSTIC"}:
        raise ContextBlockError("unsupported context block kind")
    if block_kind in {"FORBIDDEN", "DIAGNOSTIC"}:
        raise ContextBlockError("control entries cannot be emitted as fact blocks")
    try:
        type_uri = _token(type_uri, "type_uri")
        refs = tuple(_token(ref, "object_ref") for ref in object_refs)
    except ValueError as exc:
        raise ContextBlockError(str(exc)) from exc
    if not refs:
        raise ContextBlockError("context block must reference at least one object")
    checked_evidence = _validate_evidence(evidence_refs, scope, evidence_validator)
    seed = {
        "type_uri": type_uri,
        "object_refs": refs,
        "scope": scope,
        "content": content,
        "evidence_refs": checked_evidence,
        "block_kind": block_kind,
        "required": required,
    }
    generated_id = "block-" + _stable_hash(seed)[:32]
    try:
        selected_id = _token(block_id or generated_id, "block_id")
    except ValueError as exc:
        raise ContextBlockError(str(exc)) from exc
    block = ContextBlock(
        block_id=selected_id,
        block_kind=block_kind,
        type_uri=type_uri,
        object_refs=refs,
        content=content,
        evidence_refs=checked_evidence,
        scope=scope,
        required=required,
    )
    if recompute_block_hash(block) != block.block_hash:
        raise ContextBlockError("context block hash could not be recomputed")
    return block


__all__ = ["ContextBlockError", "block_hash_payload", "make_context_block", "recompute_block_hash"]
