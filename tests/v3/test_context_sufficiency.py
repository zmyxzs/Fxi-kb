"""Synthetic F6 knowledge sufficiency tests."""

from hashlib import sha256

from fxi.knowledge.contracts import ContextBlock, ContextView, EvidenceRef, Scope
from fxi.knowledge.knowledge_sufficiency import KnowledgeSufficiencyService
from fxi.knowledge.view_selector import ViewSelector


def _scope() -> Scope:
    return Scope(work_id="work-synthetic", branch_id="branch-main", as_of=1, purpose="query", actor="actor-test")


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


def test_sufficiency_reports_missing_requirements_instead_of_passed() -> None:
    scope = _scope()
    block = ContextBlock(
        block_id="block-1",
        type_uri="example.note@1",
        object_refs=("object-1",),
        content={"claim_id": "claim-1"},
        evidence_refs=(_evidence(),),
        scope=scope,
    )
    view = ContextView(
        view_id="view-1",
        work_id="work-synthetic",
        branch_id="branch-main",
        as_of=1,
        purpose="query",
        scope=scope,
        blocks=(block,),
        evidence_refs=(_evidence(),),
    )
    selector = ViewSelector(
        work_id="work-synthetic",
        branch_id="branch-main",
        as_of=1,
        purpose="query",
        type_uris=("example.missing@1",),
        required_claim_refs=("claim-missing",),
        required_evidence_refs=("evidence-missing",),
        capabilities=("vector",),
    )
    report = KnowledgeSufficiencyService(capabilities=("lexical",)).assess(selector, view)
    assert report.status == "INCOMPLETE"
    assert "type_uri:example.missing@1" in report.missing_required
    assert "claim_ref:claim-missing" in report.missing_required
    assert report.missing_capabilities == ("vector",)
    assert report.missing_evidence == ("evidence:evidence-missing",)

