"""Regression coverage for the EvidenceStore-backed v2 closure."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fxi.api import router_v2
from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.contracts import ChapterCommitRequestV2, ChapterProposalRequestV2
from fxi.api.registry import WorkRegistry
from fxi.api.server import create_app
from fxi.core.config import FxiConfig
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.fixture
def closure_env(tmp_path: Path, monkeypatch):
    root = tmp_path / "v2-closure"
    paths = {name: root / name for name in ("data", "projects", "skills", "sources", "materials")}
    config = FxiConfig(
        workspace_root=root,
        data_dir=paths["data"],
        projects_dir=paths["projects"],
        skills_dir=paths["skills"],
        sources_dir=paths["sources"],
        materials_dir=paths["materials"],
        sqlite_path=paths["data"] / "manifest.sqlite",
        cache_db_path=paths["data"] / "cache.sqlite",
        jieba_custom_dict_path=paths["data"] / "project_lexicon.txt",
    )
    config.ensure_directories()
    work_id = "closure_work"
    source_id = "book"
    source_text = "Chapter one\nInitial fact."
    source_dir = config.sources_dir / source_id
    chapter_path = source_dir / "chapters" / "ch001.md"
    chapter_path.parent.mkdir(parents=True)
    chapter_path.write_text(source_text, encoding="utf-8")
    source_version = _hash(source_text)
    (source_dir / "source.yaml").write_text(f"sha256: {source_version}\n", encoding="utf-8")
    (config.projects_dir / work_id).mkdir(parents=True)
    (config.projects_dir / work_id / "work.yaml").write_text("continuity_rules: []\n", encoding="utf-8")

    database = DatabaseClient(config.sqlite_path)
    with database.transaction() as cur:
        ensure_work(cur, work_id)
    WorkRegistry.from_database_client(database).register_source(
        work_id,
        source_id,
        source_dir=source_id,
        source_version=source_version,
    )
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "writer-1",
                    "roles": ["writer", "reviewer"],
                    "work_ids": [work_id],
                },
                "reader-token": {
                    "actor_id": "reader-1",
                    "roles": ["reader"],
                    "work_ids": [work_id],
                },
                "approver-token": {
                    "actor_id": "approver-1",
                    "roles": ["approver"],
                    "work_ids": [work_id],
                },
            }
        ),
    )
    app = create_app(config)
    app.state.semantic_reviewer = lambda request: {
        "check_id": "semantic_review",
        "status": "PASSED",
        "input_text_hash": request.text_hash,
    }
    yield {
        "app": app,
        "config": config,
        "work_id": work_id,
        "source_id": source_id,
        "source_version": source_version,
        "source_dir": source_dir,
        "writer": TestClient(app, headers={"X-Fxi-Actor-Token": "writer-token"}),
        "reader": TestClient(app, headers={"X-Fxi-Actor-Token": "reader-token"}),
        "approver": TestClient(app, headers={"X-Fxi-Actor-Token": "approver-token"}),
    }


def _create_snapshot(env):
    return env["writer"].post(
        f"/v2/sources/{env['source_id']}/snapshots",
        json={
            "work_id": env["work_id"],
            "source_id": env["source_id"],
            "expected_source_version": env["source_version"],
            "idempotency_key": "snapshot-1",
        },
    )


def _review(env, *, required_checks=None, chapter_index=2):
    text = "The hero leaves."
    payload = {
        "work_id": env["work_id"],
        "source_id": env["source_id"],
        "source_version": env["source_version"],
        "knowledge_version": "knowledge-v0",
        "chapter_index": chapter_index,
        "text": text,
        "text_hash": _hash(text),
        "plan_hash": "plan-1",
        "context_hash": "context-1",
    }
    if required_checks is not None:
        payload["required_checks"] = required_checks
    return env["writer"].post("/v2/writing/review", json=payload), text


def test_snapshot_context_and_document_use_immutable_objects(closure_env):
    env = closure_env
    assert _create_snapshot(env).status_code == 200

    source_file = env["source_dir"] / "chapters" / "ch001.md"
    source_file.write_text("Changed live source", encoding="utf-8")
    document = env["reader"].get(
        f"/v2/sources/book/snapshots/{env['source_version']}/documents/ch001"
    )
    assert document.status_code == 200
    assert document.json()["content"] == "Chapter one\nInitial fact."
    context = env["reader"].post(
        "/v2/writing/context",
        json={
            "work_id": env["work_id"],
            "source_id": env["source_id"],
            "source_version": env["source_version"],
            "pov_character_id": "hero",
        },
    )
    assert context.status_code == 200

    object_file = (
        env["config"].sources_dir
        / "objects"
        / env["source_id"]
        / env["source_version"]
        / "ch001"
        / "normalized.txt"
    )
    object_file.write_text("Corrupted immutable object", encoding="utf-8")
    corrupted_context = env["reader"].post(
        "/v2/writing/context",
        json={
            "work_id": env["work_id"],
            "source_id": env["source_id"],
            "source_version": env["source_version"],
        },
    )
    assert corrupted_context.status_code == 503
    assert corrupted_context.json()["error"]["code"] == "SOURCE_SNAPSHOT_UNAVAILABLE"


def test_explicit_semantic_required_check_is_covered(closure_env):
    env = closure_env
    assert _create_snapshot(env).status_code == 200
    review, _ = _review(env, required_checks=["semantic_review"])
    assert review.status_code == 200
    assert review.json()["overall_status"] == "PASSED"
    assert [(item["check_id"], item["status"]) for item in review.json()["checks"]] == [
        ("semantic_review", "PASSED")
    ]


def test_incomplete_required_check_cannot_create_proposal(closure_env):
    env = closure_env
    assert _create_snapshot(env).status_code == 200
    review, text = _review(env, required_checks=["continuity", "unsupported_check"])
    assert review.status_code == 200
    assert review.json()["overall_status"] == "INCOMPLETE"
    proposal = {
        "work_id": env["work_id"],
        "source_id": env["source_id"],
        "source_version": env["source_version"],
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "text": text,
        "text_hash": _hash(text),
        "final_review_ref": review.json()["report_id"],
        "proposal_hash": "placeholder",
        "idempotency_key": "proposal-1",
    }
    proposal["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal)
    )
    response = env["writer"].post("/v2/writing/proposals", json=proposal)
    assert response.status_code == 409


def test_tampered_review_dependency_cannot_create_a_proposal(closure_env):
    env = closure_env
    assert _create_snapshot(env).status_code == 200
    review, text = _review(env)
    assert review.json()["overall_status"] == "PASSED"
    report_id = review.json()["report_id"]

    database = DatabaseClient(env["config"].sqlite_path)
    with database.transaction() as cur:
        row = cur.execute(
            "SELECT payload_json FROM v2_reviews WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["dependency_hash"] = "tampered"
        cur.execute(
            "UPDATE v2_reviews SET payload_json = ? WHERE report_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), report_id),
        )

    proposal = {
        "work_id": env["work_id"],
        "source_id": env["source_id"],
        "source_version": env["source_version"],
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "text": text,
        "text_hash": _hash(text),
        "final_review_ref": report_id,
        "proposal_hash": "placeholder",
        "idempotency_key": "proposal-tampered-review",
    }
    proposal["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal)
    )
    response = env["writer"].post("/v2/writing/proposals", json=proposal)

    assert response.status_code == 409
    with database.transaction() as cur:
        assert cur.execute("SELECT COUNT(*) FROM v2_proposals").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM v2_commits").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["exception", "hash", "unknown"])
def test_semantic_adapter_failures_never_create_committed_state(closure_env, failure):
    env = closure_env
    assert _create_snapshot(env).status_code == 200

    def semantic_reviewer(request):
        if failure == "exception":
            raise TimeoutError("semantic reviewer timed out")
        return {
            "check_id": "semantic_review",
            "status": "UNKNOWN" if failure == "unknown" else "PASSED",
            "input_text_hash": "wrong-hash" if failure == "hash" else request.text_hash,
        }

    env["app"].state.semantic_reviewer = semantic_reviewer
    text = "The semantic reviewer is unavailable."
    review = env["writer"].post(
        "/v2/writing/review",
        json={
            "work_id": env["work_id"],
            "source_id": env["source_id"],
            "source_version": env["source_version"],
            "knowledge_version": "knowledge-v0",
            "chapter_index": 2,
            "text": text,
            "text_hash": _hash(text),
            "plan_hash": "plan-failure",
            "context_hash": "context-failure",
            "required_checks": ["semantic_review"],
        },
    )
    assert review.status_code == 200
    assert review.json()["overall_status"] == "INCOMPLETE"

    proposal = {
        "work_id": env["work_id"],
        "source_id": env["source_id"],
        "source_version": env["source_version"],
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "text": text,
        "text_hash": _hash(text),
        "final_review_ref": review.json()["report_id"],
        "proposal_hash": "placeholder",
        "idempotency_key": f"proposal-failure-{failure}",
    }
    proposal["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal)
    )
    response = env["writer"].post("/v2/writing/proposals", json=proposal)

    assert response.status_code == 409
    with DatabaseClient(env["config"].sqlite_path).transaction() as cur:
        assert cur.execute("SELECT COUNT(*) FROM v2_commits").fetchone()[0] == 0


def test_commit_binds_source_review_approval_and_is_idempotent(closure_env):
    env = closure_env
    assert _create_snapshot(env).status_code == 200
    review, text = _review(env)
    assert review.json()["overall_status"] == "PASSED"
    proposal = {
        "work_id": env["work_id"],
        "source_id": env["source_id"],
        "source_version": env["source_version"],
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "text": text,
        "text_hash": _hash(text),
        "final_review_ref": review.json()["report_id"],
        "proposal_hash": "placeholder",
        "idempotency_key": "proposal-1",
    }
    proposal["proposal_hash"] = router_v2._proposal_payload_fingerprint(
        ChapterProposalRequestV2(**proposal)
    )
    proposal_response = env["writer"].post("/v2/writing/proposals", json=proposal)
    assert proposal_response.status_code == 200
    proposal_id = proposal_response.json()["proposal_id"]
    approval_response = env["approver"].post(
        "/v2/approvals",
        json={
            "work_id": env["work_id"],
            "action": "chapter_commit",
            "target_id": proposal_id,
            "target_hash": proposal["proposal_hash"],
            "expected_version": "knowledge-v0",
        },
    )
    assert approval_response.status_code == 200
    commit = {
        "work_id": env["work_id"],
        "source_id": env["source_id"],
        "source_version": env["source_version"],
        "chapter_index": 2,
        "chapter_version": "chapter-v1",
        "proposal_id": proposal_id,
        "proposal_hash": proposal["proposal_hash"],
        "pending_text": text,
        "text_hash": _hash(text),
        "final_review_reference": review.json()["report_id"],
        "approval_id": approval_response.json()["approval_id"],
        "expected_knowledge_version": "knowledge-v0",
        "idempotency_key": "commit-1",
        "payload_hash": "placeholder",
    }
    commit["payload_hash"] = router_v2._commit_payload_fingerprint(
        ChapterCommitRequestV2(**commit)
    )
    object_file = (
        env["config"].sources_dir
        / "objects"
        / env["source_id"]
        / env["source_version"]
        / "ch001"
        / "normalized.txt"
    )
    object_file.write_text("Corrupted immutable object", encoding="utf-8")
    corrupted_commit = env["writer"].post("/v2/writing/commits", json=commit)
    assert corrupted_commit.status_code == 503
    assert corrupted_commit.json()["error"]["code"] == "SOURCE_SNAPSHOT_UNAVAILABLE"
    object_file.write_text("Chapter one\nInitial fact.", encoding="utf-8")
    committed = env["writer"].post("/v2/writing/commits", json=commit)
    assert committed.status_code == 200
    replay = env["writer"].post("/v2/writing/commits", json=commit)
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    duplicate = dict(commit, idempotency_key="commit-duplicate")
    duplicate["payload_hash"] = router_v2._commit_payload_fingerprint(
        ChapterCommitRequestV2(**duplicate)
    )
    assert env["writer"].post("/v2/writing/commits", json=duplicate).status_code == 409
    wrong_source = dict(commit, source_version="wrong-source", idempotency_key="commit-wrong-source")
    wrong_source["payload_hash"] = router_v2._commit_payload_fingerprint(
        ChapterCommitRequestV2(**wrong_source)
    )
    assert env["writer"].post("/v2/writing/commits", json=wrong_source).status_code == 404
    database = DatabaseClient(env["config"].sqlite_path)
    with database.transaction() as cur:
        commit_count = cur.execute(
            "SELECT COUNT(*) AS count FROM v2_commits WHERE work_id = ?",
            (env["work_id"],),
        ).fetchone()["count"]
        projection_count = cur.execute(
            "SELECT COUNT(*) AS count FROM v2_projection_tasks WHERE work_id = ?",
            (env["work_id"],),
        ).fetchone()["count"]
    assert commit_count == 1
    assert projection_count == 5
