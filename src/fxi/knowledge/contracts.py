"""Versioned, domain-neutral contracts for the Fxi v3 knowledge boundary.

The module intentionally knows nothing about a particular story domain.  Domain
types are registered by :class:`DomainPackage` implementations in
``fxi.knowledge.registry``.  The models in this file are the shared wire and
storage contracts consumed by F2--F10 and by Studio.
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fxi.core.canonical import canonical_json, sha256_hex


CONTRACT_REVISION = "studio-fxi-v3.20260909"
SCHEMA_VERSION = "knowledge-contract.v3"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,255}$")

# This is the cross-repository public schema fixture.  Studio hashes this
# field map (rather than private Python model details), so both repositories
# can verify the same wire contract without importing one another.
PUBLIC_CONTRACT_SCHEMA: Mapping[str, tuple[str, ...]] = {
    "StoryCoordinate": (
        "work_id", "source_id", "source_version", "branch_id", "as_of",
        "chapter_index", "scene_index", "narrative_order", "pov_id",
        "knowledge_version", "reader_state_hash", "handoff_hash",
    ),
    "ContextManifest": (
        "coordinate", "knowledge_version", "source_snapshot_refs", "fact_refs",
        "method_refs", "expression_refs", "forbidden_refs", "evidence_refs",
        "scope", "budget", "view_hash", "content_hash", "completeness",
    ),
    "LearningArtifactEnvelope": (
        "artifact_id", "artifact_kind", "status", "work_id", "source_snapshot_ref",
        "evidence_refs", "input_hash", "extractor_id", "domain_package_version",
        "model_route", "prompt_hash", "policy_hash", "budget_ref", "payload",
        "payload_hash",
    ),
    "NarrativePlan": (
        "coordinate", "intent", "scene_plans", "required_fact_refs",
        "allowed_invention_scopes", "selected_method_refs", "plan_hash",
    ),
    "GroundedChapterPlan": (
        "narrative_plan_ref", "coordinate", "context_view_ref", "permitted_claim_refs",
        "forbidden_claim_refs", "scenes", "plan_evidence_refs", "grounding_status",
        "plan_hash",
    ),
    "ProductionJob": (
        "job_id", "coordinate", "profile_ref", "context_manifest_ref",
        "narrative_plan_ref", "grounded_plan_ref", "run_id", "idempotency_key", "job_hash",
    ),
    "ReviewReport": (
        "report_id", "draft_ref", "draft_hash", "coordinate", "context_hash", "checks",
        "overall_status", "blocking_findings", "report_hash",
    ),
    "StateChangeSet": (
        "change_set_id", "coordinate", "source_artifact_hash", "changes", "review_ref",
        "change_set_hash",
    ),
    "Proposal": (
        "proposal_id", "draft_ref", "draft_hash", "review_ref", "state_change_set_ref",
        "context_hash", "knowledge_version", "status", "proposal_hash",
    ),
    "CommitReceipt": (
        "commit_id", "proposal_id", "new_knowledge_version", "chapter_version",
        "idempotent_replay",
    ),
}


class ErrorCode(str, Enum):
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_SCOPE = "INVALID_SCOPE"
    UNREGISTERED_TYPE = "UNREGISTERED_TYPE"
    NO_EVIDENCE = "NO_EVIDENCE"
    EVALUATION_REQUIRED = "EVALUATION_REQUIRED"
    CONFLICTING_ASSERTIONS = "CONFLICTING_ASSERTIONS"
    STALE_VERSION = "STALE_VERSION"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    KNOWLEDGE_INSUFFICIENT = "KNOWLEDGE_INSUFFICIENT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TOKEN_BUDGET_EXCEEDED = "TOKEN_BUDGET_EXCEEDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    CAS_CONFLICT = "CAS_CONFLICT"
    PROJECTION_STALE = "PROJECTION_STALE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    PROJECTION_FAILED = "PROJECTION_FAILED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INVALID_POLICY = "INVALID_POLICY"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    PLAN_INFEASIBLE = "PLAN_INFEASIBLE"
    UNRESOLVED_INTENT = "UNRESOLVED_INTENT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    CANCELLED = "CANCELLED"
    ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    MISSING_COMMIT_DEPENDENCY = "MISSING_COMMIT_DEPENDENCY"
    LEGACY_MIGRATION_REQUIRED = "LEGACY_MIGRATION_REQUIRED"
    INVALID_ORIGIN = "INVALID_ORIGIN"
    INVALID_HASH = "INVALID_HASH"


ERROR_CODE_MAP: Mapping[str, str] = {
    code.value: code.value for code in ErrorCode
}
ERROR_CODE_MAP = {
    **ERROR_CODE_MAP,
    ErrorCode.TOKEN_BUDGET_EXCEEDED.value: ErrorCode.BUDGET_EXCEEDED.value,
}

PUBLIC_ERROR_CODES: tuple[str, ...] = (
    "MISSING_CONTEXT", "VERSION_CONFLICT", "INVALID_POLICY", "UNSUPPORTED_CAPABILITY",
    "AUTHORIZATION_FAILED", "PLAN_INFEASIBLE", "UNRESOLVED_INTENT", "BUDGET_EXHAUSTED",
    "MODEL_UNAVAILABLE", "INVALID_OUTPUT", "CANCELLED", "ARTIFACT_CONFLICT",
    "UNKNOWN_OUTCOME", "MISSING_COMMIT_DEPENDENCY", "LEGACY_MIGRATION_REQUIRED",
    "INVALID_ORIGIN", "INVALID_SCOPE", "INVALID_HASH", "INVALID_SCHEMA", "NO_EVIDENCE",
    "CONFLICTING_ASSERTIONS", "KNOWLEDGE_INSUFFICIENT", "CAPABILITY_UNSUPPORTED",
    "BUDGET_EXCEEDED", "STALE_VERSION", "APPROVAL_REQUIRED", "APPROVAL_EXPIRED",
    "CAS_CONFLICT", "IDEMPOTENCY_CONFLICT", "PROJECTION_STALE", "EVALUATION_REQUIRED",
    "UNREGISTERED_TYPE",
)


class ContractModel(BaseModel):
    """Base model that rejects undeclared fields at every public boundary."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _non_empty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{label} must be a non-empty safe string")
    return value


def _token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{label} must be a safe identifier")
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _digest_mapping(value: Mapping[str, Any]) -> str:
    return _stable_hash(value)


def _jsonable(value: Any) -> Any:
    """Convert model/enum containers before canonical hashing."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _stable_hash(value: Any) -> str:
    return sha256_hex(_jsonable(value))


def _computed_hash(data: Mapping[str, Any], field: str, source: str) -> dict[str, Any]:
    result = dict(data)
    payload = result.get(source)
    if payload is None:
        raise ValueError(f"{source} is required to compute {field}")
    expected = _stable_hash(payload)
    supplied = result.get(field)
    if supplied not in (None, "") and supplied != expected:
        raise ValueError(f"{field} does not match canonical {source} hash")
    result[field] = expected
    return result


class Scope(ContractModel):
    work_id: str
    branch_id: str
    as_of: int | str
    purpose: str
    actor: str

    @field_validator("work_id", "branch_id")
    @classmethod
    def validate_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("purpose", "actor")
    @classmethod
    def validate_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: int | str) -> int | str:
        if isinstance(value, bool):
            raise ValueError("as_of cannot be boolean")
        if isinstance(value, int) and value < 0:
            raise ValueError("as_of cannot be negative")
        if isinstance(value, str) and not value.strip():
            raise ValueError("as_of cannot be empty")
        return value


class Validity(ContractModel):
    valid_from: int | str | None = None
    valid_to: int | str | None = None
    status: str = "ACTIVE"
    reason: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _non_empty(value, "status")

    @model_validator(mode="after")
    def validate_interval(self) -> "Validity":
        if isinstance(self.valid_from, int) and isinstance(self.valid_to, int):
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to must not precede valid_from")
        return self


class EvidenceRef(ContractModel):
    evidence_id: str | None = None
    source_snapshot_ref: str | None = None
    source_id: str
    source_version: str
    document_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt_hash: str
    normalization_version: str
    scope: Scope | None = None
    license: str | None = None

    @field_validator("evidence_id", "source_snapshot_ref", "source_id", "source_version", "document_id")
    @classmethod
    def validate_identifiers(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @field_validator("normalization_version")
    @classmethod
    def validate_normalization(cls, value: str) -> str:
        return _non_empty(value, "normalization_version")

    @field_validator("excerpt_hash")
    @classmethod
    def validate_excerpt_hash(cls, value: str) -> str:
        return _hash(value, "excerpt_hash")

    @model_validator(mode="after")
    def validate_range(self) -> "EvidenceRef":
        if self.end <= self.start:
            raise ValueError("EvidenceRef uses a non-empty half-open [start, end) range")
        return self

    @property
    def start_char(self) -> int:
        return self.start

    @property
    def end_char(self) -> int:
        return self.end


class Actor(ContractModel):
    actor_id: str
    role: str
    work_id: str | None = None
    scope: Scope | None = None

    @field_validator("actor_id", "role")
    @classmethod
    def validate_actor_fields(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @model_validator(mode="after")
    def bind_scope(self) -> "Actor":
        if self.scope is not None and self.scope.actor != self.actor_id:
            raise ValueError("actor_id must match scope.actor")
        if self.scope is not None and self.work_id is not None and self.scope.work_id != self.work_id:
            raise ValueError("work_id must match scope.work_id")
        return self


class SourceBindingRef(ContractModel):
    binding_id: str
    work_id: str
    source_id: str
    role: str
    priority: int = Field(default=0, ge=0)
    branch_id: str
    validity: Validity = Field(default_factory=Validity)
    license: str
    access: str
    allowed_purposes: tuple[str, ...] = ()
    sync_direction: str = "pull"

    @field_validator("binding_id", "work_id", "source_id", "branch_id")
    @classmethod
    def validate_binding_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("role", "license", "access", "sync_direction")
    @classmethod
    def validate_binding_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("allowed_purposes")
    @classmethod
    def validate_purposes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item, "allowed_purpose") for item in value)


class SourceSnapshotRef(ContractModel):
    snapshot_id: str
    work_id: str
    source_id: str
    source_version: str
    content_hash: str
    binding_id: str
    status: str = "PUBLISHED"
    document_refs: tuple[str, ...] = ()

    @field_validator("snapshot_id", "work_id", "source_id", "source_version", "binding_id")
    @classmethod
    def validate_snapshot_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("content_hash")
    @classmethod
    def validate_content_hash(cls, value: str) -> str:
        return _hash(value, "content_hash")

    @field_validator("status")
    @classmethod
    def validate_snapshot_status(cls, value: str) -> str:
        return _non_empty(value, "status")


class StoryCoordinate(ContractModel):
    work_id: str
    source_id: str | None = None
    source_version: str | None = None
    branch_id: str
    as_of: int | str
    chapter_index: int = Field(gt=0)
    scene_index: int | None = Field(default=None, ge=0)
    narrative_order: int = Field(gt=0)
    pov_id: str | None = None
    knowledge_version: str
    reader_state_hash: str | None = None
    handoff_hash: str | None = None

    @field_validator("work_id", "branch_id", "knowledge_version")
    @classmethod
    def validate_coordinate_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("source_id", "source_version", "pov_id")
    @classmethod
    def validate_optional_tokens(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @field_validator("reader_state_hash", "handoff_hash")
    @classmethod
    def validate_optional_hashes(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _hash(value, info.field_name)

    @field_validator("as_of")
    @classmethod
    def validate_coordinate_as_of(cls, value: int | str) -> int | str:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0):
            raise ValueError("as_of must be a non-negative integer or non-empty string")
        if isinstance(value, str) and not value.strip():
            raise ValueError("as_of cannot be empty")
        return value


class WorkProfile(ContractModel):
    work_id: str
    content_origin: str
    source_bindings: tuple[SourceBindingRef, ...] = ()
    branch_policy: Mapping[str, Any] = Field(default_factory=dict)
    learning_policy: Mapping[str, Any] = Field(default_factory=dict)
    artifact_policy: Mapping[str, Any] = Field(default_factory=dict)
    required_capabilities: tuple[str, ...] = ()
    allowed_invention_scopes: tuple[str, ...] = ()
    model_policy: Mapping[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @field_validator("work_id")
    @classmethod
    def validate_profile_work(cls, value: str) -> str:
        return _token(value, "work_id")

    @field_validator("content_origin", "schema_version")
    @classmethod
    def validate_profile_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class CandidateEnvelope(ContractModel):
    candidate_id: str
    artifact_kind: str
    status: Literal["CANDIDATE", "EVALUATED", "APPROVED", "REJECTED", "INCOMPLETE", "QUARANTINED"] = "CANDIDATE"
    work_id: str
    branch_id: str
    source_snapshot_ref: str
    evidence_refs: tuple[EvidenceRef, ...]
    input_hash: str
    extractor_id: str
    schema_version: str
    domain_package_version: str
    model_route: str | None = None
    prompt_hash: str | None = None
    policy_hash: str
    budget_ref: str | None = None
    payload: Mapping[str, Any]
    payload_hash: str = ""
    actor: Actor | None = None

    @field_validator("candidate_id", "artifact_kind", "work_id", "branch_id", "source_snapshot_ref", "extractor_id", "schema_version", "domain_package_version", "policy_hash")
    @classmethod
    def validate_candidate_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("input_hash", "prompt_hash")
    @classmethod
    def validate_candidate_hashes(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _hash(value, info.field_name)

    @field_validator("budget_ref")
    @classmethod
    def validate_budget_ref(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "budget_ref")

    @model_validator(mode="before")
    @classmethod
    def calculate_payload_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        return _computed_hash(data, "payload_hash", "payload")


class CandidateRef(ContractModel):
    candidate_id: str
    candidate_hash: str
    work_id: str
    branch_id: str
    status: str

    @field_validator("candidate_id", "work_id", "branch_id")
    @classmethod
    def validate_candidate_ref_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("candidate_hash")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return _hash(value, "candidate_hash")

    @field_validator("status")
    @classmethod
    def validate_candidate_ref_status(cls, value: str) -> str:
        return _non_empty(value, "status")


class EvaluationPolicy(ContractModel):
    policy_id: str
    policy_hash: str
    evaluator_id: str
    evaluator_version: str
    allow_model_assistance: bool = False
    require_semantic_reviewer: bool = True
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)

    @field_validator("policy_id", "evaluator_id", "evaluator_version")
    @classmethod
    def validate_policy_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("policy_hash")
    @classmethod
    def validate_policy_hash(cls, value: str) -> str:
        return _hash(value, "policy_hash")


class ViewSpec(ContractModel):
    purpose: str
    type_uris: tuple[str, ...] = ()
    object_refs: tuple[str, ...] = ()
    required_claim_refs: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    pov_id: str | None = None
    budget_tokens: int | None = Field(default=None, gt=0)
    include_methods: bool = False
    include_expression_features: bool = False

    @field_validator("purpose")
    @classmethod
    def validate_view_purpose(cls, value: str) -> str:
        return _non_empty(value, "purpose")

    @field_validator("type_uris", "object_refs", "required_claim_refs", "source_ids")
    @classmethod
    def validate_view_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "view_ref") for item in value)

    @field_validator("pov_id")
    @classmethod
    def validate_view_pov(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "pov_id")


class ContextBlock(ContractModel):
    block_id: str
    block_kind: Literal["FACT", "METHOD", "EXPRESSION", "FORBIDDEN", "DIAGNOSTIC"] = "FACT"
    type_uri: str
    object_refs: tuple[str, ...] = ()
    content: Any
    evidence_refs: tuple[EvidenceRef, ...] = ()
    scope: Scope
    block_hash: str = ""
    required: bool = False

    @field_validator("block_id", "type_uri")
    @classmethod
    def validate_block_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("object_refs")
    @classmethod
    def validate_block_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "object_ref") for item in value)

    @model_validator(mode="before")
    @classmethod
    def calculate_block_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {
            "block_id": result.get("block_id"),
            "block_kind": result.get("block_kind", "FACT"),
            "type_uri": result.get("type_uri"),
            "object_refs": result.get("object_refs", ()),
            "content": result.get("content"),
            "evidence_refs": result.get("evidence_refs", ()),
            "scope": result.get("scope"),
            "required": result.get("required", False),
        }
        expected = _stable_hash(payload)
        supplied = result.get("block_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("block_hash does not match canonical block content")
        result["block_hash"] = expected
        return result


class ContextManifest(ContractModel):
    fact_refs: tuple[str, ...] = ()
    method_refs: tuple[str, ...] = ()
    expression_refs: tuple[str, ...] = ()
    forbidden_refs: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    scope: Scope
    budget: Mapping[str, int] = Field(default_factory=dict)
    view_hash: str
    content_hash: str

    @field_validator("fact_refs", "method_refs", "expression_refs", "forbidden_refs")
    @classmethod
    def validate_manifest_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "manifest_ref") for item in value)

    @field_validator("view_hash", "content_hash")
    @classmethod
    def validate_manifest_hashes(cls, value: str, info: Any) -> str:
        return _hash(value, info.field_name)


class ContextView(ContractModel):
    view_id: str
    work_id: str
    branch_id: str
    as_of: int | str
    purpose: str
    scope: Scope
    blocks: tuple[ContextBlock, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    forbidden_refs: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    staleness: str = "FRESH"
    completeness: str = "COMPLETE"
    budget: Mapping[str, int] = Field(default_factory=dict)
    view_hash: str = ""
    content_hash: str = ""
    manifest: ContextManifest | None = None

    @field_validator("view_id", "work_id", "branch_id")
    @classmethod
    def validate_view_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("purpose", "staleness", "completeness")
    @classmethod
    def validate_view_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @model_validator(mode="after")
    def validate_view_scope_and_hashes(self) -> "ContextView":
        if self.scope.work_id != self.work_id or self.scope.branch_id != self.branch_id:
            raise ValueError("ContextView scope must match work_id and branch_id")
        view_payload = self.model_dump(mode="json", exclude={"view_hash", "content_hash"})
        expected_view_hash = _stable_hash(view_payload)
        expected_content_hash = _stable_hash({"blocks": [block.model_dump(mode="json") for block in self.blocks]})
        if self.view_hash not in ("", expected_view_hash):
            raise ValueError("view_hash does not match canonical view")
        if self.content_hash not in ("", expected_content_hash):
            raise ValueError("content_hash does not match canonical blocks")
        object.__setattr__(self, "view_hash", expected_view_hash)
        object.__setattr__(self, "content_hash", expected_content_hash)
        return self


class QueryRequest(ContractModel):
    work_id: str
    branch_id: str
    as_of: int | str
    purpose: str
    query: str
    source_ids: tuple[str, ...] = ()
    type_uris: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    pov_id: str | None = None
    budget_tokens: int | None = Field(default=None, gt=0)
    actor: Actor | None = None

    @field_validator("work_id", "branch_id")
    @classmethod
    def validate_query_ids(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("purpose", "query")
    @classmethod
    def validate_query_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("source_ids", "type_uris", "capabilities")
    @classmethod
    def validate_query_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "query_ref") for item in value)


class QueryResult(ContractModel):
    trace_id: str
    query_hash: str
    work_id: str
    branch_id: str
    source_versions: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    capability_hits: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    status: str = "OK"
    code: str = "OK"
    result_hash: str = ""

    @field_validator("trace_id", "work_id", "branch_id", "status", "code")
    @classmethod
    def validate_result_strings(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name) if info.field_name in {"trace_id", "work_id", "branch_id"} else _non_empty(value, info.field_name)

    @field_validator("query_hash")
    @classmethod
    def validate_query_hash(cls, value: str) -> str:
        return _hash(value, "query_hash")

    @model_validator(mode="before")
    @classmethod
    def calculate_result_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("trace_id", "query_hash", "work_id", "branch_id", "source_versions", "result_refs", "evidence_refs", "capability_hits", "diagnostics", "status", "code")}
        expected = _stable_hash(payload)
        supplied = result.get("result_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("result_hash does not match canonical result")
        result["result_hash"] = expected
        return result


class SufficiencyReport(ContractModel):
    status: Literal["SUFFICIENT", "INCOMPLETE", "CONFLICTED"]
    missing_required: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    report_hash: str = ""

    @model_validator(mode="before")
    @classmethod
    def calculate_sufficiency_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("status", "missing_required", "missing_capabilities", "missing_evidence", "diagnostics")}
        expected = _stable_hash(payload)
        supplied = result.get("report_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("report_hash does not match canonical report")
        result["report_hash"] = expected
        return result


class NarrativePlan(ContractModel):
    coordinate: StoryCoordinate
    intent: Mapping[str, Any] = Field(default_factory=dict)
    scene_plans: tuple[Mapping[str, Any], ...] = ()
    required_fact_refs: tuple[str, ...] = ()
    allowed_invention_scopes: tuple[str, ...] = ()
    selected_method_refs: tuple[str, ...] = ()
    plan_hash: str = ""

    @model_validator(mode="before")
    @classmethod
    def calculate_plan_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("coordinate", "intent", "scene_plans", "required_fact_refs", "allowed_invention_scopes", "selected_method_refs")}
        expected = _stable_hash(payload)
        supplied = result.get("plan_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("plan_hash does not match canonical plan")
        result["plan_hash"] = expected
        return result


class GroundedChapterPlan(ContractModel):
    narrative_plan_ref: str
    coordinate: StoryCoordinate
    context_view_ref: str
    permitted_claim_refs: tuple[str, ...] = ()
    forbidden_claim_refs: tuple[str, ...] = ()
    scenes: tuple[Mapping[str, Any], ...] = ()
    plan_evidence_refs: tuple[EvidenceRef, ...] = ()
    grounding_status: Literal["GROUNDED", "INCOMPLETE", "CONFLICTED"]
    plan_hash: str = ""

    @field_validator("narrative_plan_ref", "context_view_ref")
    @classmethod
    def validate_grounded_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_grounded_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("narrative_plan_ref", "coordinate", "context_view_ref", "permitted_claim_refs", "forbidden_claim_refs", "scenes", "plan_evidence_refs", "grounding_status")}
        expected = _stable_hash(payload)
        supplied = result.get("plan_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("plan_hash does not match canonical grounded plan")
        result["plan_hash"] = expected
        return result


class ProductionJob(ContractModel):
    schema_version: Literal["production-job.v3"] = "production-job.v3"
    job_id: str
    coordinate: StoryCoordinate
    profile_ref: str
    context_manifest_ref: str
    narrative_plan_ref: str
    grounded_plan_ref: str
    run_id: str
    idempotency_key: str
    job_hash: str = ""

    @field_validator("job_id", "profile_ref", "context_manifest_ref", "narrative_plan_ref", "grounded_plan_ref", "run_id", "idempotency_key")
    @classmethod
    def validate_job_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_job_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("schema_version", "job_id", "coordinate", "profile_ref", "context_manifest_ref", "narrative_plan_ref", "grounded_plan_ref", "run_id", "idempotency_key")}
        expected = _stable_hash(payload)
        supplied = result.get("job_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("job_hash does not match canonical job")
        result["job_hash"] = expected
        return result


class ReviewReport(ContractModel):
    report_id: str
    draft_ref: str
    draft_hash: str
    coordinate: StoryCoordinate
    context_hash: str
    checks: tuple[Mapping[str, Any], ...] = ()
    overall_status: Literal["PASSED", "NEEDS_REVISION", "REJECTED", "INCOMPLETE"]
    blocking_findings: tuple[Mapping[str, Any], ...] = ()
    report_hash: str = ""

    @field_validator("report_id", "draft_ref")
    @classmethod
    def validate_review_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("draft_hash", "context_hash")
    @classmethod
    def validate_review_hashes(cls, value: str, info: Any) -> str:
        return _hash(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_review_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("report_id", "draft_ref", "draft_hash", "coordinate", "context_hash", "checks", "overall_status", "blocking_findings")}
        expected = _stable_hash(payload)
        supplied = result.get("report_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("report_hash does not match canonical review")
        result["report_hash"] = expected
        return result


class StateChangeSet(ContractModel):
    change_set_id: str
    coordinate: StoryCoordinate
    source_artifact_hash: str
    changes: tuple[Mapping[str, Any], ...] = ()
    review_ref: str
    change_set_hash: str = ""

    @field_validator("change_set_id", "review_ref")
    @classmethod
    def validate_change_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("source_artifact_hash")
    @classmethod
    def validate_artifact_hash(cls, value: str) -> str:
        return _hash(value, "source_artifact_hash")

    @model_validator(mode="before")
    @classmethod
    def calculate_change_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("change_set_id", "coordinate", "source_artifact_hash", "changes", "review_ref")}
        expected = _stable_hash(payload)
        supplied = result.get("change_set_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("change_set_hash does not match canonical state changes")
        result["change_set_hash"] = expected
        return result


class Proposal(ContractModel):
    proposal_id: str
    draft_ref: str
    draft_hash: str
    review_ref: str
    state_change_set_ref: str
    context_hash: str
    knowledge_version: str
    status: Literal["PENDING_CONFIRMATION", "APPROVED", "REJECTED", "COMMITTED"] = "PENDING_CONFIRMATION"
    proposal_hash: str = ""

    @field_validator("proposal_id", "draft_ref", "review_ref", "state_change_set_ref", "knowledge_version")
    @classmethod
    def validate_proposal_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("draft_hash", "context_hash")
    @classmethod
    def validate_proposal_hashes(cls, value: str, info: Any) -> str:
        return _hash(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_proposal_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        payload = {key: result.get(key) for key in ("proposal_id", "draft_ref", "draft_hash", "review_ref", "state_change_set_ref", "context_hash", "knowledge_version", "status")}
        expected = _stable_hash(payload)
        supplied = result.get("proposal_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("proposal_hash does not match canonical proposal")
        result["proposal_hash"] = expected
        return result


class ApprovalRef(ContractModel):
    approval_id: str
    proposal_id: str
    actor: Actor
    approval_hash: str
    expires_at: int | str | None = None
    consumed: bool = False

    @field_validator("approval_id", "proposal_id")
    @classmethod
    def validate_approval_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("approval_hash")
    @classmethod
    def validate_approval_hash(cls, value: str) -> str:
        return _hash(value, "approval_hash")


class CommitReceipt(ContractModel):
    commit_id: str
    proposal_id: str
    new_knowledge_version: str
    chapter_version: str
    idempotent_replay: bool = False
    receipt_hash: str = ""

    @field_validator("commit_id", "proposal_id", "new_knowledge_version", "chapter_version")
    @classmethod
    def validate_receipt_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_receipt_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("idempotent_replay", False)
        payload = {key: result.get(key) for key in ("commit_id", "proposal_id", "new_knowledge_version", "chapter_version", "idempotent_replay")}
        expected = _stable_hash(payload)
        supplied = result.get("receipt_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("receipt_hash does not match canonical receipt")
        result["receipt_hash"] = expected
        return result


class CapabilityManifest(ContractModel):
    contract_revision: str = CONTRACT_REVISION
    schema_version: str = SCHEMA_VERSION
    schema_hash: str
    registered_type_uris: tuple[str, ...] = ()
    views: tuple[str, ...] = ()
    projections: tuple[str, ...] = ()
    model_capabilities: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = PUBLIC_ERROR_CODES
    feature_flags: Mapping[str, bool] = Field(default_factory=dict)

    @field_validator("contract_revision", "schema_version")
    @classmethod
    def validate_manifest_versions(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("schema_hash")
    @classmethod
    def validate_schema_hash(cls, value: str) -> str:
        return _hash(value, "schema_hash")

    @field_validator("registered_type_uris", "views", "projections", "model_capabilities", "error_codes")
    @classmethod
    def validate_manifest_lists(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item, "capability") for item in value)


class ErrorEnvelope(ContractModel):
    code: str
    message: str
    trace_id: str
    retryable: bool = False
    dependency_versions: Mapping[str, str] = Field(default_factory=dict)
    result_hash: str | None = None

    @field_validator("code", "trace_id")
    @classmethod
    def validate_error_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("message")
    @classmethod
    def validate_error_message(cls, value: str) -> str:
        return _non_empty(value, "message")

    @field_validator("result_hash")
    @classmethod
    def validate_error_hash(cls, value: str | None) -> str | None:
        return None if value is None else _hash(value, "result_hash")


@lru_cache(maxsize=1)
def contract_schema_hash() -> str:
    """Return the stable hash advertised to Studio and API clients."""

    return _stable_hash(PUBLIC_CONTRACT_SCHEMA)


SCHEMA_HASH = contract_schema_hash()


__all__ = [
    "CONTRACT_REVISION",
    "PUBLIC_CONTRACT_SCHEMA",
    "PUBLIC_ERROR_CODES",
    "SCHEMA_HASH",
    "SCHEMA_VERSION",
    "ERROR_CODE_MAP",
    "ErrorCode",
    "ContractModel",
    "Scope",
    "Validity",
    "EvidenceRef",
    "Actor",
    "SourceBindingRef",
    "SourceSnapshotRef",
    "StoryCoordinate",
    "WorkProfile",
    "CandidateEnvelope",
    "CandidateRef",
    "EvaluationPolicy",
    "ViewSpec",
    "ContextBlock",
    "ContextManifest",
    "ContextView",
    "QueryRequest",
    "QueryResult",
    "SufficiencyReport",
    "NarrativePlan",
    "GroundedChapterPlan",
    "ProductionJob",
    "ReviewReport",
    "StateChangeSet",
    "Proposal",
    "ApprovalRef",
    "CommitReceipt",
    "CapabilityManifest",
    "ErrorEnvelope",
    "contract_schema_hash",
]
