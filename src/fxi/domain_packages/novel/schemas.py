"""Strict, novel-domain schemas layered on the domain-neutral v3 contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import AliasChoices, Field, field_validator, model_validator

from fxi.knowledge.contracts import ContractModel, EvidenceRef, Scope, _non_empty, _stable_hash, _token
from fxi.knowledge.objects import Claim, Relation


NOVEL_SCHEMA_VERSION = "1"


def _evidence_matches_scope(evidence_refs: tuple[EvidenceRef, ...], scope: Scope) -> tuple[EvidenceRef, ...]:
    if not evidence_refs:
        raise ValueError("evidence_refs must not be empty")
    for evidence in evidence_refs:
        if evidence.scope is None:
            raise ValueError("every evidence reference must carry scope")
        if evidence.scope.work_id != scope.work_id or evidence.scope.branch_id != scope.branch_id:
            raise ValueError("evidence scope must match the object scope")
    return evidence_refs


class NovelPayload(ContractModel):
    """Common strict fields for registered novel records."""

    record_id: str = Field(validation_alias=AliasChoices("record_id", "entity_id", "character_id", "item_id", "location_id", "faction_id"))
    label: str = Field(validation_alias=AliasChoices("label", "name", "title"))
    attributes: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("record_id")
    @classmethod
    def validate_record_id(cls, value: str) -> str:
        return _token(value, "record_id")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return _non_empty(value, "label")


class CharacterPayload(NovelPayload):
    aliases: tuple[str, ...] = ()
    role: str | None = None
    status: str = "ACTIVE"

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item, "alias") for item in value)

    @field_validator("role", "status")
    @classmethod
    def validate_character_strings(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _non_empty(value, info.field_name)


class ItemPayload(NovelPayload):
    owner_ref: str | None = None
    status: str = "ACTIVE"

    @field_validator("owner_ref")
    @classmethod
    def validate_owner_ref(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "owner_ref")

    @field_validator("status")
    @classmethod
    def validate_item_status(cls, value: str) -> str:
        return _non_empty(value, "status")


class LocationPayload(NovelPayload):
    parent_ref: str | None = None

    @field_validator("parent_ref")
    @classmethod
    def validate_parent_ref(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "parent_ref")


class FactionPayload(NovelPayload):
    leader_ref: str | None = None
    member_refs: tuple[str, ...] = ()

    @field_validator("leader_ref")
    @classmethod
    def validate_leader_ref(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "leader_ref")

    @field_validator("member_refs")
    @classmethod
    def validate_member_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "member_ref") for item in value)


class RelationPayload(ContractModel):
    relation_id: str
    relation_type: str
    subject_ref: str
    object_ref: str
    attributes: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("relation_id", "subject_ref", "object_ref")
    @classmethod
    def validate_relation_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("relation_type")
    @classmethod
    def validate_relation_type(cls, value: str) -> str:
        return _non_empty(value, "relation_type")


class TimelineEventPayload(ContractModel):
    event_id: str
    label: str
    at: int | str
    participant_refs: tuple[str, ...] = ()
    location_ref: str | None = None
    details: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        return _token(value, "event_id")

    @field_validator("label")
    @classmethod
    def validate_event_label(cls, value: str) -> str:
        return _non_empty(value, "label")

    @field_validator("at")
    @classmethod
    def validate_event_time(cls, value: int | str) -> int | str:
        if isinstance(value, bool) or (isinstance(value, int) and value < 0) or (isinstance(value, str) and not value.strip()):
            raise ValueError("at must be a non-negative integer or non-empty string")
        return value

    @field_validator("participant_refs", "location_ref")
    @classmethod
    def validate_event_refs(cls, value: Any, info: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, tuple):
            return tuple(_token(item, "participant_ref") for item in value)
        return _token(value, info.field_name)


class CausalLinkPayload(ContractModel):
    link_id: str
    cause_ref: str
    effect_ref: str
    basis: str

    @field_validator("link_id", "cause_ref", "effect_ref")
    @classmethod
    def validate_link_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("basis")
    @classmethod
    def validate_link_basis(cls, value: str) -> str:
        return _non_empty(value, "basis")


class StateEventPayload(ContractModel):
    event_id: str
    subject_ref: str
    state: Mapping[str, Any]
    at: int | str
    formula: str
    rule_version: str

    @field_validator("event_id", "subject_ref")
    @classmethod
    def validate_state_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("formula", "rule_version")
    @classmethod
    def validate_state_rule_fields(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class CharacterKnowledgePayload(ContractModel):
    knowledge_id: str
    character_ref: str
    claim_ref: str
    belief_status: str
    as_of: int | str
    basis: str

    @field_validator("knowledge_id", "character_ref", "claim_ref")
    @classmethod
    def validate_knowledge_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("belief_status", "basis")
    @classmethod
    def validate_knowledge_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class StylePackagePayload(ContractModel):
    style_id: str
    style_version: str
    constraints: Mapping[str, Any]
    voice: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("style_id", "style_version")
    @classmethod
    def validate_style_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)


class TropePayload(ContractModel):
    trope_id: str
    label: str
    beats: tuple[str, ...] = ()
    semantic_core: Mapping[str, Any] = Field(default_factory=dict)
    structural_signature: Mapping[str, Any] = Field(default_factory=dict)
    expression_features: Mapping[str, Any] = Field(default_factory=dict)
    constraints: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("trope_id")
    @classmethod
    def validate_trope_id(cls, value: str) -> str:
        return _token(value, "trope_id")

    @field_validator("label")
    @classmethod
    def validate_trope_label(cls, value: str) -> str:
        return _non_empty(value, "label")

    @field_validator("beats")
    @classmethod
    def validate_trope_beats(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item, "beat") for item in value)


class ForeshadowingPayload(ContractModel):
    foreshadowing_id: str
    signal_ref: str
    target_ref: str
    status: str
    details: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("foreshadowing_id", "signal_ref", "target_ref")
    @classmethod
    def validate_foreshadowing_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("status")
    @classmethod
    def validate_foreshadowing_status(cls, value: str) -> str:
        return _non_empty(value, "status")


class MutationPayload(ContractModel):
    mutation_id: str
    target_ref: str
    operation: str
    before: Any
    after: Any
    rule_version: str

    @field_validator("mutation_id", "target_ref")
    @classmethod
    def validate_mutation_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("operation", "rule_version")
    @classmethod
    def validate_mutation_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class NovelPayloadEnvelope(ContractModel):
    """Optional evidence-bound envelope used by extractors and integrations."""

    type_uri: str
    schema_version: str
    scope: Scope
    evidence_refs: tuple[EvidenceRef, ...]
    payload: Mapping[str, Any]
    candidate_ref: str | None = None

    @field_validator("type_uri", "schema_version")
    @classmethod
    def validate_envelope_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("candidate_ref")
    @classmethod
    def validate_candidate_ref(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "candidate_ref")

    @model_validator(mode="after")
    def validate_envelope(self) -> "NovelPayloadEnvelope":
        if self.schema_version != NOVEL_SCHEMA_VERSION:
            raise ValueError(f"unsupported novel schema version: {self.schema_version}")
        _evidence_matches_scope(self.evidence_refs, self.scope)
        return self


class ClaimStatus(str, Enum):
    ASSERTED = "ASSERTED"
    SUPERSEDED = "SUPERSEDED"
    DISPUTED = "DISPUTED"
    INVALIDATED = "INVALIDATED"
    UNKNOWN = "UNKNOWN"


class ClaimPerspective(str, Enum):
    CANONICAL = "CANONICAL"
    RETCON = "RETCON"
    MISUNDERSTANDING = "MISUNDERSTANDING"
    CHARACTER_KNOWLEDGE = "CHARACTER_KNOWLEDGE"


class NovelClaim(ContractModel):
    """Evidence-bound claim view with explicit perspective semantics."""

    claim_id: str
    subject_ref: str = Field(validation_alias=AliasChoices("subject", "subject_ref"))
    predicate: str
    value: Any
    object_type_uri: str = Field(validation_alias=AliasChoices("type", "object_type_uri"))
    scope: Scope
    evidence_refs: tuple[EvidenceRef, ...] = Field(validation_alias=AliasChoices("evidence", "evidence_refs"))
    status: str = ClaimStatus.ASSERTED.value
    version: str = "1"
    perspective: ClaimPerspective = Field(default=ClaimPerspective.CANONICAL, validation_alias=AliasChoices("perspective", "claim_kind", "kind"))
    perspective_ref: str | None = Field(default=None, validation_alias=AliasChoices("perspective_ref", "knower_ref", "character_ref"))
    supersedes_claim_ref: str | None = None
    claim_hash: str = ""

    @field_validator("claim_id", "subject_ref", "object_type_uri")
    @classmethod
    def validate_claim_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("predicate", "status", "version")
    @classmethod
    def validate_claim_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("perspective_ref", "supersedes_claim_ref")
    @classmethod
    def validate_claim_optional_refs(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @model_validator(mode="after")
    def validate_claim_semantics(self) -> "NovelClaim":
        _evidence_matches_scope(self.evidence_refs, self.scope)
        if self.perspective in {ClaimPerspective.MISUNDERSTANDING, ClaimPerspective.CHARACTER_KNOWLEDGE} and self.perspective_ref is None:
            raise ValueError("perspective_ref is required for non-canonical character views")
        if self.perspective is ClaimPerspective.RETCON and self.supersedes_claim_ref is None:
            raise ValueError("retcon claims must identify the superseded claim")
        payload = {
            "claim_id": self.claim_id,
            "subject_ref": self.subject_ref,
            "predicate": self.predicate,
            "value": self.value,
            "object_type_uri": self.object_type_uri,
            "scope": self.scope,
            "evidence_refs": self.evidence_refs,
            "status": self.status,
            "version": self.version,
            "perspective": self.perspective,
            "perspective_ref": self.perspective_ref,
            "supersedes_claim_ref": self.supersedes_claim_ref,
        }
        expected = _stable_hash(payload)
        if self.claim_hash not in ("", expected):
            raise ValueError("claim_hash does not match canonical claim")
        object.__setattr__(self, "claim_hash", expected)
        return self

    def to_kernel(self) -> Claim:
        return Claim(
            claim_id=self.claim_id,
            subject_ref=self.subject_ref,
            predicate=self.predicate,
            value=self.value,
            object_type_uri=self.object_type_uri,
            scope=self.scope,
            evidence_refs=self.evidence_refs,
            status=self.status,
            version=self.version,
        )

    @classmethod
    def from_kernel(cls, claim: Claim) -> "NovelClaim":
        return cls(
            claim_id=claim.claim_id,
            subject_ref=claim.subject_ref,
            predicate=claim.predicate,
            value=claim.value,
            object_type_uri=claim.object_type_uri,
            scope=claim.scope,
            evidence_refs=claim.evidence_refs,
            status=claim.status,
            version=claim.version or "1",
        )


class NovelRelation(ContractModel):
    relation_id: str
    relation_type: str
    subject_ref: str
    object_ref: str
    scope: Scope
    evidence_refs: tuple[EvidenceRef, ...]
    status: str = ClaimStatus.ASSERTED.value
    relation_hash: str = ""

    @field_validator("relation_id", "subject_ref", "object_ref")
    @classmethod
    def validate_relation_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("relation_type", "status")
    @classmethod
    def validate_relation_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @model_validator(mode="after")
    def validate_relation(self) -> "NovelRelation":
        _evidence_matches_scope(self.evidence_refs, self.scope)
        payload = {
            "relation_id": self.relation_id,
            "relation_type": self.relation_type,
            "subject_ref": self.subject_ref,
            "object_ref": self.object_ref,
            "scope": self.scope,
            "evidence_refs": self.evidence_refs,
            "status": self.status,
        }
        expected = _stable_hash(payload)
        if self.relation_hash not in ("", expected):
            raise ValueError("relation_hash does not match canonical relation")
        object.__setattr__(self, "relation_hash", expected)
        return self

    def to_kernel(self) -> Relation:
        return Relation(
            relation_id=self.relation_id,
            relation_type=self.relation_type,
            subject_ref=self.subject_ref,
            object_ref=self.object_ref,
            scope=self.scope,
            evidence_refs=self.evidence_refs,
            status=self.status,
        )


class StateRule(ContractModel):
    formula: str
    rule_version: str

    @field_validator("formula", "rule_version")
    @classmethod
    def validate_rule_fields(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


PAYLOAD_MODELS: Mapping[str, type[ContractModel]] = {
    "character": CharacterPayload,
    "item": ItemPayload,
    "location": LocationPayload,
    "faction": FactionPayload,
    "relation": RelationPayload,
    "timeline_event": TimelineEventPayload,
    "causal_link": CausalLinkPayload,
    "state_event": StateEventPayload,
    "character_knowledge": CharacterKnowledgePayload,
    "style_package": StylePackagePayload,
    "trope": TropePayload,
    "foreshadowing": ForeshadowingPayload,
    "mutation": MutationPayload,
}


__all__ = [
    "NOVEL_SCHEMA_VERSION",
    "CharacterKnowledgePayload",
    "CharacterPayload",
    "ClaimPerspective",
    "ClaimStatus",
    "CausalLinkPayload",
    "FactionPayload",
    "ForeshadowingPayload",
    "ItemPayload",
    "LocationPayload",
    "MutationPayload",
    "NovelClaim",
    "NovelPayload",
    "NovelPayloadEnvelope",
    "NovelRelation",
    "PAYLOAD_MODELS",
    "RelationPayload",
    "StateEventPayload",
    "StateRule",
    "StylePackagePayload",
    "TimelineEventPayload",
    "TropePayload",
]
