"""Controlled extraction adapters for the novel domain package."""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import field_validator, model_validator

from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import Actor, CandidateEnvelope, ContractModel, EvidenceRef, Scope, _hash, _token
from fxi.knowledge.objects import Claim, Relation

from .schemas import NOVEL_SCHEMA_VERSION, NovelClaim, NovelRelation, _evidence_matches_scope


_DEFAULT_POLICY_HASH = sha256_hex({"package": "novel", "purpose": "extraction", "version": NOVEL_SCHEMA_VERSION})


class ExtractionInput(ContractModel):
    """All candidate metadata required before a domain payload can be emitted."""

    candidate_id: str
    type_uri: str
    payload: Mapping[str, Any]
    scope: Scope
    source_snapshot_ref: str
    evidence_refs: tuple[EvidenceRef, ...]
    extractor_id: str = "novel-extractor-1"
    schema_version: str = NOVEL_SCHEMA_VERSION
    domain_package_version: str = NOVEL_SCHEMA_VERSION
    policy_hash: str = _DEFAULT_POLICY_HASH
    actor: Actor | None = None

    @field_validator("candidate_id", "type_uri", "source_snapshot_ref", "extractor_id", "schema_version", "domain_package_version")
    @classmethod
    def validate_input_refs(cls, value: str, info: Any) -> str:
        return _token(value, info.field_name)

    @field_validator("policy_hash")
    @classmethod
    def validate_policy_hash(cls, value: str) -> str:
        return _hash(value, "policy_hash")

    @model_validator(mode="after")
    def validate_input_scope(self) -> "ExtractionInput":
        if self.schema_version != NOVEL_SCHEMA_VERSION or self.domain_package_version != NOVEL_SCHEMA_VERSION:
            raise ValueError("unsupported extraction schema or package version")
        _evidence_matches_scope(self.evidence_refs, self.scope)
        if self.actor is not None and self.actor.scope is not None and self.actor.scope != self.scope:
            raise ValueError("extractor actor scope must match extraction scope")
        return self


class ClaimExtractionInput(ContractModel):
    claim: NovelClaim | Mapping[str, Any]
    scope: Scope | None = None
    evidence_refs: tuple[EvidenceRef, ...] | None = None


class RelationExtractionInput(ContractModel):
    relation: NovelRelation | Mapping[str, Any]
    scope: Scope | None = None
    evidence_refs: tuple[EvidenceRef, ...] | None = None


def _payload_for_candidate(data: ExtractionInput) -> None:
    from .manifest import NovelDomainPackage

    NovelDomainPackage().validate(data.type_uri, data.payload)


def extract_candidate(data: ExtractionInput | Mapping[str, Any]) -> CandidateEnvelope:
    """Validate controlled input and return an F3 candidate envelope."""

    request = data if isinstance(data, ExtractionInput) else ExtractionInput.model_validate(data)
    _payload_for_candidate(request)
    input_hash = sha256_hex(
        {
            "candidate_id": request.candidate_id,
            "type_uri": request.type_uri,
            "payload": request.payload,
            "scope": request.scope.model_dump(mode="json"),
            "source_snapshot_ref": request.source_snapshot_ref,
            "evidence_refs": [item.model_dump(mode="json") for item in request.evidence_refs],
            "extractor_id": request.extractor_id,
            "schema_version": request.schema_version,
            "domain_package_version": request.domain_package_version,
            "policy_hash": request.policy_hash,
        }
    )
    return CandidateEnvelope(
        candidate_id=request.candidate_id,
        artifact_kind=request.type_uri,
        work_id=request.scope.work_id,
        branch_id=request.scope.branch_id,
        source_snapshot_ref=request.source_snapshot_ref,
        evidence_refs=request.evidence_refs,
        input_hash=input_hash,
        extractor_id=request.extractor_id,
        schema_version=request.schema_version,
        domain_package_version=request.domain_package_version,
        policy_hash=request.policy_hash,
        payload=request.payload,
        actor=request.actor,
    )


def _claim_view(data: NovelClaim | Claim | Mapping[str, Any], *, scope: Scope | None, evidence_refs: tuple[EvidenceRef, ...] | None) -> NovelClaim:
    if isinstance(data, NovelClaim):
        claim = data
    elif isinstance(data, Claim):
        claim = NovelClaim.from_kernel(data)
    else:
        values = dict(data)
        if scope is not None:
            values["scope"] = scope
        if evidence_refs is not None:
            values["evidence_refs"] = evidence_refs
        claim = NovelClaim.model_validate(values)
    if scope is not None and claim.scope != scope:
        raise ValueError("claim scope does not match extraction scope")
    if evidence_refs is not None and claim.evidence_refs != evidence_refs:
        raise ValueError("claim evidence does not match extraction evidence")
    return claim


def extract_claim(data: NovelClaim | Claim | Mapping[str, Any], *, scope: Scope | None = None, evidence_refs: tuple[EvidenceRef, ...] | None = None) -> Claim:
    """Return the generic kernel Claim after package validation."""

    return _claim_view(data, scope=scope, evidence_refs=evidence_refs).to_kernel()


def extract_claim_view(data: NovelClaim | Claim | Mapping[str, Any], *, scope: Scope | None = None, evidence_refs: tuple[EvidenceRef, ...] | None = None) -> NovelClaim:
    return _claim_view(data, scope=scope, evidence_refs=evidence_refs)


def _relation_view(data: NovelRelation | Relation | Mapping[str, Any], *, scope: Scope | None, evidence_refs: tuple[EvidenceRef, ...] | None) -> NovelRelation:
    if isinstance(data, NovelRelation):
        relation = data
    elif isinstance(data, Relation):
        relation = NovelRelation(
            relation_id=data.relation_id,
            relation_type=data.relation_type,
            subject_ref=data.subject_ref,
            object_ref=data.object_ref,
            scope=data.scope,
            evidence_refs=data.evidence_refs,
            status=data.status,
        )
    else:
        values = dict(data)
        if scope is not None:
            values["scope"] = scope
        if evidence_refs is not None:
            values["evidence_refs"] = evidence_refs
        relation = NovelRelation.model_validate(values)
    if scope is not None and relation.scope != scope:
        raise ValueError("relation scope does not match extraction scope")
    if evidence_refs is not None and relation.evidence_refs != evidence_refs:
        raise ValueError("relation evidence does not match extraction evidence")
    return relation


def extract_relation(data: NovelRelation | Relation | Mapping[str, Any], *, scope: Scope | None = None, evidence_refs: tuple[EvidenceRef, ...] | None = None) -> Relation:
    return _relation_view(data, scope=scope, evidence_refs=evidence_refs).to_kernel()


class NovelExtractor:
    """Stateless facade used by callers that inject a package extractor."""

    def extract_candidate(self, data: ExtractionInput | Mapping[str, Any]) -> CandidateEnvelope:
        return extract_candidate(data)

    def extract_claim(self, data: NovelClaim | Claim | Mapping[str, Any], *, scope: Scope | None = None, evidence_refs: tuple[EvidenceRef, ...] | None = None) -> Claim:
        return extract_claim(data, scope=scope, evidence_refs=evidence_refs)

    def extract_relation(self, data: NovelRelation | Relation | Mapping[str, Any], *, scope: Scope | None = None, evidence_refs: tuple[EvidenceRef, ...] | None = None) -> Relation:
        return extract_relation(data, scope=scope, evidence_refs=evidence_refs)


__all__ = [
    "ClaimExtractionInput",
    "ExtractionInput",
    "NovelExtractor",
    "RelationExtractionInput",
    "extract_candidate",
    "extract_claim",
    "extract_claim_view",
    "extract_relation",
]
