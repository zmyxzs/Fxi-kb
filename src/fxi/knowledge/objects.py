"""Authority-side knowledge objects built from the shared v3 contracts."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .contracts import ContractModel, EvidenceRef, Scope, Validity, _computed_hash, _hash, _non_empty, _stable_hash, _token


class KnowledgeObject(ContractModel):
    object_id: str
    work_id: str
    branch_id: str
    type_uri: str
    schema_uri: str
    schema_version: str
    payload: Mapping[str, Any]
    payload_hash: str = ""
    origin: str
    scope: Scope
    validity: Validity = Field(default_factory=Validity)
    evidence_refs: tuple[EvidenceRef, ...] = ()
    evaluation_ref: str | None = None
    knowledge_version: str | None = None
    status: str = "CANDIDATE"

    @field_validator("object_id", "work_id", "branch_id", "schema_version")
    @classmethod
    def validate_object_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("type_uri", "schema_uri")
    @classmethod
    def validate_type_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("origin", "status")
    @classmethod
    def validate_object_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("evaluation_ref", "knowledge_version")
    @classmethod
    def validate_optional_object_refs(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _token(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_payload_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        return _computed_hash(data, "payload_hash", "payload")

    @model_validator(mode="after")
    def validate_scope_binding(self) -> "KnowledgeObject":
        if self.scope.work_id != self.work_id or self.scope.branch_id != self.branch_id:
            raise ValueError("KnowledgeObject scope must match work_id and branch_id")
        return self


class Claim(ContractModel):
    claim_id: str
    subject_ref: str
    predicate: str
    value: Any
    object_type_uri: str
    scope: Scope
    evidence_refs: tuple[EvidenceRef, ...] = ()
    status: str = "ASSERTED"
    version: str | None = None
    claim_hash: str = ""

    @field_validator("claim_id", "subject_ref", "object_type_uri")
    @classmethod
    def validate_claim_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("predicate", "status")
    @classmethod
    def validate_claim_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("version")
    @classmethod
    def validate_claim_version(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "version")

    @model_validator(mode="before")
    @classmethod
    def calculate_claim_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("evidence_refs", ())
        result.setdefault("status", "ASSERTED")
        result.setdefault("version", None)
        payload = {key: result.get(key) for key in ("claim_id", "subject_ref", "predicate", "value", "object_type_uri", "scope", "evidence_refs", "status", "version")}
        expected = _stable_hash(payload)
        supplied = result.get("claim_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("claim_hash does not match canonical claim")
        result["claim_hash"] = expected
        return result


class Relation(ContractModel):
    relation_id: str
    relation_type: str
    subject_ref: str
    object_ref: str
    scope: Scope
    evidence_refs: tuple[EvidenceRef, ...] = ()
    status: str = "ASSERTED"
    relation_hash: str = ""

    @field_validator("relation_id", "subject_ref", "object_ref")
    @classmethod
    def validate_relation_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("relation_type", "status")
    @classmethod
    def validate_relation_strings(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def calculate_relation_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("evidence_refs", ())
        result.setdefault("status", "ASSERTED")
        payload = {key: result.get(key) for key in ("relation_id", "relation_type", "subject_ref", "object_ref", "scope", "evidence_refs", "status")}
        expected = _stable_hash(payload)
        supplied = result.get("relation_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("relation_hash does not match canonical relation")
        result["relation_hash"] = expected
        return result


class KnowledgeVersion(ContractModel):
    knowledge_version: str
    work_id: str
    branch_id: str
    parent_version: str | None = None
    object_refs: tuple[str, ...] = ()
    claim_refs: tuple[str, ...] = ()
    relation_refs: tuple[str, ...] = ()
    source_snapshot_refs: tuple[str, ...] = ()
    status: str = "APPROVED"
    actor: str
    version_hash: str = ""

    @field_validator("knowledge_version", "work_id", "branch_id", "actor")
    @classmethod
    def validate_version_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("parent_version")
    @classmethod
    def validate_parent_version(cls, value: str | None) -> str | None:
        return None if value is None else _token(value, "parent_version")

    @field_validator("object_refs", "claim_refs", "relation_refs", "source_snapshot_refs")
    @classmethod
    def validate_version_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_token(item, "version_ref") for item in value)

    @field_validator("status")
    @classmethod
    def validate_version_status(cls, value: str) -> str:
        return _non_empty(value, "status")

    @model_validator(mode="before")
    @classmethod
    def calculate_version_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("parent_version", None)
        result.setdefault("object_refs", ())
        result.setdefault("claim_refs", ())
        result.setdefault("relation_refs", ())
        result.setdefault("source_snapshot_refs", ())
        result.setdefault("status", "APPROVED")
        payload = {key: result.get(key) for key in ("knowledge_version", "work_id", "branch_id", "parent_version", "object_refs", "claim_refs", "relation_refs", "source_snapshot_refs", "status", "actor")}
        expected = _stable_hash(payload)
        supplied = result.get("version_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("version_hash does not match canonical knowledge version")
        result["version_hash"] = expected
        return result


class KnowledgeHead(ContractModel):
    work_id: str
    branch_id: str
    knowledge_version: str
    version_hash: str
    cas_revision: int = Field(default=0, ge=0)

    @field_validator("work_id", "branch_id", "knowledge_version")
    @classmethod
    def validate_head_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("version_hash")
    @classmethod
    def validate_head_hash(cls, value: str) -> str:
        return _hash(value, "version_hash")


class Commit(ContractModel):
    commit_id: str
    work_id: str
    branch_id: str
    proposal_ref: str
    knowledge_version: str
    chapter_version: str
    actor: str
    idempotency_key: str
    status: str = "COMMITTED"
    commit_hash: str = ""

    @field_validator("commit_id", "work_id", "branch_id", "proposal_ref", "knowledge_version", "chapter_version", "actor", "idempotency_key")
    @classmethod
    def validate_commit_tokens(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("status")
    @classmethod
    def validate_commit_status(cls, value: str) -> str:
        return _non_empty(value, "status")

    @model_validator(mode="before")
    @classmethod
    def calculate_commit_hash(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        result = dict(data)
        result.setdefault("status", "COMMITTED")
        payload = {key: result.get(key) for key in ("commit_id", "work_id", "branch_id", "proposal_ref", "knowledge_version", "chapter_version", "actor", "idempotency_key", "status")}
        expected = _stable_hash(payload)
        supplied = result.get("commit_hash")
        if supplied not in (None, "") and supplied != expected:
            raise ValueError("commit_hash does not match canonical commit")
        result["commit_hash"] = expected
        return result


__all__ = [
    "Claim",
    "Commit",
    "KnowledgeHead",
    "KnowledgeObject",
    "KnowledgeVersion",
    "Relation",
]
