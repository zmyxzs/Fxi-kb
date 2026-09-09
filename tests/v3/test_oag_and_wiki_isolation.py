"""Clean-room tests proving graph/Wiki projections cannot become fact sources."""

from __future__ import annotations

from hashlib import sha256

from fxi.knowledge.contracts import Actor, EvidenceRef, Scope
from fxi.knowledge.conflict_sets import ConflictCandidate, ConflictSet
from fxi.knowledge.objects import Claim, KnowledgeObject, KnowledgeVersion, Relation
from fxi.knowledge.oag_projection import GraphQuery, OAGLiteProjectionProvider
from fxi.knowledge.projection_runner import ProjectionRunner
from fxi.knowledge.wiki_projection import WikiProjectionProvider


def _scope(work_id: str = "work-graph") -> Scope:
    return Scope(work_id=work_id, branch_id="branch-main", as_of=2, purpose="synthetic-test", actor="actor-test")


def _evidence(scope: Scope, suffix: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"evidence-{suffix}",
        source_snapshot_ref="snapshot-source-synthetic",
        source_id="source-synthetic",
        source_version="version-1",
        document_id="document-synthetic",
        start=0,
        end=8,
        excerpt_hash=sha256(b"synthetic").hexdigest(),
        normalization_version="normalization-1",
        scope=scope,
    )


def test_oag_multihop_is_scope_bound_and_does_not_project_regular_objects() -> None:
    scope = _scope()
    evidence = _evidence(scope, "graph")
    first = Claim(
        claim_id="claim-a-b",
        subject_ref="node-a",
        predicate="connects",
        value="node-b",
        object_type_uri="type-a@1",
        scope=scope,
        evidence_refs=(evidence,),
    )
    second = Relation(
        relation_id="relation-b-c",
        relation_type="causal-link",
        subject_ref="node-b",
        object_ref="node-c",
        scope=scope,
        evidence_refs=(evidence,),
    )
    unrelated = KnowledgeObject(
        object_id="object-regular",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        type_uri="type-regular@1",
        schema_uri="schema-regular@1",
        schema_version="1",
        payload={"marker": "synthetic"},
        origin="synthetic-extractor",
        scope=scope,
        evidence_refs=(evidence,),
        status="APPROVED",
    )
    version = KnowledgeVersion(
        knowledge_version="knowledge-graph-v1",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        claim_refs=(first.claim_id,),
        relation_refs=(second.relation_id,),
        object_refs=(unrelated.object_id,),
        source_snapshot_refs=("snapshot-source-synthetic",),
        actor=scope.actor,
    )
    provider = OAGLiteProjectionProvider(records=(first, second, unrelated))
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))
    manifest = runner.run(provider.projection_id, version)

    assert manifest.status == "BUILT"
    assert {record["kind"] for record in manifest.records} == {"CLAIM", "RELATION"}
    result = provider.query(
        GraphQuery(
            start_ref="node-a",
            target_ref="node-c",
            work_id=scope.work_id,
            branch_id=scope.branch_id,
            knowledge_version=version.knowledge_version,
            relation_types=("connects", "causal-link"),
            max_hops=3,
        )
    )
    assert result.status == "OK"
    assert result.paths == (("node-a", "node-b", "node-c"),)
    assert set(result.result_refs) == {first.claim_id, second.relation_id}
    assert all(ref["source_snapshot_ref"] == "snapshot-source-synthetic" for ref in result.evidence_refs)

    other_scope = _scope("work-other")
    other = Relation(
        relation_id="relation-outside",
        relation_type="causal-link",
        subject_ref="node-a",
        object_ref="node-outside",
        scope=other_scope,
        evidence_refs=(_evidence(other_scope, "other"),),
    )
    provider.records = (first, second, unrelated, other)
    rebuilt = runner.rebuild(provider.projection_id, version)
    assert rebuilt.projection_hash == manifest.projection_hash


def test_wiki_is_approved_only_and_annotation_returns_candidate() -> None:
    scope = _scope("work-wiki")
    evidence = _evidence(scope, "wiki")
    approved = KnowledgeObject(
        object_id="object-approved",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        type_uri="type-regular@1",
        schema_uri="schema-regular@1",
        schema_version="1",
        payload={"marker": "approved-synthetic"},
        origin="synthetic-extractor",
        scope=scope,
        evidence_refs=(evidence,),
        status="APPROVED",
    )
    candidate = approved.model_copy(update={"object_id": "object-candidate", "status": "CANDIDATE"})
    version = KnowledgeVersion(
        knowledge_version="knowledge-wiki-v1",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        object_refs=(approved.object_id, candidate.object_id),
        source_snapshot_refs=("snapshot-source-synthetic",),
        actor=scope.actor,
    )
    provider = WikiProjectionProvider(records=(approved, candidate))
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))
    manifest = runner.run("wiki", version)
    assert manifest.status == "BUILT"
    assert manifest.record_count == 1
    page = provider.pages[0]
    assert page.object_ref == approved.object_id
    assert page.status == "APPROVED"
    assert version.knowledge_version in page.body
    assert "Evidence:" in page.body
    assert "Decision:" in page.body
    assert "Conflict:" in page.body
    assert "Staleness:" in page.body

    before = page.page_hash
    annotation = provider.annotate(
        page.page_id,
        {"note": "synthetic annotation"},
        actor=Actor(actor_id="actor-test", role="reviewer", scope=scope),
    )
    assert annotation.status == "CANDIDATE"
    assert annotation.payload["page_ref"] == page.page_id
    assert provider.get_page(page.page_id).page_hash == before


def test_wiki_conflict_page_exposes_decision_and_conflict_without_resolving_it() -> None:
    scope = _scope("work-conflict")
    evidence = _evidence(scope, "conflict")
    claim_a = Claim(
        claim_id="claim-conflict-a",
        subject_ref="node-a",
        predicate="state",
        value="value-a",
        object_type_uri="type-state@1",
        scope=scope,
        evidence_refs=(evidence,),
    )
    claim_b = claim_a.model_copy(update={"claim_id": "claim-conflict-b", "value": "value-b", "claim_hash": ""})
    conflict = ConflictSet(
        conflict_id="conflict-synthetic",
        claim_refs=(claim_a.claim_id, claim_b.claim_id),
        conflict_type="ASSERTION_VALUE_MISMATCH",
        candidate_solutions=(
            ConflictCandidate(candidate_id="candidate-a", claim_refs=(claim_a.claim_id,), description="retain a"),
            ConflictCandidate(candidate_id="candidate-b", claim_refs=(claim_b.claim_id,), description="retain b"),
        ),
        evidence_refs=(evidence,),
        scope=scope,
    )
    version = KnowledgeVersion(
        knowledge_version="knowledge-conflict-v1",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        claim_refs=(claim_a.claim_id, claim_b.claim_id),
        source_snapshot_refs=("snapshot-source-synthetic",),
        actor=scope.actor,
    )
    provider = WikiProjectionProvider(records=(claim_a, claim_b), conflicts=(conflict,))
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))
    manifest = runner.run("wiki", version)
    assert manifest.record_count == 3
    assert any(page.conflict_refs for page in provider.pages)
    assert all(page.status == "APPROVED" for page in provider.pages)
