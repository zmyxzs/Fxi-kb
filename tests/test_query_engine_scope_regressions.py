"""QueryEngine 来源绑定、版本证据和空结果可见性的回归覆盖。"""

from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.index_retrieval.query_engine import QueryDecomposition, QueryEngine
from fxi.sources.evidence_store import EvidenceStore
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.versioned_store import SourceDocumentInput
from fxi.timeline.dag import CausalDAG


def _bind_source(config, source_id: str, work_id: str, version: str) -> None:
    with DatabaseClient(config.sqlite_path).transaction() as cur:
        cur.execute(
            """
            INSERT INTO work_sources
                (source_id, work_id, source_dir, source_version, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, work_id, work_id, version, "now"),
        )


def test_versioned_source_arguments_must_be_provided_as_a_pair(temp_workspace):
    engine = QueryEngine(temp_workspace)
    query = QueryDecomposition(target_entities=["主角"], keywords=["不存在"])

    for kwargs in (
        {"source_id": "source-a"},
        {"source_version": "version-a"},
    ):
        try:
            engine.retrieve_evidence("work_a", query, **kwargs)
        except ValueError:
            continue
        raise AssertionError("partial source scope was silently treated as an unversioned query")


def test_claim_evidence_from_another_work_is_not_returned(temp_workspace):
    _bind_source(temp_workspace, "source-a", "work_a", "version-a")
    _bind_source(temp_workspace, "source-b", "work_b", "version-b")
    with DatabaseClient(temp_workspace.sqlite_path).transaction() as cur:
        cur.execute(
            """
            INSERT INTO claim_families
                (family_key, claim_family_id, work_id, owner_id, scope, created_at)
            VALUES ('claim-family', 'claim-family', 'work_a', 'test-owner', 'public', 'now')
            """
        )
        cur.execute(
            """
            INSERT INTO claim_versions
                (family_key, claim_id, claim_family_id, version, work_id,
                 status, statement, semantic_hash, created_at)
            VALUES ('claim-family', 'claim-a', 'claim-family', 1, 'work_a',
                    'accepted', '作品 A 主张', 'hash-claim-a', 'now')
            """
        )
        cur.execute(
            """
            INSERT INTO claim_evidence
                (claim_id, source_id, scene_uuid, line_start, line_end, quote, created_at)
            VALUES ('claim-a', 'source-b', 'scene-b', 0, 1, '作品 B 引文', 'now')
            """
        )

    engine = QueryEngine(temp_workspace)
    with DatabaseClient(temp_workspace.sqlite_path).get_connection() as conn:
        claims = engine._load_claims(conn, "work_a")

    assert claims[0]["evidence"] == []


def test_registered_source_from_another_work_cannot_supply_versioned_scene(temp_workspace):
    source_text = "作品 B 的不可变来源正文"
    snapshot = EvidenceStore(temp_workspace.sources_dir).create_snapshot(
        "source-b",
        [
            SourceDocumentInput(
                document_id="chapter-1",
                chapter_index=1,
                raw_bytes=source_text.encode("utf-8"),
                text=source_text,
                relative_path="chapters/chapter-1.md",
            )
        ],
        version="version-b",
    )
    _bind_source(temp_workspace, "source-b", "work_b", snapshot.version)
    ChineseFTS(temp_workspace).index_scene(
        "scene-a", "work_a", 1, "作品 B 的不可变来源正文"
    )

    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a",
        QueryDecomposition(target_entities=["主角"], keywords=["不可变来源正文"]),
        source_id="source-b",
        source_version=snapshot.version,
    )

    scene = evidence["scenes"][0]
    assert scene["content_source"] == "fts_snippet"
    assert scene["content"] == ""
    assert not scene["document_available"]
    assert any(
        item["code"] == "SOURCE_NOT_BOUND_TO_WORK"
        for item in evidence["diagnostics"]
    )


def test_missing_versioned_snapshot_keeps_fts_as_non_fact_hint(temp_workspace):
    ChineseFTS(temp_workspace).index_scene(
        "scene-unversioned", "work_a", 1, "未版本化来源片段"
    )

    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a",
        QueryDecomposition(target_entities=["主角"], keywords=["未版本化来源片段"]),
        source_id="missing-source",
        source_version="missing-version",
    )

    scene = evidence["scenes"][0]
    assert scene["content_source"] == "fts_snippet"
    assert scene["content"] == ""
    assert not scene["document_available"]
    assert any(
        item["code"] == "SOURCE_SNAPSHOT_UNAVAILABLE"
        for item in evidence["diagnostics"]
    )


def test_empty_retrieval_is_explicitly_marked(temp_workspace):
    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a",
        QueryDecomposition(target_entities=["不存在的实体"], keywords=["绝不可能命中"]),
    )

    assert evidence["result_status"] == "EMPTY"
    assert any(
        item["code"] == "NO_EVIDENCE_FOUND"
        for item in evidence["diagnostics"]
    )


def test_causal_retrieval_isolated_by_timeline_and_source_provenance(temp_workspace):
    _bind_source(temp_workspace, "book-a", "work_a", "version-a")
    dag = CausalDAG(temp_workspace)
    dag.register_event(
        "main-event",
        "work_a",
        "scene-main",
        1,
        "t1",
        "主线事件",
        timeline_id="main",
        source_id="book-a",
        source_version="version-a",
    )
    dag.register_event(
        "branch-event",
        "work_a",
        "scene-branch",
        1,
        "t1",
        "分支事件",
        timeline_id="branch",
        source_id="book-a",
        source_version="version-a",
    )

    engine = QueryEngine(temp_workspace)
    main = engine.retrieve_evidence(
        "work_a",
        QueryDecomposition(keywords=["事件"]),
        source_id="book-a",
        source_version="version-a",
        timeline_id="main",
    )
    assert [event["event_id"] for event in main["events"]] == ["main-event"]
    assert main["evidence_blocks"]
    timeline_block = next(
        block for block in main["evidence_blocks"] if block["kind"] == "timeline"
    )
    assert timeline_block["status"] == "AVAILABLE"
    assert timeline_block["provenance"]

    branch = engine.retrieve_evidence(
        "work_a",
        QueryDecomposition(keywords=["事件"]),
        source_id="book-a",
        source_version="version-a",
        timeline_id="branch",
    )
    assert [event["event_id"] for event in branch["events"]] == ["branch-event"]


def test_legacy_causal_events_are_not_authoritative(temp_workspace):
    _bind_source(temp_workspace, "book-a", "work_a", "version-a")
    dag = CausalDAG(temp_workspace)
    dag.register_event(
        "legacy-event",
        "work_a",
        "scene-legacy",
        1,
        "t1",
        "旧事件",
        timeline_id="main",
    )

    evidence = QueryEngine(temp_workspace).retrieve_evidence(
        "work_a",
        QueryDecomposition(keywords=["旧事件"]),
        source_id="book-a",
        source_version="version-a",
        timeline_id="main",
    )

    assert evidence["events"][0]["provenance_status"] == "LEGACY_HINT"
    timeline_block = next(
        block for block in evidence["evidence_blocks"] if block["kind"] == "timeline"
    )
    assert timeline_block["status"] == "LEGACY_HINT"
    assert evidence["result_status"] == "LEGACY_HINT"
