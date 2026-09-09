"""Focused regression coverage for the v2 review-to-commit boundary."""

import hashlib
import json

from fastapi.testclient import TestClient

from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.registry import WorkRegistry
from fxi.api.server import create_app
from fxi.api.contracts import ChapterCommitRequestV2, ChapterProposalRequestV2, WritingReviewRequestV2
from fxi.core.config import FxiConfig
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
from fxi.api import router_v2


def _hash(value):
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prepare_workspace(config: FxiConfig) -> str:
    text = "Chapter one\nInitial fact."
    source_dir = config.sources_dir / "book"
    chapters = source_dir / "chapters"
    chapters.mkdir(parents=True)
    (chapters / "ch001.md").write_text(text, encoding="utf-8")
    source_version = _hash(text)
    (source_dir / "source.yaml").write_text(f"sha256: {source_version}\n", encoding="utf-8")
    (config.projects_dir / "integrity_work").mkdir(parents=True)
    (config.projects_dir / "integrity_work" / "work.yaml").write_text("continuity_rules: []\n", encoding="utf-8")

    database = DatabaseClient(config.sqlite_path)
    with database.transaction() as cur:
        ensure_work(cur, "integrity_work")
    WorkRegistry.from_database_client(database).register_source(
        "integrity_work",
        "book",
        source_dir="book",
        source_version=source_version,
    )
    return source_version


def test_review_proposal_approval_commit_is_bound_and_atomic(
    temp_workspace: FxiConfig,
    monkeypatch,
):
    source_version = _prepare_workspace(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "writer-1",
                    "roles": ["writer", "reviewer"],
                    "work_ids": ["integrity_work"],
                },
                "approver-token": {
                    "actor_id": "approver-1",
                    "roles": ["approver"],
                    "work_ids": ["integrity_work"],
                },
            }
        ),
    )
    app = create_app(temp_workspace)
    seen_review_inputs = []

    def semantic_reviewer(request):
        assert isinstance(request, WritingReviewRequestV2)
        seen_review_inputs.append(request)
        return {
            "check_id": "semantic_review",
            "status": "PASSED",
            "input_text_hash": request.text_hash,
        }

    app.state.semantic_reviewer = semantic_reviewer
    client = TestClient(app)
    writer_headers = {"X-Fxi-Actor-Token": "writer-token"}
    approver_headers = {"X-Fxi-Actor-Token": "approver-token"}

    snapshot = client.post(
        "/v2/sources/book/snapshots",
        headers=writer_headers,
        json={
            "work_id": "integrity_work",
            "source_id": "book",
            "expected_source_version": source_version,
            "idempotency_key": "snapshot-1",
        },
    )
    assert snapshot.status_code == 200

    text = "The hero leaves the room."
    review = client.post(
        "/v2/writing/review",
        headers=writer_headers,
        json={
            "work_id": "integrity_work",
            "source_id": "book",
            "source_version": source_version,
            "knowledge_version": "knowledge-v0",
            "chapter_index": 2,
            "text": text,
            "text_hash": _hash(text),
            "plan_hash": "plan-1",
            "context_hash": "context-1",
        },
    )
    assert review.status_code == 200
    assert review.json()["overall_status"] == "PASSED"
    assert seen_review_inputs and seen_review_inputs[0].text_hash == _hash(text)
    report_id = review.json()["report_id"]

    proposal_body = {
        "work_id": "integrity_work",
        "source_id": "book",
        "source_version": source_version,
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "text": text,
        "text_hash": _hash(text),
        "final_review_ref": report_id,
        "proposal_hash": "placeholder",
        "idempotency_key": "proposal-1",
    }
    proposal_body["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal_body)
    )
    proposal = client.post("/v2/writing/proposals", headers=writer_headers, json=proposal_body)
    assert proposal.status_code == 200
    proposal_id = proposal.json()["proposal_id"]

    approval = client.post(
        "/v2/approvals",
        headers=approver_headers,
        json={
            "action": "chapter_commit",
            "target_id": proposal_id,
            "target_hash": proposal_body["proposal_hash"],
            "expected_version": "knowledge-v0",
        },
    )
    assert approval.status_code == 200

    commit_body = {
        "work_id": "integrity_work",
        "source_id": "book",
        "source_version": source_version,
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "proposal_id": proposal_id,
        "proposal_hash": proposal_body["proposal_hash"],
        "pending_text": text,
        "text_hash": _hash(text),
        "final_review_reference": report_id,
        "approval_id": approval.json()["approval_id"],
        "expected_knowledge_version": "knowledge-v0",
        "idempotency_key": "commit-1",
        "payload_hash": "placeholder",
    }
    commit_body["payload_hash"] = router_v2._commit_payload_fingerprint(
        ChapterCommitRequestV2(**commit_body)
    )
    committed = client.post("/v2/writing/commits", headers=writer_headers, json=commit_body)
    assert committed.status_code == 200

    replay = client.post("/v2/writing/commits", headers=writer_headers, json=commit_body)
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["commit_id"] == committed.json()["commit_id"]

    database = DatabaseClient(temp_workspace.sqlite_path)
    with database.transaction() as cur:
        review_row = cur.execute(
            "SELECT status, source_id, source_version FROM v2_reviews WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        commit_id = committed.json()["commit_id"]
        projection_counts = cur.execute(
            "SELECT COUNT(*) AS count FROM v2_projection_tasks WHERE commit_id = ?",
            (commit_id,),
        ).fetchone()
        commit_row = cur.execute(
            "SELECT actor_id FROM v2_commits WHERE commit_id = ?",
            (commit_id,),
        ).fetchone()
    assert dict(review_row) == {
        "status": "PASSED",
        "source_id": "book",
        "source_version": source_version,
    }
    assert projection_counts["count"] == 5
    assert commit_row["actor_id"] == "writer-1"


def test_stale_review_cannot_create_a_committable_proposal(
    temp_workspace: FxiConfig,
    monkeypatch,
):
    source_version = _prepare_workspace(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "writer-1",
                    "roles": ["writer", "reviewer"],
                    "work_ids": ["integrity_work"],
                }
            }
        ),
    )
    app = create_app(temp_workspace)
    app.state.semantic_reviewer = lambda request: {
        "check_id": "semantic_review",
        "status": "PASSED",
        "input_text_hash": request.text_hash,
    }
    client = TestClient(app, headers={"X-Fxi-Actor-Token": "writer-token"})
    assert client.post(
        "/v2/sources/book/snapshots",
        json={
            "work_id": "integrity_work",
            "source_id": "book",
            "expected_source_version": source_version,
            "idempotency_key": "snapshot-stale",
        },
    ).status_code == 200

    text = "A stale review must not commit."
    review = client.post(
        "/v2/writing/review",
        json={
            "work_id": "integrity_work",
            "source_id": "book",
            "source_version": source_version,
            "knowledge_version": "knowledge-v0",
            "chapter_index": 2,
            "text": text,
            "text_hash": _hash(text),
            "plan_hash": "plan-stale",
            "context_hash": "context-stale",
        },
    )
    assert review.status_code == 200

    database = DatabaseClient(temp_workspace.sqlite_path)
    with database.transaction() as cur:
        cur.execute(
            "INSERT INTO v2_work_heads (work_id, knowledge_version, chapter_version, updated_at) VALUES (?, ?, ?, ?)",
            ("integrity_work", "knowledge-v1", "chapter-v1", "2026-09-07T00:00:00+00:00"),
        )

    proposal_body = {
        "work_id": "integrity_work",
        "source_id": "book",
        "source_version": source_version,
        "chapter_index": 2,
        "chapter_version": "chapter-v2",
        "text": text,
        "text_hash": _hash(text),
        "final_review_ref": review.json()["report_id"],
        "proposal_hash": "placeholder",
        "idempotency_key": "proposal-stale",
    }
    proposal_body["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal_body)
    )
    response = client.post("/v2/writing/proposals", json=proposal_body)

    assert response.status_code == 409
    with database.transaction() as cur:
        assert cur.execute("SELECT COUNT(*) FROM v2_proposals").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM v2_commits").fetchone()[0] == 0


def test_context_assembly_failure_is_structured(
    temp_workspace: FxiConfig,
    monkeypatch,
):
    source_version = _prepare_workspace(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "reader-token": {
                    "actor_id": "reader-1",
                    "roles": ["writer", "reader"],
                    "work_ids": ["integrity_work"],
                },
            }
        ),
    )
    app = create_app(temp_workspace)

    def fail_context(*args, **kwargs):
        raise RuntimeError("context failure")

    monkeypatch.setattr(router_v2.ContextPruner, "assemble_writing_context", fail_context)
    client = TestClient(app)
    headers = {"X-Fxi-Actor-Token": "reader-token"}
    snapshot = client.post(
        "/v2/sources/book/snapshots",
        headers=headers,
        json={
            "work_id": "integrity_work",
            "source_id": "book",
            "expected_source_version": source_version,
            "idempotency_key": "snapshot-context-failure",
        },
    )
    assert snapshot.status_code == 200

    response = client.post(
        "/v2/writing/context",
        headers=headers,
        json={
            "work_id": "integrity_work",
            "source_id": "book",
            "source_version": source_version,
            "pov_character_id": "hero",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CONTEXT_ASSEMBLY_FAILED"
