"""Strict HTTP value objects for the Fxi/Studio v3 boundary.

The knowledge services deliberately use their own domain-neutral models.  This
module is the small wire adapter at the HTTP edge: it accepts the public
Studio vocabulary and leaves actor identity, approval records, and business
state to the authenticated router and the v3 services.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator

from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import (
    CONTRACT_REVISION,
    ERROR_CODE_MAP,
    PUBLIC_ERROR_CODES,
    SCHEMA_HASH,
    SCHEMA_VERSION,
    _hash,
    _non_empty,
    _token,
)


def _wire_token(value: str, label: str) -> str:
    return _token(value, label)


def _wire_text(value: str, label: str) -> str:
    return _non_empty(value, label)


def _wire_hash(value: str, label: str) -> str:
    return _hash(value, label)


class V3WireModel:
    """Mixin marker used to keep the public models easy to discover."""


from pydantic import BaseModel  # noqa: E402  (keeps the imports grouped by purpose)


class StrictWireModel(BaseModel, V3WireModel):
    """Every v3 HTTP model rejects undeclared fields."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class EvidenceRefV3(StrictWireModel):
    """Studio-compatible evidence coordinates.

    ``start``/``end`` are accepted only as input aliases for older Fxi-side
    callers; serialized v3 responses always use the Studio names.
    """

    source_id: str
    source_version: str
    document_id: str
    start_char: int = Field(ge=0, validation_alias=AliasChoices("start_char", "start"))
    end_char: int = Field(gt=0, validation_alias=AliasChoices("end_char", "end"))
    excerpt_hash: str

    _ids = field_validator("source_id", "source_version", "document_id")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _hash = field_validator("excerpt_hash")(
        lambda value: _wire_hash(value, "excerpt_hash")
    )

    @model_validator(mode="after")
    def validate_range(self) -> "EvidenceRefV3":
        if self.end_char <= self.start_char:
            raise ValueError("evidence uses a non-empty half-open [start_char, end_char) range")
        return self


class StoryCoordinateV3(StrictWireModel):
    """Public narrative coordinate shared with Studio."""

    schema_version: Literal["story-coordinate.v3"] = "story-coordinate.v3"
    work_id: str
    source_id: str | None = None
    source_version: str | None = None
    branch_id: str
    as_of: int | str
    chapter_index: int = Field(ge=1)
    scene_index: int | None = Field(default=None, ge=1)
    narrative_order: int = Field(ge=1)
    pov_id: str | None = None
    knowledge_version: str
    reader_state_hash: str | None = None
    handoff_hash: str | None = None

    _ids = field_validator("work_id", "branch_id", "knowledge_version")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _optional_ids = field_validator("source_id", "source_version", "pov_id")(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _hashes = field_validator("reader_state_hash", "handoff_hash")(
        lambda value, info: None if value is None else _wire_hash(value, info.field_name)
    )

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: int | str) -> int | str:
        if isinstance(value, bool):
            raise ValueError("as_of cannot be boolean")
        if isinstance(value, int):
            if value < 0:
                raise ValueError("as_of cannot be negative")
            return value
        return _wire_text(value, "as_of")

    @model_validator(mode="after")
    def validate_source_pair(self) -> "StoryCoordinateV3":
        if (self.source_id is None) != (self.source_version is None):
            raise ValueError("source_id and source_version must be provided together")
        return self


class ProjectCreateRequestV3(StrictWireModel):
    work_id: str
    slug: str | None = None
    title: str | None = None
    owner_id: str | None = None
    content_origin: str = "original"
    source_dir: str | None = None
    skill_root: str | None = None
    profile: Mapping[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None

    _ids = field_validator("work_id", "slug", "owner_id")(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _text = field_validator("title", "content_origin", "source_dir", "skill_root")(
        lambda value, info: None if value is None else _wire_text(value, info.field_name)
    )
    _idempotency = field_validator("idempotency_key")(
        lambda value: None if value is None else _wire_token(value, "idempotency_key")
    )


class SourceBindingRequestV3(StrictWireModel):
    work_id: str
    source_id: str
    role: str
    branch_id: str
    priority: int = Field(default=0, ge=0)
    license: str
    access: str
    allowed_purposes: tuple[str, ...] = ()
    sync_direction: str = "pull"
    binding_id: str | None = None
    source_dir: str | None = None
    source_version: str | None = None
    idempotency_key: str | None = None

    _ids = field_validator("work_id", "source_id", "branch_id", "binding_id")(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _text = field_validator("role", "license", "access", "sync_direction", "source_dir", "source_version")(
        lambda value, info: None if value is None else _wire_text(value, info.field_name)
    )
    _purposes = field_validator("allowed_purposes")(
        lambda values: tuple(_wire_text(value, "allowed_purpose") for value in values)
    )
    _idempotency = field_validator("idempotency_key")(
        lambda value: None if value is None else _wire_token(value, "idempotency_key")
    )


class SourceSnapshotRequestV3(StrictWireModel):
    coordinate: StoryCoordinateV3
    source_id: str
    source_version: str
    idempotency_key: str | None = None

    _ids = field_validator("source_id", "source_version")(
        lambda value, info: _wire_token(value, info.field_name)
    )

    @model_validator(mode="after")
    def validate_coordinate_binding(self) -> "SourceSnapshotRequestV3":
        if self.coordinate.source_id and self.coordinate.source_id != self.source_id:
            raise ValueError("source_id does not match coordinate")
        if self.coordinate.source_version and self.coordinate.source_version != self.source_version:
            raise ValueError("source_version does not match coordinate")
        return self


class LearningArtifactEnvelopeV3(StrictWireModel):
    """Candidate-only envelope; actor and approval are never body fields."""

    schema_version: Literal["learning-artifact.v3"] = "learning-artifact.v3"
    artifact_id: str
    artifact_kind: str
    status: Literal["CANDIDATE", "EVALUATED", "APPROVED", "REJECTED", "INCOMPLETE"] = "CANDIDATE"
    work_id: str
    source_snapshot_ref: str
    evidence_refs: tuple[EvidenceRefV3, ...] = ()
    input_hash: str
    extractor_id: str
    domain_package_version: str
    model_route: str | None = None
    prompt_hash: str | None = None
    policy_hash: str
    budget_ref: str | None = None
    payload: Mapping[str, Any]
    payload_hash: str
    evaluation: Mapping[str, Any] | None = None

    _ids = field_validator(
        "artifact_id", "artifact_kind", "work_id", "source_snapshot_ref",
        "extractor_id", "domain_package_version", "budget_ref",
    )(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _hashes = field_validator("input_hash", "prompt_hash", "policy_hash", "payload_hash")(
        lambda value, info: None if value is None else _wire_hash(value, info.field_name)
    )

    @model_validator(mode="after")
    def validate_candidate(self) -> "LearningArtifactEnvelopeV3":
        if self.status != "CANDIDATE":
            raise ValueError("candidate submission must use status=CANDIDATE")
        if not self.evidence_refs:
            raise ValueError("candidate requires evidence_refs")
        if sha256_hex(self.payload) != self.payload_hash:
            raise ValueError("payload_hash does not match payload")
        return self


class CandidateSubmitRequestV3(LearningArtifactEnvelopeV3):
    """Named alias for OpenAPI and callers that prefer an operation name."""


class EvaluationPolicyV3(StrictWireModel):
    policy_id: str
    policy_hash: str
    evaluator_id: str
    evaluator_version: str
    allow_model_assistance: bool = False
    require_semantic_reviewer: bool = True
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)

    _ids = field_validator("policy_id", "evaluator_id", "evaluator_version")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _hash = field_validator("policy_hash")(
        lambda value: _wire_hash(value, "policy_hash")
    )


class EvaluationRequestV3(StrictWireModel):
    candidate_id: str = Field(validation_alias=AliasChoices("candidate_id", "artifact_id"))
    policy: EvaluationPolicyV3
    idempotency_key: str | None = None

    _candidate = field_validator("candidate_id")(
        lambda value: _wire_token(value, "candidate_id")
    )
    _idempotency = field_validator("idempotency_key")(
        lambda value: None if value is None else _wire_token(value, "idempotency_key")
    )


class DecisionRequestV3(StrictWireModel):
    candidate_id: str = Field(validation_alias=AliasChoices("candidate_id", "artifact_id"))
    action: Literal["APPROVE", "REJECT", "QUARANTINE", "REQUEST_ADAPTATION", "DISPUTE"]
    reason: str | None = None
    evaluation_id: str | None = None
    idempotency_key: str | None = None

    _candidate = field_validator("candidate_id")(
        lambda value: _wire_token(value, "candidate_id")
    )
    _ids = field_validator("evaluation_id", "idempotency_key")(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _reason = field_validator("reason")(
        lambda value: None if value is None else _wire_text(value, "reason")
    )


class PromotionRequestV3(StrictWireModel):
    candidate_id: str = Field(validation_alias=AliasChoices("candidate_id", "artifact_id"))
    approval_id: str
    approval_hash: str
    expected_head: str = Field(validation_alias=AliasChoices("expected_head", "expected_version"))
    idempotency_key: str | None = None

    _ids = field_validator("candidate_id", "approval_id")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _hashes = field_validator("approval_hash")(
        lambda value: _wire_hash(value, "approval_hash")
    )
    _head = field_validator("expected_head")(
        lambda value: _wire_text(value, "expected_head")
    )
    _idempotency = field_validator("idempotency_key")(
        lambda value: None if value is None else _wire_token(value, "idempotency_key")
    )


class ContextViewRequestV3(StrictWireModel):
    coordinate: StoryCoordinateV3
    purpose: str
    required_capabilities: tuple[str, ...] = ()
    budget: int = Field(gt=0)
    type_uris: tuple[str, ...] = ()
    object_refs: tuple[str, ...] = ()
    required_claim_refs: tuple[str, ...] = ()
    required_evidence_refs: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    pov_id: str | None = None
    source_composite: Mapping[str, Any] | None = None
    knowledge_head: Mapping[str, Any] | None = None
    include_methods: bool = False
    include_expression_features: bool = False

    _purpose = field_validator("purpose")(
        lambda value: _wire_text(value, "purpose")
    )
    _refs = field_validator(
        "required_capabilities", "type_uris", "object_refs", "required_claim_refs",
        "required_evidence_refs", "source_ids",
    )(
        lambda values: tuple(_wire_token(value, "context_ref") for value in values)
    )
    _pov = field_validator("pov_id")(
        lambda value: None if value is None else _wire_token(value, "pov_id")
    )


class QueryRequestV3(StrictWireModel):
    coordinate: StoryCoordinateV3
    query: str
    budget: int = Field(gt=0)
    purpose: str = "fts"
    capabilities: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    type_uris: tuple[str, ...] = ()
    pov_id: str | None = None

    _query = field_validator("query", "purpose")(
        lambda value, info: _wire_text(value, info.field_name)
    )
    _refs = field_validator("capabilities", "source_ids", "type_uris")(
        lambda values: tuple(_wire_token(value, "query_ref") for value in values)
    )
    _pov = field_validator("pov_id")(
        lambda value: None if value is None else _wire_token(value, "pov_id")
    )


class ReviewRequestV3(StrictWireModel):
    draft_ref: str
    draft_hash: str
    coordinate: StoryCoordinateV3
    context_hash: str | None = None
    context_view: Mapping[str, Any] | None = None
    knowledge_head: Mapping[str, Any] | None = None
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    source_composite: Mapping[str, Any] | None = None
    checks: tuple[Mapping[str, Any], ...] = ()
    required_checks: tuple[str, ...] = ()
    semantic_reviewer_id: str | None = None
    semantic_reviewer_version: str | None = None
    review_id: str | None = None

    _refs = field_validator(
        "draft_ref", "source_snapshot_ref", "source_id", "source_version",
        "semantic_reviewer_id", "semantic_reviewer_version", "review_id",
    )(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _hashes = field_validator("draft_hash", "context_hash")(
        lambda value, info: None if value is None else _wire_hash(value, info.field_name)
    )
    _checks = field_validator("required_checks")(
        lambda values: tuple(_wire_token(value, "required_check") for value in values)
    )


class ProposalRequestV3(StrictWireModel):
    draft_ref: str
    draft_hash: str
    review: Mapping[str, Any]
    state_change_set: Mapping[str, Any]
    context_view: Mapping[str, Any]
    knowledge_head: Mapping[str, Any]
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    source_composite: Mapping[str, Any] | None = None
    proposal_id: str | None = None
    idempotency_key: str | None = None

    _refs = field_validator(
        "draft_ref", "source_snapshot_ref", "source_id", "source_version", "proposal_id", "idempotency_key",
    )(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _hash = field_validator("draft_hash")(
        lambda value: _wire_hash(value, "draft_hash")
    )


class ApprovalRequestV3(StrictWireModel):
    proposal_id: str
    proposal_hash: str | None = None
    work_id: str | None = None
    branch_id: str | None = None
    expected_version: str | None = None
    approved_at: int | None = Field(default=None, ge=0)
    expires_at: int | str | None = None
    approval_id: str | None = None
    approval_hash: str | None = None
    idempotency_key: str | None = None

    _ids = field_validator(
        "proposal_id", "work_id", "branch_id", "expected_version", "approval_id", "idempotency_key",
    )(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _hashes = field_validator("proposal_hash", "approval_hash")(
        lambda value, info: None if value is None else _wire_hash(value, info.field_name)
    )

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, bool):
            raise ValueError("expires_at cannot be boolean")
        if value is not None and isinstance(value, str):
            return _wire_text(value, "expires_at")
        return value


class CommitRequestV3(StrictWireModel):
    proposal_id: str
    proposal_hash: str
    approval_id: str
    approval_hash: str
    expected_knowledge_version: str
    chapter_version: str
    idempotency_key: str
    work_id: str | None = None
    branch_id: str | None = None
    source_snapshot_ref: str | None = None
    source_id: str | None = None
    source_version: str | None = None
    draft_ref: str | None = None
    draft_hash: str | None = None
    chapter: Mapping[str, Any] = Field(default_factory=dict)
    projection_kinds: tuple[str, ...] = ("fts",)
    knowledge_head: Mapping[str, Any] | None = None
    source_composite: Mapping[str, Any] | None = None
    context_view: Mapping[str, Any] | None = None
    review: Mapping[str, Any] | None = None
    state_change_set: Mapping[str, Any] | None = None
    commit_id: str | None = None

    _ids = field_validator(
        "proposal_id", "approval_id", "expected_knowledge_version", "chapter_version", "idempotency_key",
        "work_id", "branch_id", "source_snapshot_ref", "source_id", "source_version", "draft_ref", "commit_id",
    )(
        lambda value, info: None if value is None else _wire_token(value, info.field_name)
    )
    _hashes = field_validator("proposal_hash", "approval_hash", "draft_hash")(
        lambda value, info: None if value is None else _wire_hash(value, info.field_name)
    )
    _projection_kinds = field_validator("projection_kinds")(
        lambda values: tuple(_wire_token(value, "projection_kind") for value in values)
    )


class ProjectionRebuildRequestV3(StrictWireModel):
    work_id: str
    branch_id: str
    knowledge_version: str
    projection_ids: tuple[str, ...] = ("fts",)
    context_view: Mapping[str, Any] | None = None

    _ids = field_validator("work_id", "branch_id", "knowledge_version")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _projections = field_validator("projection_ids")(
        lambda values: tuple(_wire_token(value, "projection_id") for value in values)
    )


class V3Response(StrictWireModel):
    """Stable success/error envelope used by every v3 endpoint."""

    code: str = "OK"
    message: str = "OK"
    trace_id: str
    result_hash: str = ""
    dependency_versions: Mapping[str, str] = Field(default_factory=dict)
    retryable: bool = False
    result: Any = None

    _code = field_validator("code", "trace_id")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _message = field_validator("message")(
        lambda value: _wire_text(value, "message")
    )

    @model_validator(mode="before")
    @classmethod
    def calculate_result_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        supplied = result.get("result_hash")
        expected = sha256_hex(result.get("result"))
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("result_hash does not match result")
        result["result_hash"] = expected
        return result

    @field_validator("result_hash")
    @classmethod
    def validate_result_digest(cls, value: str) -> str:
        return _wire_hash(value, "result_hash")


class ProjectResultV3(StrictWireModel):
    work_id: str
    owner_id: str
    slug: str
    title: str


class OperationResultV3(StrictWireModel):
    operation_id: str
    kind: str
    status: str
    idempotency_key: str
    request_hash: str
    result_hash: str | None = None
    code: str = "OK"
    message: str = "OK"

    _ids = field_validator("operation_id", "kind", "status", "idempotency_key", "code")(
        lambda value, info: _wire_token(value, info.field_name)
    )
    _hashes = field_validator("request_hash", "result_hash")(
        lambda value, info: None if value is None else _wire_hash(value, info.field_name)
    )


class ReadinessResultV3(StrictWireModel):
    status: Literal["READY", "NOT_READY", "DEGRADED"]
    liveness: bool
    contract_revision: str = CONTRACT_REVISION
    schema_hash: str = SCHEMA_HASH
    checks: Mapping[str, Any] = Field(default_factory=dict)
    projections: Mapping[str, Any] = Field(default_factory=dict)

    _revision = field_validator("contract_revision")(
        lambda value: _wire_text(value, "contract_revision")
    )
    _schema = field_validator("schema_hash")(
        lambda value: _wire_hash(value, "schema_hash")
    )


__all__ = [
    "ApprovalRequestV3",
    "CandidateSubmitRequestV3",
    "CommitRequestV3",
    "ContextViewRequestV3",
    "DecisionRequestV3",
    "ERROR_CODE_MAP",
    "EvidenceRefV3",
    "EvaluationPolicyV3",
    "EvaluationRequestV3",
    "LearningArtifactEnvelopeV3",
    "OperationResultV3",
    "ProjectCreateRequestV3",
    "ProjectResultV3",
    "ProjectionRebuildRequestV3",
    "ProposalRequestV3",
    "PromotionRequestV3",
    "PUBLIC_ERROR_CODES",
    "QueryRequestV3",
    "ReadinessResultV3",
    "ReviewRequestV3",
    "SCHEMA_HASH",
    "SCHEMA_VERSION",
    "SourceBindingRequestV3",
    "SourceSnapshotRequestV3",
    "StoryCoordinateV3",
    "StrictWireModel",
    "V3Response",
    "CONTRACT_REVISION",
]
