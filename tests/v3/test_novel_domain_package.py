"""Clean-room tests for the registered novel domain package."""

from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from fxi.domain_packages.novel import (
    SUPPORTED_TYPE_URIS,
    ExtractionInput,
    NovelDomainPackage,
    NovelPayloadEnvelope,
    StateRule,
    extract_candidate,
    register_novel,
)
from fxi.domain_packages.novel.projections import project
from fxi.domain_packages.novel.context import render_context
from fxi.knowledge import DomainRegistry, KnowledgeObject, RegistryError
from fxi.knowledge.candidate_service import CandidateService
from fxi.knowledge.contracts import EvidenceRef, Scope
from fxi.knowledge.context_blocks import recompute_block_hash


def _scope() -> Scope:
    return Scope(work_id="work-synthetic", branch_id="branch-main", as_of=3, purpose="test", actor="actor-test")


def _evidence(scope: Scope) -> EvidenceRef:
    return EvidenceRef(
        evidence_id="evidence-synthetic",
        source_snapshot_ref="snapshot-synthetic",
        source_id="source-synthetic",
        source_version="version-1",
        document_id="document-synthetic",
        start=0,
        end=8,
        excerpt_hash=sha256(b"synthetic").hexdigest(),
        normalization_version="normalization-1",
        scope=scope,
    )


def test_package_registers_types_and_exposes_contract() -> None:
    registry = DomainRegistry()
    package = register_novel(registry)

    assert isinstance(package, NovelDomainPackage)
    assert registry.resolve("novel.character@1") is package
    assert registry.registered_type_uris() == tuple(sorted(SUPPORTED_TYPE_URIS))
    contract = package.extract_contract()
    assert contract["contract_revision"] == "studio-fxi-v3.20260909"
    assert len(contract["schema_hash"]) == 64
    assert set(contract["schemas"]) == set(SUPPORTED_TYPE_URIS)


def test_payload_validation_migration_and_quarantine_are_fail_closed() -> None:
    package = NovelDomainPackage()
    valid = {"record_id": "character-1", "label": "synthetic", "attributes": {"marker": "test"}}
    package.validate("novel.character@1", valid)
    migrated = package.migrate("novel.character@1", {"id": "character-1", "name": "synthetic"}, "0")
    assert migrated["record_id"] == "character-1"
    with pytest.raises(ValueError):
        package.migrate("novel.character@1", valid, "unsupported")
    with pytest.raises(ValidationError):
        package.validate("novel.character@1", {**valid, "unexpected": True})

    registry = DomainRegistry()
    registry.register(package)
    with pytest.raises(RegistryError):
        registry.validate("novel.character@1", {"record_id": "character-1"})
    record = registry.quarantine_records()[-1]
    assert record.reason == "INVALID_SCHEMA"
    assert record.payload == {"record_id": "character-1"}


def test_extractor_binds_evidence_and_f3_candidate_service_without_domain_logic() -> None:
    scope = _scope()
    evidence = _evidence(scope)
    candidate = extract_candidate(
        ExtractionInput(
            candidate_id="candidate-character-1",
            type_uri="novel.character@1",
            payload={"record_id": "character-1", "label": "synthetic"},
            scope=scope,
            source_snapshot_ref="snapshot-synthetic",
            evidence_refs=(evidence,),
        )
    )
    assert candidate.artifact_kind == "novel.character@1"
    assert candidate.input_hash != ""
    assert CandidateService().submit(candidate).status == "CANDIDATE"
    with pytest.raises(ValueError):
        extract_candidate(
            ExtractionInput(
                candidate_id="candidate-character-2",
                type_uri="novel.character@1",
                payload={"record_id": "character-2", "label": "synthetic"},
                scope=scope,
                source_snapshot_ref="snapshot-synthetic",
                evidence_refs=(evidence.model_copy(update={"scope": Scope(work_id="other-work", branch_id="branch-main", as_of=3, purpose="test", actor="actor-test")}),),
            )
        )


def test_context_and_projection_accept_only_approved_kernel_objects() -> None:
    scope = _scope()
    evidence = _evidence(scope)
    obj = KnowledgeObject(
        object_id="object-character-1",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        type_uri="novel.character@1",
        schema_uri="novel-character-schema-1",
        schema_version="1",
        payload={"record_id": "character-1", "label": "synthetic"},
        origin="novel-extractor-1",
        scope=scope,
        evidence_refs=(evidence,),
        status="APPROVED",
    )
    block = render_context(obj, {"scope": scope})
    records = project(obj)
    assert block.object_refs == (obj.object_id,)
    assert block.block_hash == recompute_block_hash(block)
    assert records[0].deletable is True
    assert records[0].deletion_key.startswith("work-synthetic:branch-main:")
    with pytest.raises(ValueError):
        render_context(obj.model_copy(update={"status": "CANDIDATE"}), None)
    with pytest.raises(ValueError):
        project(obj.model_copy(update={"status": "CANDIDATE"}))


def test_evidence_bound_envelope_and_state_rule_require_explicit_versions() -> None:
    scope = _scope()
    evidence = _evidence(scope)
    envelope = NovelPayloadEnvelope(
        type_uri="novel.state_event@1",
        schema_version="1",
        scope=scope,
        evidence_refs=(evidence,),
        payload={"event_id": "event-1", "subject_ref": "character-1", "state": {"active": True}, "at": 1, "formula": "declared", "rule_version": "rule-1"},
    )
    assert envelope.schema_version == "1"
    assert StateRule(formula="declared", rule_version="rule-1").rule_version == "rule-1"


def test_trope_payload_keeps_the_three_domain_axes() -> None:
    package = NovelDomainPackage()
    package.validate(
        "novel.trope@1",
        {
            "trope_id": "trope-synthetic",
            "label": "synthetic",
            "semantic_core": {"core": "opaque"},
            "structural_signature": {"shape": "opaque"},
            "expression_features": {"surface": "opaque"},
        },
    )
