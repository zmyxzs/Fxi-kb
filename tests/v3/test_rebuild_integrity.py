"""Synthetic F10 rebuild and non-replayable projection evidence."""

from __future__ import annotations

from hashlib import sha256

from fxi.knowledge.contracts import EvidenceRef, Scope
from fxi.knowledge.objects import Claim, KnowledgeVersion
from fxi.knowledge.projection_runner import LexicalProjectionProvider, ProjectionRunner
from fxi.storage.rebuild import RebuildService, Rebuilder
from fxi.storage.sqlite_client import DatabaseClient


def _authority() -> tuple[Claim, KnowledgeVersion]:
    scope = Scope(work_id="synthetic-rebuild", branch_id="branch-main", as_of=1, purpose="f10-test", actor="actor")
    evidence = EvidenceRef(
        evidence_id="evidence-rebuild",
        source_snapshot_ref="snapshot-rebuild",
        source_id="source-rebuild",
        source_version="v1",
        document_id="document-rebuild",
        start=0,
        end=8,
        excerpt_hash=sha256(b"synthetic").hexdigest(),
        normalization_version="normalization-1",
        scope=scope,
    )
    claim = Claim(
        claim_id="claim-rebuild",
        subject_ref="subject-rebuild",
        predicate="has-property",
        value="synthetic",
        object_type_uri="type-generic@1",
        scope=scope,
        evidence_refs=(evidence,),
    )
    version = KnowledgeVersion(
        knowledge_version="knowledge-rebuild",
        work_id=scope.work_id,
        branch_id=scope.branch_id,
        claim_refs=(claim.claim_id,),
        source_snapshot_refs=(evidence.source_snapshot_ref,),
        status="APPROVED",
        actor=scope.actor,
    )
    return claim, version


def test_selected_projection_rebuild_keeps_authority_hashes() -> None:
    claim, version = _authority()
    runner = ProjectionRunner(
        providers=(LexicalProjectionProvider(records=(claim,)),),
        knowledge_versions=(version,),
    )
    first = runner.run("fts", version)
    authority_hashes = (claim.claim_hash, version.version_hash)
    service = RebuildService(projection_runner=runner)

    report = service.rebuild(("fts@knowledge-rebuild",))

    assert report.success is True
    assert report.status == "PASSED"
    assert report.migration_report is not None
    assert report.migration_report.success is True
    assert report.projection_manifests[0]["status"] == "BUILT"
    assert report.projection_manifests[0]["projection_hash"] == first.projection_hash
    assert (claim.claim_hash, version.version_hash) == authority_hashes


def test_projection_rebuild_reports_unsupported_without_claiming_success() -> None:
    _, version = _authority()
    runner = ProjectionRunner(knowledge_versions=(version,))
    report = RebuildService(projection_runner=runner).rebuild(("vector",))

    assert report.success is False
    assert report.status == "FAILED"
    assert report.projection_manifests == ()
    assert any("NOT_FOUND" in error for error in report.errors)


def test_unknown_selector_does_not_mutate_projection_runner() -> None:
    _, version = _authority()
    provider = LexicalProjectionProvider()
    runner = ProjectionRunner(providers=(provider,), knowledge_versions=(version,))
    before = dict(runner.manifests)

    report = RebuildService(projection_runner=runner).rebuild(("unknown-projection",))

    assert report.success is False
    assert report.status == "FAILED"
    assert dict(runner.manifests) == before


def test_legacy_rebuild_blocks_before_clearing_non_replayable_projection(temp_workspace) -> None:
    client = DatabaseClient(temp_workspace.sqlite_path)
    with client.transaction() as cur:
        cur.execute(
            "INSERT INTO causal_events "
            "(event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("stale-event", "work_rebuild", "main", "synthetic-scene", 1, "unknown", "synthetic", 0, "stale"),
        )

    report = Rebuilder(temp_workspace).rebuild_all()

    assert report.success is False
    assert report.status == "BLOCKED"
    assert report.migration_report is not None
    assert report.migration_report.status == "BLOCKED"
    assert report.cleared_tables == []
    with client.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM causal_events WHERE event_id = ?", ("stale-event",)).fetchone()[0] == 1
