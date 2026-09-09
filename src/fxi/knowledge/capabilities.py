"""Capability contracts and explicit unsupported-feature failures."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from pydantic import Field
from fxi.core.exceptions import FxiError

from .contracts import CapabilityManifest, ContractModel, ErrorCode, SCHEMA_HASH, SCHEMA_VERSION, CONTRACT_REVISION


class CapabilityError(FxiError):
    def __init__(self, message: str, code: str = ErrorCode.CAPABILITY_UNSUPPORTED.value):
        super().__init__(message, code=code)


class Capability(ContractModel):
    capability_id: str
    status: str
    version: str
    details: Mapping[str, Any] = Field(default_factory=dict)


class CapabilityProvider(Protocol):
    capability_id: str

    def capabilities(self) -> Capability: ...


def unsupported(capability_id: str) -> CapabilityError:
    return CapabilityError(f"capability is not implemented: {capability_id}")


__all__ = [
    "CONTRACT_REVISION",
    "SCHEMA_HASH",
    "SCHEMA_VERSION",
    "Capability",
    "CapabilityError",
    "CapabilityManifest",
    "CapabilityProvider",
    "unsupported",
]
