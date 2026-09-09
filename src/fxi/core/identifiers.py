"""Shared validation for identifiers that cross filesystem or SQL boundaries."""

from __future__ import annotations

import re
from typing import Any

from .exceptions import ValidationError


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_identifier(value: Any, label: str = "identifier") -> str:
    """Validate one path/SQL identifier without normalizing or creating it."""

    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value) or value in {".", ".."}:
        raise ValidationError(f"{label} 必须是安全的非空标识符")
    return value


def validate_work_id(value: Any) -> str:
    """Validate an explicit work scope."""

    return validate_identifier(value, "work_id")


def validate_segment(value: Any, label: str = "segment") -> str:
    """Validate a single path segment such as source/version/document ID."""

    return validate_identifier(value, label)


__all__ = ["validate_identifier", "validate_work_id", "validate_segment"]
