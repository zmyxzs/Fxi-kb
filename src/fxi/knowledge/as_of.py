"""Canonical as-of values and validity checks for F5 selectors.

The v3 contracts intentionally keep ``as_of`` as ``int | str`` on the wire.
This module supplies the stricter comparison semantics needed by selectors:
integers are narrative positions and strings must be ISO calendar timestamps.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import field_validator, model_validator

from fxi.core.exceptions import ValidationError

from .contracts import ContractModel, Validity, _stable_hash, _token


class AsOfError(ValidationError):
    """A fail-closed as-of or validity scope error."""

    def __init__(self, message: str, *, code: str = "INVALID_SCOPE") -> None:
        super().__init__(message)
        self.code = code


def _parse_as_of(value: int | str) -> tuple[str, int | datetime]:
    if isinstance(value, bool):
        raise AsOfError("as_of cannot be boolean")
    if isinstance(value, int):
        if value < 0:
            raise AsOfError("as_of cannot be negative")
        return "integer", value
    if not isinstance(value, str) or not value.strip():
        raise AsOfError("as_of must be a non-empty integer or ISO timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError as exc:
            raise AsOfError("as_of string must be an ISO date or datetime") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    else:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return "timestamp", parsed


class AsOf(ContractModel):
    """A validated, hashable selector time.

    ``source_version`` is optional metadata used when a caller wants to bind a
    selector to a particular source revision.  It is not used as a priority or
    conflict decision.
    """

    value: int | str
    source_version: str | None = None
    as_of_hash: str = ""

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: int | str) -> int | str:
        _parse_as_of(value)
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_version")
    @classmethod
    def validate_source_version(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "source_version")

    @model_validator(mode="before")
    @classmethod
    def calculate_hash(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        result = dict(data)
        payload = {"value": result.get("value"), "source_version": result.get("source_version")}
        expected = _stable_hash(payload)
        supplied = result.get("as_of_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("as_of_hash does not match canonical as-of")
        result["as_of_hash"] = expected
        return result


def coerce_as_of(value: AsOf | int | str) -> AsOf:
    """Convert a raw selector to the single F5 as-of DTO."""

    if isinstance(value, AsOf):
        return value
    try:
        return AsOf(value=value)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, AsOfError):
            raise
        raise AsOfError("invalid as_of value") from exc


def compare_as_of(left: AsOf | int | str, right: AsOf | int | str) -> int:
    """Compare two as-of values, rejecting incomparable representations."""

    left_kind, left_value = _parse_as_of(coerce_as_of(left).value)
    right_kind, right_value = _parse_as_of(coerce_as_of(right).value)
    if left_kind != right_kind:
        raise AsOfError("integer and timestamp as_of values are inconsistent")
    return (left_value > right_value) - (left_value < right_value)


def validity_contains(validity: Validity, as_of: AsOf | int | str) -> bool:
    """Return whether an active validity interval contains ``as_of``.

    Intervals are half-open: ``[valid_from, valid_to)``.  Missing endpoints
    are unbounded.  A non-active validity is never considered usable.
    """

    if not isinstance(validity, Validity):
        raise AsOfError("validity must be a Validity contract")
    if validity.status.upper() != "ACTIVE":
        return False
    selected = coerce_as_of(as_of)
    if validity.valid_from is not None and compare_as_of(selected, validity.valid_from) < 0:
        return False
    if validity.valid_to is not None and compare_as_of(selected, validity.valid_to) >= 0:
        return False
    return True


def require_validity(validity: Validity, as_of: AsOf | int | str) -> None:
    """Raise a stable selector error when a validity interval cannot be used."""

    if not validity_contains(validity, as_of):
        raise AsOfError("as_of is outside the active validity interval", code="STALE_VERSION")


def canonical_as_of(value: AsOf | int | str) -> int | str:
    return coerce_as_of(value).value


__all__ = [
    "AsOf",
    "AsOfError",
    "canonical_as_of",
    "coerce_as_of",
    "compare_as_of",
    "require_validity",
    "validity_contains",
]
