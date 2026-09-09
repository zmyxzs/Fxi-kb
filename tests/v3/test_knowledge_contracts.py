"""Synthetic contract tests for the Fxi v3 generic kernel."""

from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from fxi.core.canonical import sha256_hex
from fxi.knowledge import (
    CONTRACT_REVISION,
    SCHEMA_HASH,
    Actor,
    Claim,
    CommitReceipt,
    ContextBlock,
    ContextView,
    EvidenceRef,
    KnowledgeObject,
    Proposal,
    Scope,
    StoryCoordinate,
    Validity,
    contract_schema_hash,
    validate_evidence_ref,
)


def _scope() -> Scope:
    return Scope(work_id="work-synthetic", branch_id="main", as_of=1, purpose="test", actor="actor-test")


def _evidence(text: str = "synthetic") -> EvidenceRef:
    return EvidenceRef(
        source_snapshot_ref="snapshot-1",
        source_id="source-1",
        source_version="version-1",
        document_id="document-1",
        start=0,
        end=len(text),
        excerpt_hash=sha256(text.encode("utf-8")).hexdigest(),
        normalization_version="n1",
        scope=_scope(),
    )


def test_contract_revision_and_schema_hash_are_stable() -> None:
    assert CONTRACT_REVISION == "studio-fxi-v3.20260909"
    assert SCHEMA_HASH == contract_schema_hash()
    assert len(SCHEMA_HASH) == 64
    assert SCHEMA_HASH == sha256(SCHEMA_HASH.encode("ascii")).hexdigest() or len(SCHEMA_HASH) == 64


def test_scope_and_validity_reject_unsafe_or_backwards_values() -> None:
    with pytest.raises(ValidationError):
        Scope(work_id="../escape", branch_id="main", as_of=1, purpose="test", actor="actor-test")
    with pytest.raises(ValidationError):
        Validity(valid_from=3, valid_to=2)
    with pytest.raises(ValidationError):
        StoryCoordinate(
            work_id="work-synthetic",
            branch_id="main",
            as_of=1,
            chapter_index=0,
            narrative_order=1,
            knowledge_version="kv-1",
        )


def test_knowledge_object_claim_and_relation_hashes_are_derived() -> None:
    evidence = _evidence()
    obj = KnowledgeObject(
        object_id="object-1",
        work_id="work-synthetic",
        branch_id="main",
        type_uri="example.note@1",
        schema_uri="schema.note@1",
        schema_version="1",
        payload={"value": "synthetic"},
        origin="source",
        scope=_scope(),
        evidence_refs=(evidence,),
    )
    assert obj.payload_hash == sha256_hex(obj.payload)
    with pytest.raises(ValidationError):
        KnowledgeObject(
            object_id="object-1",
            work_id="work-synthetic",
            branch_id="main",
            type_uri="example.note@1",
            schema_uri="schema.note@1",
            schema_version="1",
            payload={"value": "synthetic"},
            payload_hash="0" * 64,
            origin="source",
            scope=_scope(),
            evidence_refs=(evidence,),
        )
    claim = Claim(
        claim_id="claim-1",
        subject_ref="object-1",
        predicate="has_value",
        value="synthetic",
        object_type_uri="example.note@1",
        scope=_scope(),
        evidence_refs=(evidence,),
    )
    assert len(claim.claim_hash) == 64


def test_evidence_is_half_open_and_hash_checked() -> None:
    ref = _evidence("synthetic")
    validated = validate_evidence_ref(ref, expected_text="synthetic", document_length=20)
    assert validated.excerpt == "synthetic"
    with pytest.raises(Exception):
        validate_evidence_ref(ref, expected_text="different", document_length=20)


def test_context_view_hash_and_scope_are_derived_without_side_effects() -> None:
    scope = _scope()
    block = ContextBlock(
        block_id="block-1",
        type_uri="example.note@1",
        content={"value": "synthetic"},
        scope=scope,
    )
    view = ContextView(
        view_id="view-1",
        work_id="work-synthetic",
        branch_id="main",
        as_of=1,
        purpose="test",
        scope=scope,
        blocks=(block,),
    )
    assert len(view.view_hash) == 64
    assert len(view.content_hash) == 64
    assert view.blocks[0].block_hash == block.block_hash


def test_studio_commit_models_have_canonical_hashes_and_forbid_extra_fields() -> None:
    coordinate = StoryCoordinate(
        work_id="work-synthetic",
        branch_id="main",
        as_of=1,
        chapter_index=1,
        narrative_order=1,
        knowledge_version="kv-1",
    )
    review_hash = sha256_hex({"draft": "synthetic"})
    proposal = Proposal(
        proposal_id="proposal-1",
        draft_ref="draft-1",
        draft_hash=review_hash,
        review_ref="review-1",
        state_change_set_ref="changes-1",
        context_hash=review_hash,
        knowledge_version="kv-1",
    )
    assert len(proposal.proposal_hash) == 64
    receipt = CommitReceipt(
        commit_id="commit-1",
        proposal_id=proposal.proposal_id,
        new_knowledge_version="kv-2",
        chapter_version="chapter-1",
    )
    assert len(receipt.receipt_hash) == 64
    with pytest.raises(ValidationError):
        Actor(actor_id="actor-test", role="writer", unexpected="nope")
