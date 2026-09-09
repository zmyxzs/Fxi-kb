"""Branch and as-of identity contracts."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .contracts import ContractModel, _non_empty, _stable_hash, _token
from .objects import KnowledgeHead


class AsOf(ContractModel):
    value: int | str
    source_version: str | None = None

    @field_validator("value")
    @classmethod
    def validate_as_of_value(cls, value: int | str) -> int | str:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("as_of must be a non-negative integer or non-empty string")
        if isinstance(value, str) and not value.strip():
            raise ValueError("as_of cannot be empty")
        return value

    @field_validator("source_version")
    @classmethod
    def validate_as_of_source(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "source_version")


class DivergenceAnchor(ContractModel):
    source_snapshot_ref: str
    narrative_order: int = Field(gt=0)
    reason: str
    anchor_hash: str = ""

    @field_validator("source_snapshot_ref")
    @classmethod
    def validate_anchor_ref(cls, value: str) -> str:
        return _token(value, "source_snapshot_ref")

    @field_validator("reason")
    @classmethod
    def validate_anchor_reason(cls, value: str) -> str:
        return _non_empty(value, "reason")

    @model_validator(mode="before")
    @classmethod
    def calculate_anchor_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("source_snapshot_ref", "narrative_order", "reason")}
        expected = _stable_hash(payload)
        supplied = result.get("anchor_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("anchor_hash does not match canonical divergence anchor")
        result["anchor_hash"] = expected
        return result


class BranchPolicy(ContractModel):
    inherit_static: bool = True
    filter_post_divergence: bool = True
    allow_reference_sources: bool = True


class Branch(ContractModel):
    branch_id: str
    work_id: str
    parent_branch: str | None = None
    divergence: DivergenceAnchor | None = None
    policy: BranchPolicy = Field(default_factory=BranchPolicy)
    created_by: str
    status: str = "ACTIVE"
    branch_hash: str = ""

    @field_validator("branch_id", "work_id", "created_by")
    @classmethod
    def validate_branch_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("parent_branch")
    @classmethod
    def validate_parent_branch(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "parent_branch")

    @field_validator("status")
    @classmethod
    def validate_branch_status(cls, value: str) -> str:
        return _non_empty(value, "status")

    @model_validator(mode="before")
    @classmethod
    def calculate_branch_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("parent_branch", None)
        result.setdefault("divergence", None)
        result.setdefault("policy", BranchPolicy().model_dump(mode="json"))
        result.setdefault("status", "ACTIVE")
        payload = {key: result.get(key) for key in ("branch_id", "work_id", "parent_branch", "divergence", "policy", "created_by", "status")}
        expected = _stable_hash(payload)
        supplied = result.get("branch_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("branch_hash does not match canonical branch")
        result["branch_hash"] = expected
        return result


__all__ = ["AsOf", "Branch", "BranchPolicy", "DivergenceAnchor", "KnowledgeHead"]
