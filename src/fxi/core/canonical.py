"""Stable serialization and hashing shared by evidence and candidate records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .exceptions import ValidationError


def canonical_json(value: Any) -> str:
    """Serialize JSON-compatible data deterministically.

    ``allow_nan=False`` prevents non-portable NaN/Infinity values from becoming
    different hashes across runtimes.
    """

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"值无法进行稳定序列化: {type(exc).__name__}") from exc


def sha256_hex(value: Any) -> str:
    """Return a SHA-256 hex digest for bytes, text, or JSON-compatible data."""

    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["canonical_json", "sha256_hex"]
