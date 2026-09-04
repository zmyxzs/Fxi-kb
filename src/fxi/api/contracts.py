"""
fxi.api.contracts - 本地 REST API 请求与响应强类型契约 (与 novel-Skill 对齐)
"""

from typing import Any, Optional
from pydantic import BaseModel, Field
from fxi.core.types import CausalStatus, SceneType


class ContextAssembleRequest(BaseModel):
    work_id: str
    scene_type: SceneType = SceneType.COMBAT
    pov_character_id: str
    current_narrative_order: int = 1
    query: Optional[str] = None
    territory_id: Optional[str] = None
    skill_slug: Optional[str] = None
    budget: int = Field(default=3500, ge=1000, le=8000)


class ContextAssembleResponse(BaseModel):
    work_id: str
    pov_character_id: str
    assembled_context: str
    approx_tokens: int


class CanonCheckRequest(BaseModel):
    work_id: str
    canon_work_id: str
    intended_canon_event_id: str


class CanonCheckResponse(BaseModel):
    status: CausalStatus
    is_safe: bool
    diagnostic_reason: str
    suggestions: list[str]


class RippleQueryRequest(BaseModel):
    work_id: str
    canon_work_id: str
    canon_event_id: str
    fanfic_event_id: str
    fanfic_summary: str
    narrative_order: int


class RippleQueryResponse(BaseModel):
    work_id: str
    divergence_canon_event_id: str
    invalidated_events: list[dict[str, Any]]
    mutated_events: list[dict[str, Any]]
    suggested_alternatives: list[str]


class StateQueryRequest(BaseModel):
    work_id: str
    entity_id: str
    metric_id: str
    narrative_order: int


class StateQueryResponse(BaseModel):
    work_id: str
    entity_id: str
    metric_id: str
    computed_value: float
    status: str
    narrative_order: int


class OOCCheckRequest(BaseModel):
    work_id: str
    draft_text: str
    narrative_order: int
    speaker_id: Optional[str] = None
    unrevealed_secrets: Optional[dict[str, str]] = None


class OOCCheckResponse(BaseModel):
    has_violations: bool
    violations: list[dict[str, Any]]
