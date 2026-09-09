"""Synthetic F6 selector/query tests."""

from hashlib import sha256

from fxi.knowledge.contracts import EvidenceRef, ErrorCode, QueryRequest, Scope
from fxi.knowledge.query_service import QueryService


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
        scope=Scope(work_id="work-synthetic", branch_id="branch-main", as_of=1, purpose="query", actor="actor-test"),
    )


def test_registered_intent_uses_lexical_baseline_and_returns_trace() -> None:
    evidence = _evidence()

    def lexical(request: QueryRequest) -> dict[str, object]:
        assert request.query == "synthetic"
        return {"result_refs": ("object-1",), "evidence_refs": (evidence,)}

    service = QueryService(lexical_baseline=lexical)
    result = service.query(QueryRequest(work_id="work-synthetic", branch_id="branch-main", as_of=1, purpose="lexical", query="synthetic"))
    assert result.status == "OK"
    assert result.code == "OK"
    assert result.result_refs == ("object-1",)
    assert result.source_versions == ("version-1",)
    assert result.evidence_refs == (evidence,)
    assert result.trace_id.startswith("trace-")


def test_unknown_and_vector_intents_are_explicit_errors() -> None:
    request = QueryRequest(work_id="work-synthetic", branch_id="branch-main", as_of=1, purpose="missing", query="synthetic")
    service = QueryService(lexical_baseline=lambda _: ())
    assert service.query(request).code == ErrorCode.NOT_FOUND.value
    service.register_intent("world-state", lambda _: ("object-1",), capabilities=("vector",))
    vector_request = request.model_copy(update={"purpose": "world-state", "capabilities": ("vector",)})
    assert service.query(vector_request).code == ErrorCode.CAPABILITY_UNSUPPORTED.value

