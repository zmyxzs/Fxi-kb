"""Evidence validation primitives used by source and candidate services."""

from __future__ import annotations

from typing import Any, Mapping

from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError

from .contracts import EvidenceRef, ErrorCode


class EvidenceError(FxiError):
    """Evidence is missing, out of bounds, or does not match its hash."""

    def __init__(self, message: str, code: str = ErrorCode.INVALID_EVIDENCE.value):
        super().__init__(message, code=code)


class ValidatedEvidence(EvidenceRef):
    """An EvidenceRef whose excerpt was checked against the supplied source."""

    excerpt: str
    excerpt_hash: str


def validate_evidence_ref(
    ref: EvidenceRef,
    *,
    expected_text: str | None = None,
    document_length: int | None = None,
) -> ValidatedEvidence | EvidenceRef:
    """Validate coordinates and, when available, the exact excerpt hash.

    The helper never stores the source text.  Callers that do not have the
    source bytes can still use it for structural validation, while a source
    adapter should pass the bounded excerpt for cryptographic validation.
    """

    if ref.end <= ref.start:
        raise EvidenceError("evidence range must be non-empty and half-open")
    if document_length is not None and ref.end > document_length:
        raise EvidenceError("evidence range exceeds document boundary")
    if expected_text is None:
        return ref
    if len(expected_text) != ref.end - ref.start:
        raise EvidenceError("evidence excerpt length does not match coordinates")
    expected_hash = sha256_hex(expected_text)
    if expected_hash != ref.excerpt_hash:
        raise EvidenceError("evidence excerpt hash mismatch")
    data: Mapping[str, Any] = ref.model_dump(mode="python")
    data["excerpt"] = expected_text
    return ValidatedEvidence.model_validate(data)


__all__ = ["EvidenceError", "ValidatedEvidence", "validate_evidence_ref"]
