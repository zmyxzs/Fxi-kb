"""Synthetic F6 context-view tests; no project assets are used."""

from hashlib import sha256

import pytest

from fxi.core.canonical import sha256_hex
from fxi.knowledge.as_of import AsOf
from fxi.knowledge.context_compiler import ContextCompilationError, ContextCompiler, ContextRequest
from fxi.knowledge.contracts import EvidenceRef, ErrorCode, Scope, Validity
from fxi.knowledge.objects import Claim, KnowledgeHead, KnowledgeObject
from fxi.knowledge.source_graph import SourceCompositeRef, SourceContributionRef


def _scope(as_of: int = 1) -> Scope:
    return Scope(work_id="work-synthetic", branch_id="branch-main", as_of=as_of, purpose="world-state", actor="actor-test")


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id="evidence-1",
        source_snapshot_ref="snapshot-1",
        source_id="source-1",
        source_version="version-1",
        document_id="document-1",
        start=0,
        end=9,
        excerpt_hash=sha256(b"synthetic").hexdigest(),
        normalization_version="n1",
        scope=_scope(),
    )


def _composite() -> SourceCompositeRef:
    contribution = SourceContributionRef(
        binding_id="binding-1",
        snapshot_id="snapshot-1",
        source_id="source-1",
        source_version="version-1",
        role="reference",
        priority=1,
        license="synthetic",
        branch_id="branch-main",
        content_hash=sha256_hex("source-1"),
        validity=Validity(valid_from=0, valid_to=10),
    )
    return SourceCompositeRef(
        composite_id="composite-1",
        work_id="work-synthetic",
        branch_id="branch-main",
        as_of=1,
        binding_ids=("binding-1",),
        source_snapshot_refs=("snapshot-1",),
        source_versions=("version-1",),
        priorities=(1,),
        licenses=("synthetic",),
        contributions=(contribution,),
    )


def _request(**updates: object) -> ContextRequest:
    data: dict[str, object] = {
        "work_id": "work-synthetic",
        "branch_id": "branch-main",
        "as_of": 1,
        "purpose": "world-state",
        "source_composite": _composite(),
        "knowledge_head": KnowledgeHead(
            work_id="work-synthetic",
            branch_id="branch-main",
            knowledge_version="version-1",
            version_hash="a" * 64,
        ),
    }
    data.update(updates)
    return ContextRequest(**data)


def test_compiler_is_approved_only_evidence_bound_and_deterministic() -> None:
    evidence = _evidence()
    obj = KnowledgeObject(
        object_id="object-1",
        work_id="work-synthetic",
        branch_id="branch-main",
        type_uri="example.note@1",
        schema_uri="schema.note@1",
        schema_version="1",
        payload={"value": "synthetic"},
        origin="source",
        scope=_scope(),
        evidence_refs=(evidence,),
        status="APPROVED",
    )
    rejected = obj.model_copy(update={"object_id": "object-2", "status": "CANDIDATE", "payload_hash": ""})
    compiler = ContextCompiler(objects=(obj, rejected))
    request = _request(object_refs=("object-1",))
    first = compiler.compile(request)
    second = compiler.compile(request)
    assert first.view_hash == second.view_hash
    assert first.content_hash == second.content_hash
    assert len(first.blocks) == 1
    assert first.blocks[0].object_refs == ("object-1",)
    assert first.blocks[0].evidence_refs == (evidence,)


def test_required_context_cannot_be_silently_pruned_by_budget() -> None:
    evidence = _evidence()
    obj = KnowledgeObject(
        object_id="object-1",
        work_id="work-synthetic",
        branch_id="branch-main",
        type_uri="example.note@1",
        schema_uri="schema.note@1",
        schema_version="1",
        payload={"value": "synthetic"},
        origin="source",
        scope=_scope(),
        evidence_refs=(evidence,),
        status="APPROVED",
    )
    compiler = ContextCompiler(objects=(obj,), token_counter=lambda _: 2)
    with pytest.raises(ContextCompilationError) as error:
        compiler.compile(_request(object_refs=("object-1",), budget_tokens=1))
    assert error.value.code == ErrorCode.TOKEN_BUDGET_EXCEEDED.value


def test_provider_type_error_is_not_treated_as_an_empty_collection() -> None:
    class BrokenProvider:
        def objects(self):
            raise TypeError("provider implementation failed")

    compiler = ContextCompiler(provider=BrokenProvider())

    with pytest.raises(ContextCompilationError) as error:
        compiler.compile(_request(object_refs=("object-1",)))

    assert error.value.code == ErrorCode.INVALID_SCHEMA.value
    assert "provider.objects" in str(error.value)
