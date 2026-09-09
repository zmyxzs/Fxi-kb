"""
fxi.api.contracts - 本地 REST API 请求与响应强类型契约 (与 novel-Skill 对齐)
"""

from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator
from fxi.core.types import CausalStatus, SceneType


class ModelRouteRequest(BaseModel):
    """Select one configured model route without accepting credentials."""

    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(default="scene_drafting", min_length=1, max_length=100)
    provider_override: Optional[str] = Field(default=None, min_length=1, max_length=100)
    model_override: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ModelHealthResponse(BaseModel):
    healthy: bool


class ModelChatRequest(ModelRouteRequest):
    system_prompt: Optional[str] = Field(default=None, max_length=2_000_000)
    user_prompt: Optional[str] = Field(default=None, max_length=2_000_000)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=1, le=131_072)

    @model_validator(mode="after")
    def require_prompt(self) -> "ModelChatRequest":
        if not any(
            isinstance(value, str) and value.strip()
            for value in (self.system_prompt, self.user_prompt)
        ):
            raise ValueError("system_prompt or user_prompt must be non-empty")
        return self


class ModelChatResponse(BaseModel):
    content: str = Field(min_length=1)


class ContextAssembleRequest(BaseModel):
    work_id: str
    scene_type: str = "combat"
    pov_character_id: str
    current_narrative_order: int = 1
    query: Optional[str] = None
    territory_id: Optional[str] = None
    skill_slug: Optional[str] = None
    budget: int = Field(default=3500, ge=1000, le=8000)
    characters: Optional[list[str]] = None
    props: Optional[list[str]] = None
    events: Optional[list[str]] = None


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
    computed_value: Optional[float] = 0.0
    status: str
    narrative_order: int


class OOCCheckRequest(BaseModel):
    work_id: str
    draft_text: str
    narrative_order: int = 1
    speaker_id: Optional[str] = None
    characters: Optional[list[str]] = None
    unrevealed_secrets: Optional[dict[str, str]] = None


class OOCCheckResponse(BaseModel):
    has_violations: bool
    violations: list[dict[str, Any]]


class ContinuityCheckRequest(BaseModel):
    work_id: str
    chapter_index: int
    draft_text: str
    planned_events: Optional[list[str]] = None


class ContinuityCheckResponse(BaseModel):
    is_valid: bool
    violations: list[dict[str, Any]]


class AskQueryRequest(BaseModel):
    work_id: str
    question: str
    top_k_scenes: int = Field(default=5, ge=1, le=20)
    source_id: Optional[str] = Field(default=None, min_length=1)
    source_version: Optional[str] = Field(default=None, min_length=1)
    knowledge_version: Optional[str] = Field(default=None, min_length=1)
    narrative_order: Optional[int] = Field(default=None, ge=0)
    timeline_id: Optional[str] = Field(default=None, min_length=1)
    divergence_narrative_order: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_source_pair(self) -> "AskQueryRequest":
        if (self.source_id is None) != (self.source_version is None):
            raise ValueError("source_id 与 source_version 必须同时提供")
        return self


class AskQueryResponse(BaseModel):
    work_id: str
    question: str
    target_entities: list[str]
    keywords: list[str]
    intent: str = "general"
    signals: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    answer: str
    evidence: dict[str, Any]


class ContinuityRequest(BaseModel):
    work_id: str
    chapter_index: int


class ContinuityResponse(BaseModel):
    work_id: str
    chapter_index: int
    title: str = ""
    tail_snippet: str = ""
    ending_location: str = ""
    active_characters: list[str] = Field(default_factory=list)
    ending_situation: str = ""
    unresolved_hooks: list[str] = Field(default_factory=list)


class VoicesRequest(BaseModel):
    work_id: str
    characters: list[str]


class VoicesResponse(BaseModel):
    work_id: str
    voices: dict[str, Any] = Field(default_factory=dict)


class RelationshipsRequest(BaseModel):
    work_id: str
    characters: list[str]


class RelationshipsResponse(BaseModel):
    work_id: str
    relationships: list[dict[str, Any]] = Field(default_factory=list)


class StyleRequest(BaseModel):
    work_id: str
    scene_type: Optional[str] = None


class StyleResponse(BaseModel):
    work_id: str
    style_profile: dict[str, Any] = Field(default_factory=dict)


# /v2 contracts are deliberately separate from the legacy v1 payloads.  Every
# write-oriented request carries the immutable version/hash it is bound to.
StyleSelectionMode = Literal["none", "approved", "evaluation_candidate"]


class StyleSelectionDecisionV2(BaseModel):
    """One explicit qualification decision in a style selection view."""

    model_config = ConfigDict(extra="forbid")

    asset_kind: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    selected: bool
    reason: str = Field(min_length=1)
    asset: dict[str, Any] = Field(default_factory=dict)


class StyleSelectionViewV2(BaseModel):
    """Stable, public view over an immutable style package."""

    model_config = ConfigDict(extra="forbid")

    selection: StyleSelectionMode
    style_package_version: Optional[str] = None
    package_hash: Optional[str] = None
    view_hash: str = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    selected: list[StyleSelectionDecisionV2] = Field(default_factory=list)
    rejected: list[StyleSelectionDecisionV2] = Field(default_factory=list)


class SourceSnapshotRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    expected_source_version: str = Field(min_length=1)
    chapter_start: Optional[int] = Field(default=None, ge=1)
    chapter_end: Optional[int] = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=1)


class SourceDocumentV2(BaseModel):
    document_id: str
    chapter_index: int = Field(ge=1)
    relative_path: str
    content_hash: str
    char_count: int = Field(ge=0)


class SourceSnapshotResponseV2(BaseModel):
    work_id: str
    source_id: str
    version: str
    manifest_hash: str
    documents: list[SourceDocumentV2] = Field(default_factory=list)


class SourceDocumentResponseV2(BaseModel):
    work_id: str
    source_id: str
    source_version: str
    document_id: str
    content: str
    document_hash: str
    excerpt_hash: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)


class WritingContextRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    style_package_version: Optional[str] = Field(default=None, min_length=1)
    knowledge_version: Optional[str] = None
    mode: str = "original_in_world"
    purpose: str = "drafting"
    style_selection: StyleSelectionMode = "none"
    pov_character_id: str = ""
    current_narrative_order: int = Field(default=0, ge=0)
    scene_beat: Optional[dict[str, Any]] = None
    budget: int = Field(default=3000, ge=1, le=10000)
    objective: Optional[str] = None
    participants: Optional[list[str]] = None
    narrative_order: Optional[list[str]] = None
    chapter_beats: Optional[list[dict[str, Any]]] = None

    @property
    def selected_style_version(self) -> Optional[str]:
        return self.style_package_version

    @model_validator(mode="after")
    def validate_style_selection(self) -> "WritingContextRequestV2":
        if self.style_selection == "none" and self.style_package_version:
            raise ValueError("style_selection=none cannot include style_package_version")
        if self.style_selection != "none" and not self.style_package_version:
            raise ValueError("selected style context requires style_package_version")
        return self


class WritingContextResponseV2(BaseModel):
    assembled_context: str = ""
    work_id: str
    source_id: str
    source_version: str
    style_package_version: Optional[str] = None
    knowledge_version: str
    mode: str
    purpose: str
    style_selection: StyleSelectionMode
    package_hash: Optional[str] = None
    style_package_hash: Optional[str] = None
    style_view_hash: Optional[str] = None
    view_hash: str = ""
    selection_reason: str = ""
    style_view: Optional[StyleSelectionViewV2] = None
    facts: list[dict[str, Any]] = Field(default_factory=list)
    character_knowledge: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    planned_revelations: list[dict[str, Any]] = Field(default_factory=list)
    exact_text_rules: list[dict[str, Any]] = Field(default_factory=list)
    style_rules: list[dict[str, Any]] = Field(default_factory=list)
    style_examples: list[dict[str, Any]] = Field(default_factory=list)
    sacred_whitelist: list[str] = Field(default_factory=list)
    prompt_tax_limits: dict[str, int] = Field(
        default_factory=lambda: {"max_rules": 3, "max_examples": 1}
    )
    dialogue_inertia_spec: dict[str, Any] = Field(default_factory=dict)
    pruning_log: list[str] = Field(default_factory=list)
    completeness_status: Literal["COMPLETE", "INCOMPLETE"]
    missing_required: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)


class WritingReviewRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    knowledge_version: str = Field(min_length=1)
    style_package_version: Optional[str] = None
    mode: str = "original_in_world"
    scene_id: Optional[str] = None
    chapter_index: int = Field(ge=1)
    text: str = Field(min_length=1)
    text_hash: str = Field(min_length=1)
    plan_hash: str = Field(min_length=1)
    context_hash: str = Field(min_length=1)
    event_ids: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    sacred_whitelist: list[str] = Field(default_factory=list)


class ReviewCheckV2(BaseModel):
    check_id: str
    required: bool = True
    status: str
    checker_version: str = "fxi-review-v2"
    input_text_hash: str
    coverage_spans: list[tuple[int, int]] = Field(default_factory=list)
    event_results: list[dict[str, Any]] = Field(default_factory=list)
    action_density_ratio: Optional[float] = None
    sacred_whitelist_preserved: bool = True
    findings: list[dict[str, Any]] = Field(default_factory=list)
    error_code: Optional[str] = None


class ReviewReportV2(BaseModel):
    report_id: str
    chapter_id: str
    text_hash: str
    plan_hash: str
    context_hash: str
    knowledge_version: str
    checks: list[ReviewCheckV2] = Field(default_factory=list)
    overall_status: Literal["PASSED", "NEEDS_REVISION", "REJECTED", "INCOMPLETE"]
    blocking_findings: list[dict[str, Any]] = Field(default_factory=list)
    revision_count: int = Field(default=0, ge=0)
    previous_report_id: Optional[str] = None
    created_at: str


class StyleCandidateRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    package: dict[str, Any]
    package_hash: str = Field(min_length=1)
    learning_run_ref: Optional[str] = None
    evaluation_ref: Optional[str] = None
    idempotency_key: str = Field(min_length=1)


class StylePromotionRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    candidate_hash: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    expected_active_version: Optional[str] = None
    idempotency_key: str = Field(min_length=1)


class ApprovalRequestV2(BaseModel):
    work_id: Optional[str] = None
    action: Literal["style_promotion", "chapter_commit", "rollback"]
    target_id: str = Field(min_length=1)
    target_hash: str = Field(min_length=1)
    expected_version: str = Field(min_length=1)


class ApprovalResponseV2(BaseModel):
    approval_id: str
    action: str
    target_id: str
    target_hash: str
    expected_version: str
    source_id: Optional[str] = None
    source_version: Optional[str] = None
    candidate_version: Optional[str] = None
    evaluation_ref: Optional[str] = None
    validity: str
    consumed_by: Optional[str] = None
    created_at: str


class ChapterProposalRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    mode: str = "original_in_world"
    chapter_index: int = Field(ge=1)
    chapter_version: str = Field(min_length=1)
    text: str = Field(min_length=1)
    text_hash: str = Field(min_length=1)
    final_review_ref: str = Field(min_length=1)
    state_change_proposals: list[dict[str, Any]] = Field(default_factory=list)
    causal_events: list[dict[str, Any]] = Field(default_factory=list)
    character_knowledge: Union[dict[str, Any], list[dict[str, Any]]] = Field(default_factory=dict)
    dependency_versions: dict[str, Optional[str]] = Field(default_factory=dict)
    proposal_hash: str = Field(min_length=1)
    status: Literal["PENDING_CONFIRMATION"] = "PENDING_CONFIRMATION"
    idempotency_key: str = Field(min_length=1)


class ChapterCommitRequestV2(BaseModel):
    work_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    style_package_version: Optional[str] = None
    chapter_index: int = Field(ge=1)
    chapter_version: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_hash: str = Field(min_length=1)
    pending_text: str = Field(min_length=1)
    text_hash: str = Field(min_length=1)
    final_review_reference: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    accepted_state_change_ids: list[str] = Field(default_factory=list)
    expected_knowledge_version: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)


class CommitReceiptV2(BaseModel):
    commit_id: str
    new_knowledge_version: str
    chapter_version: str
    text_hash: str
    idempotent_replay: bool = False
