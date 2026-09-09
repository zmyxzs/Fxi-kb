"""First-class evaluation manifests for candidate governance."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .contracts import ContractModel, _hash, _non_empty, _stable_hash, _token


class Suitability(str, Enum):
    SUITABLE = "SUITABLE"
    ADAPT_REQUIRED = "ADAPT_REQUIRED"
    UNSUITABLE = "UNSUITABLE"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


class EvaluationManifest(ContractModel):
    evaluation_id: str
    candidate_id: str
    evaluator_id: str
    evaluator_version: str
    input_hash: str
    evidence_coverage: Mapping[str, Any] = Field(default_factory=dict)
    duplicate_cluster_ref: str | None = None
    same_core: bool = False
    variant_of: str | None = None
    surface_similarity_risk: float | None = Field(default=None, ge=0, le=1)
    suitability: Suitability = Suitability.UNKNOWN
    required_adaptation: tuple[str, ...] = ()
    uncertainty: Mapping[str, Any] = Field(default_factory=dict)
    conflicts: tuple[str, ...] = ()
    semantic_reviewer: str | None = None
    result_hash: str = ""
    status: str = "EVALUATED"

    @field_validator("evaluation_id", "candidate_id", "evaluator_id", "evaluator_version")
    @classmethod
    def validate_evaluation_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("input_hash")
    @classmethod
    def validate_evaluation_input_hash(cls, value: str) -> str:
        return _hash(value, "input_hash")

    @field_validator("duplicate_cluster_ref", "variant_of")
    @classmethod
    def validate_optional_evaluation_refs(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @field_validator("required_adaptation", "conflicts")
    @classmethod
    def validate_evaluation_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item, "evaluation_item") for item in value)

    @field_validator("semantic_reviewer")
    @classmethod
    def validate_reviewer(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "semantic_reviewer")

    @model_validator(mode="before")
    @classmethod
    def calculate_result_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("evaluation_id", "candidate_id", "evaluator_id", "evaluator_version", "input_hash", "evidence_coverage", "duplicate_cluster_ref", "same_core", "variant_of", "surface_similarity_risk", "suitability", "required_adaptation", "uncertainty", "conflicts", "semantic_reviewer", "status")}
        expected = _stable_hash(payload)
        supplied = result.get("result_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("result_hash does not match canonical evaluation")
        result["result_hash"] = expected
        return result


__all__ = ["EvaluationManifest", "Suitability"]
