"""Synthetic HTTP clean-room tests for durable Fxi v3 authority state."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from fxi.api.server import create_app
from fxi.core.canonical import sha256_hex
from fxi.knowledge.candidate_service import candidate_input_hash
from fxi.knowledge.contracts import EvidenceRef, Scope, StateChangeSet, StoryCoordinate
from fxi.storage.versioned_store import NORMALIZATION_VERSION


WORK_ID = "persistence-work"
SOURCE_ID = "persistence-source"
BRANCH_ID = "branch-main"
GENESIS_VERSION = "version-branch-main"
SOURCE_TEXT = "Synthetic persistence evidence; no story asset is used."
SOURCE_VERSION = sha256_hex(SOURCE_TEXT)
SOURCE_END = len(SOURCE_TEXT)
ARTIFACT_HASH = sha256_hex("synthetic-draft")


class SyntheticEvaluator:
    def evaluate(self, candidate, policy):
        del candidate, policy
        return {"status": "EVALUATED", "suitability": "SUITABLE"}


class SyntheticEvaluationReviewer:
    def review(self, candidate, policy):
        del candidate, policy
        return {
            "semantic_reviewer": "persistence-evaluation-reviewer",
            "semantic_reviewer_version": "v1",
        }


class SyntheticWritingReviewer:
    reviewer_id = "persistence-writing-reviewer"
    reviewer_version = "v1"

    def review(self, request):
        del request
        return {
            "checks": [
                {
                    "check_id": "semantic",
                    "status": "PASSED",
                    "required": True,
                }
            ]
        }


def _configure_auth(monkeypatch) -> None:
    monkeypatch.setenv(
        "FXI_API_ACTORS_JSON",
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "persistence-writer",
                    "roles": ["writer", "reviewer"],
                    "work_ids": [WORK_ID],
                },
                "approver-token": {
                    "actor_id": "persistence-approver",
                    "roles": ["approver", "reviewer"],
                    "work_ids": [WORK_ID],
                },
            }
        ),
    )


def _headers(token: str, key: str) -> dict[str, str]:
    return {"X-Fxi-Actor-Token": token, "Idempotency-Key": key}


def _coordinate(knowledge_version: str = GENESIS_VERSION) -> dict[str, object]:
    return {
        "schema_version": "story-coordinate.v3",
        "work_id": WORK_ID,
        "branch_id": BRANCH_ID,
        "as_of": 0,
        "chapter_index": 1,
        "narrative_order": 1,
        "knowledge_version": knowledge_version,
        "source_id": SOURCE_ID,
        "source_version": SOURCE_VERSION,
    }


def _app(temp_workspace, *, evaluator=True):
    return create_app(
        temp_workspace,
        evaluator=SyntheticEvaluator() if evaluator else None,
        semantic_reviewer=SyntheticWritingReviewer(),
        services={
            "evaluation_semantic_reviewer": SyntheticEvaluationReviewer()
            if evaluator
            else None
        },
    )


def _bootstrap_source(client: TestClient, temp_workspace) -> str:
    source_dir = temp_workspace.sources_dir / SOURCE_ID
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "chapter.md").write_text(SOURCE_TEXT, encoding="utf-8")

    project = client.post(
        "/v3/projects",
        headers=_headers("writer-token", "persist-project"),
        json={"work_id": WORK_ID, "slug": WORK_ID, "title": "Synthetic Persistence Work"},
    )
    assert project.status_code == 200, project.text

    binding = client.post(
        "/v3/sources/bindings",
        headers=_headers("writer-token", "persist-binding"),
        json={
            "work_id": WORK_ID,
            "source_id": SOURCE_ID,
            "role": "reference",
            "branch_id": BRANCH_ID,
            "priority": 1,
            "license": "synthetic-license",
            "access": "local",
            "source_dir": SOURCE_ID,
        },
    )
    assert binding.status_code == 200, binding.text

    snapshot = client.post(
        "/v3/sources/snapshots",
        headers=_headers("writer-token", "persist-snapshot"),
        json={
            "coordinate": _coordinate(),
            "source_id": SOURCE_ID,
            "source_version": SOURCE_VERSION,
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    return str(snapshot.json()["result"]["snapshot_ref"])


def _candidate(snapshot_ref: str, artifact_id: str) -> dict[str, object]:
    evidence = {
        "source_id": SOURCE_ID,
        "source_version": SOURCE_VERSION,
        "document_id": "document-1",
        "start_char": 0,
        "end_char": SOURCE_END,
        "excerpt_hash": sha256_hex(SOURCE_TEXT),
    }
    scoped = EvidenceRef(
        source_snapshot_ref=snapshot_ref,
        source_id=SOURCE_ID,
        source_version=SOURCE_VERSION,
        document_id="document-1",
        start=0,
        end=SOURCE_END,
        excerpt_hash=sha256_hex(SOURCE_TEXT),
        normalization_version=NORMALIZATION_VERSION,
        scope=Scope(
            work_id=WORK_ID,
            branch_id=BRANCH_ID,
            as_of=0,
            purpose="candidate",
            actor="persistence-writer",
        ),
        license="synthetic-license",
    )
    payload = {
        "record_id": artifact_id,
        "label": "Synthetic persistence item",
        "attributes": {"origin": "clean-room"},
    }
    candidate: dict[str, object] = {
        "schema_version": "learning-artifact.v3",
        "artifact_id": artifact_id,
        "artifact_kind": "novel.item@1",
        "work_id": WORK_ID,
        "source_snapshot_ref": snapshot_ref,
        "evidence_refs": [evidence],
        "extractor_id": "synthetic-extractor",
        "domain_package_version": "1",
        "policy_hash": sha256_hex({"policy": "synthetic"}),
        "payload": payload,
        "payload_hash": sha256_hex(payload),
    }
    candidate["input_hash"] = candidate_input_hash(
        {
            **candidate,
            "branch_id": BRANCH_ID,
            "evidence_refs": [scoped.model_dump(mode="json")],
        }
    )
    return candidate


def _compile_context(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v3/context/views",
        headers=_headers("writer-token", "persist-context"),
        json={
            "coordinate": _coordinate(),
            "purpose": "writing",
            "budget": 100,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def _review_and_proposal(
    client: TestClient,
    context: dict[str, object],
    snapshot_ref: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    draft_hash = sha256_hex("synthetic-draft")
    review_response = client.post(
        "/v3/writing/reviews",
        headers=_headers("writer-token", "persist-review"),
        json={
            "draft_ref": "draft-synthetic",
            "draft_hash": draft_hash,
            "coordinate": _coordinate(),
            "context_view": context,
            "source_snapshot_ref": snapshot_ref,
            "source_id": SOURCE_ID,
            "source_version": SOURCE_VERSION,
            "required_checks": ["semantic"],
        },
    )
    assert review_response.status_code == 200, review_response.text
    review = review_response.json()["result"]
    coordinate = StoryCoordinate.model_validate(
        {key: value for key, value in _coordinate().items() if key != "schema_version"}
    )
    change_set = StateChangeSet(
        change_set_id="changes-synthetic",
        coordinate=coordinate,
        source_artifact_hash=ARTIFACT_HASH,
        changes=(),
        review_ref=review["report_id"],
    )
    head = client.app.state.branch_service.head(WORK_ID, BRANCH_ID).model_dump(mode="json")
    proposal_response = client.post(
        "/v3/writing/proposals",
        headers=_headers("writer-token", "persist-proposal"),
        json={
            "draft_ref": "draft-synthetic",
            "draft_hash": draft_hash,
            "review": review,
            "state_change_set": change_set.model_dump(mode="json"),
            "context_view": context,
            "knowledge_head": head,
            "source_snapshot_ref": snapshot_ref,
            "source_id": SOURCE_ID,
            "source_version": SOURCE_VERSION,
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal = proposal_response.json()["result"]
    return review, proposal, change_set.model_dump(mode="json")


def _approve(
    client: TestClient,
    proposal: dict[str, object],
    *,
    key: str,
) -> dict[str, object]:
    response = client.post(
        "/v3/approvals",
        headers=_headers("approver-token", key),
        json={
            "proposal_id": proposal["proposal_id"],
            "proposal_hash": proposal["proposal_hash"],
            "work_id": WORK_ID,
            "expected_version": GENESIS_VERSION,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_candidate_lifecycle_survives_app_restart(temp_workspace, monkeypatch) -> None:
    _configure_auth(monkeypatch)
    app = _app(temp_workspace)
    with TestClient(app) as client:
        snapshot_ref = _bootstrap_source(client, temp_workspace)
        candidate = _candidate(snapshot_ref, "artifact-persistence")
        submitted = client.post(
            "/v3/candidates",
            headers=_headers("writer-token", "persist-candidate"),
            json=candidate,
        )
        assert submitted.status_code == 200, submitted.text

        policy = {
            "policy_id": "persistence-policy",
            "policy_hash": sha256_hex({"policy": "synthetic"}),
            "evaluator_id": "persistence-evaluator",
            "evaluator_version": "v1",
        }
        evaluated = client.post(
            "/v3/evaluations",
            headers=_headers("writer-token", "persist-evaluation"),
            json={"candidate_id": candidate["artifact_id"], "policy": policy},
        )
        assert evaluated.status_code == 200, evaluated.text
        decided = client.post(
            "/v3/decisions",
            headers=_headers("approver-token", "persist-decision"),
            json={"candidate_id": candidate["artifact_id"], "action": "APPROVE"},
        )
        assert decided.status_code == 200, decided.text

        context = _compile_context(client)
        _, proposal, _ = _review_and_proposal(client, context, snapshot_ref)
        approval = _approve(client, proposal, key="persist-promotion-approval")
        promoted = client.post(
            "/v3/promotions",
            headers=_headers("approver-token", "persist-promotion"),
            json={
                "candidate_id": candidate["artifact_id"],
                "approval_id": approval["approval_id"],
                "approval_hash": approval["approval_hash"],
                "expected_head": GENESIS_VERSION,
            },
        )
        assert promoted.status_code == 200, promoted.text
        promoted_result = promoted.json()["result"]

    restarted = _app(temp_workspace)
    with TestClient(restarted) as client:
        replay = client.post(
            "/v3/evaluations",
            headers=_headers("writer-token", "persist-evaluation-after-restart"),
            json={"candidate_id": candidate["artifact_id"], "policy": policy},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["result"]["evaluation_id"] == evaluated.json()["result"]["evaluation_id"]
        assert restarted.state.candidate_service.get(candidate["artifact_id"]).payload == candidate["payload"]
        assert restarted.state.decision_service.get(decided.json()["result"]["decision_id"]).candidate_id == candidate["artifact_id"]
        receipt = restarted.state.promotion_service.repository.get_receipt(promoted_result["promotion_id"])
        assert receipt is not None
        assert restarted.state.promotion_service.repository.get_head(WORK_ID, BRANCH_ID).knowledge_version == promoted_result["new_knowledge_version"]
        assert restarted.state.approval_service.get(approval["approval_id"]).consumed is True


def test_review_commit_is_atomic_and_durable(temp_workspace, monkeypatch) -> None:
    _configure_auth(monkeypatch)
    app = _app(temp_workspace, evaluator=False)
    with TestClient(app) as client:
        snapshot_ref = _bootstrap_source(client, temp_workspace)
        context = _compile_context(client)
        review, proposal, change_set = _review_and_proposal(client, context, snapshot_ref)
        approval = _approve(client, proposal, key="persist-commit-approval")
        committed = client.post(
            "/v3/writing/commits",
            headers=_headers("approver-token", "persist-commit"),
            json={
                "proposal_id": proposal["proposal_id"],
                "proposal_hash": proposal["proposal_hash"],
                "approval_id": approval["approval_id"],
                "approval_hash": approval["approval_hash"],
                "expected_knowledge_version": GENESIS_VERSION,
                "chapter_version": "chapter-synthetic-1",
                "idempotency_key": "commit-synthetic-1",
                "chapter": {"text": "synthetic draft", "text_hash": sha256_hex("synthetic draft")},
                "projection_kinds": ["fts"],
            },
        )
        assert committed.status_code == 200, committed.text
        receipt = committed.json()["result"]
        assert receipt["status"] == "COMMITTED"

        persisted = app.state.commit_service.repository.get_receipt(WORK_ID, "commit-synthetic-1")
        assert persisted is not None
        assert persisted.commit_id == receipt["commit_id"]
        assert app.state.commit_service.repository.get_fingerprint(WORK_ID, "commit-synthetic-1")
        assert app.state.approval_service.get(approval["approval_id"]).consumed is True
        assert app.state.proposal_service.get_record(proposal["proposal_id"]).state_change_set.model_dump(mode="json") == change_set
        assert review["overall_status"] == "PASSED"

    restarted = _app(temp_workspace, evaluator=False)
    with TestClient(restarted):
        persisted = restarted.state.commit_service.repository.get_receipt(WORK_ID, "commit-synthetic-1")
        assert persisted is not None
        assert restarted.state.commit_service.repository.get_fingerprint(WORK_ID, "commit-synthetic-1")
        assert restarted.state.approval_service.get(approval["approval_id"]).consumed is True
