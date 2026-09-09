"""Decision and promotion receipts; approval is never inferred from payload."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .contracts import Actor, ApprovalRef, ContractModel, _hash, _non_empty, _stable_hash, _token


class DecisionAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    QUARANTINE = "QUARANTINE"
    REQUEST_ADAPTATION = "REQUEST_ADAPTATION"
    DISPUTE = "DISPUTE"


class Decision(ContractModel):
    decision_id: str
    candidate_id: str
    evaluation_ref: str
    action: DecisionAction
    actor: Actor
    reason: str | None = None
    decision_hash: str = ""

    @field_validator("decision_id", "candidate_id", "evaluation_ref")
    @classmethod
    def validate_decision_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return None if value is None else _non_empty(value, "reason")

    @model_validator(mode="before")
    @classmethod
    def calculate_decision_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("decision_id", "candidate_id", "evaluation_ref", "action", "actor", "reason")}
        expected = _stable_hash(payload)
        supplied = result.get("decision_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("decision_hash does not match canonical decision")
        result["decision_hash"] = expected
        return result


class PromotionReceipt(ContractModel):
    promotion_id: str
    candidate_id: str
    approval_ref: ApprovalRef
    expected_head: str
    new_knowledge_version: str
    promoted_refs: tuple[str, ...] = ()
    projection_task_refs: tuple[str, ...] = ()
    idempotent_replay: bool = False
    receipt_hash: str = ""

    @field_validator("promotion_id", "candidate_id", "expected_head", "new_knowledge_version")
    @classmethod
    def validate_promotion_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("promoted_refs", "projection_task_refs")
    @classmethod
    def validate_promotion_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "promotion_ref") for item in value)

    @model_validator(mode="before")
    @classmethod
    def calculate_receipt_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("promoted_refs", ())
        result.setdefault("projection_task_refs", ())
        result.setdefault("idempotent_replay", False)
        payload = {key: result.get(key) for key in ("promotion_id", "candidate_id", "approval_ref", "expected_head", "new_knowledge_version", "promoted_refs", "projection_task_refs", "idempotent_replay")}
        expected = _stable_hash(payload)
        supplied = result.get("receipt_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("receipt_hash does not match canonical promotion receipt")
        result["receipt_hash"] = expected
        return result


__all__ = ["Decision", "DecisionAction", "PromotionReceipt"]
