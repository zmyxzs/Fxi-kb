"""Synthetic clean-room tests for the Fxi v3 HTTP lifecycle."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from fxi.api.server import create_app
from fxi.core.canonical import sha256_hex
from fxi.knowledge.candidate_service import candidate_input_hash
from fxi.knowledge.contracts import EvidenceRef, Scope
from fxi.storage.versioned_store import NORMALIZATION_VERSION


WORK_ID = "synthetic-work"
SOURCE_ID = "synthetic-source"
BRANCH_ID = "branch-main"
KNOWLEDGE_VERSION = "version-branch-main"


class SyntheticEvaluator:
    def evaluate(self, candidate, policy):
        del candidate, policy
        return {"status": "EVALUATED", "suitability": "SUITABLE"}


class SyntheticEvaluationReviewer:
    reviewer_id = "synthetic-evaluator-reviewer"
    reviewer_version = "synthetic-reviewer-v1"

    def review(self, candidate, policy):
        del candidate, policy
        return {
            "semantic_reviewer": self.reviewer_id,
            "semantic_reviewer_version": self.reviewer_version,
        }


def _configure_auth(monkeypatch) -> None:
    monkeypatch.setenv(
        "FXI_API_ACTORS_JSON",
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "synthetic-writer",
                    "roles": ["writer", "reviewer"],
                    "work_ids": [WORK_ID],
                },
                "approver-token": {
                    "actor_id": "synthetic-approver",
                    "roles": ["approver", "reviewer"],
                    "work_ids": [WORK_ID],
                },
                "reader-token": {
                    "actor_id": "synthetic-reader",
                    "roles": ["reader"],
                    "work_ids": [WORK_ID],
                },
            }
        ),
    )


def _headers(token: str, key: str) -> dict[str, str]:
    return {
        "X-Fxi-Actor-Token": token,
        "Idempotency-Key": key,
    }


def _coordinate(source_version: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "story-coordinate.v3",
        "work_id": WORK_ID,
        "branch_id": BRANCH_ID,
        "as_of": 0,
        "chapter_index": 1,
        "narrative_order": 1,
        "knowledge_version": KNOWLEDGE_VERSION,
    }
    if source_version is not None:
        result.update({"source_id": SOURCE_ID, "source_version": source_version})
    return result


def _bootstrap_source(client: TestClient, temp_workspace) -> tuple[str, str]:
    source_dir = temp_workspace.sources_dir / SOURCE_ID
    source_dir.mkdir(parents=True, exist_ok=True)
    text = "Synthetic evidence for a clean-room contract test."
    (source_dir / "chapter.md").write_text(text, encoding="utf-8")
    source_version = sha256_hex(text)

    project = client.post(
        "/v3/projects",
        headers=_headers("writer-token", "project-create"),
        json={"work_id": WORK_ID, "slug": WORK_ID, "title": "Synthetic Work"},
    )
    assert project.status_code == 200, project.text

    binding = client.post(
        "/v3/sources/bindings",
        headers=_headers("writer-token", "source-bind"),
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
        headers=_headers("writer-token", "source-snapshot"),
        json={
            "coordinate": _coordinate(source_version),
            "source_id": SOURCE_ID,
            "source_version": source_version,
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    return source_version, snapshot.json()["result"]["snapshot_ref"]


def test_v3_project_source_snapshot_candidate_and_scope(temp_workspace, monkeypatch) -> None:
    _configure_auth(monkeypatch)
    app = create_app(temp_workspace)
    client = TestClient(app)
    source_version, snapshot_ref = _bootstrap_source(client, temp_workspace)

    # Replaying a project operation is served from the durable manifest.
    replay = client.post(
        "/v3/projects",
        headers=_headers("writer-token", "project-create"),
        json={"work_id": WORK_ID, "slug": WORK_ID, "title": "Synthetic Work"},
    )
    assert replay.status_code == 200
    assert replay.json()["trace_id"] == client.post(
        "/v3/projects",
        headers=_headers("writer-token", "project-create"),
        json={"work_id": WORK_ID, "slug": WORK_ID, "title": "Synthetic Work"},
    ).json()["trace_id"]

    evidence = {
        "source_id": SOURCE_ID,
        "source_version": source_version,
        "document_id": "document-1",
        "start_char": 0,
        "end_char": len("Synthetic evidence for a clean-room contract test."),
        "excerpt_hash": sha256_hex("Synthetic evidence for a clean-room contract test."),
    }
    payload = {"record_id": "record-synthetic", "label": "Synthetic record"}
    policy_hash = sha256_hex({"policy": "synthetic"})
    scoped_evidence = EvidenceRef(
        source_snapshot_ref=snapshot_ref,
        source_id=SOURCE_ID,
        source_version=source_version,
        document_id="document-1",
        start=0,
        end=evidence["end_char"],
        excerpt_hash=evidence["excerpt_hash"],
        normalization_version=NORMALIZATION_VERSION,
        scope=Scope(
            work_id=WORK_ID,
            branch_id=BRANCH_ID,
            as_of=0,
            purpose="candidate",
            actor="synthetic-writer",
        ),
        license="synthetic-license",
    )
    candidate = {
        "schema_version": "learning-artifact.v3",
        "artifact_id": "artifact-synthetic",
        "artifact_kind": "novel.character@1",
        "work_id": WORK_ID,
        "source_snapshot_ref": snapshot_ref,
        "evidence_refs": [evidence],
        "extractor_id": "synthetic-extractor",
        "domain_package_version": "1",
        "policy_hash": policy_hash,
        "payload": payload,
        "payload_hash": sha256_hex(payload),
    }
    candidate["input_hash"] = candidate_input_hash(
        {
            **candidate,
            "branch_id": BRANCH_ID,
            "evidence_refs": [scoped_evidence.model_dump(mode="json")],
        }
    )

    invalid_hash = dict(candidate)
    invalid_hash["input_hash"] = "0" * 64
    bad = client.post(
        "/v3/candidates",
        headers=_headers("writer-token", "candidate-invalid"),
        json=invalid_hash,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "INVALID_HASH"

    accepted = client.post(
        "/v3/candidates",
        headers=_headers("writer-token", "candidate-submit"),
        json=candidate,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["result"]["status"] == "CANDIDATE"

    forbidden = client.post(
        "/v3/decisions",
        headers=_headers("writer-token", "decision-writer-approve"),
        json={"candidate_id": "artifact-synthetic", "action": "APPROVE"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "AUTHORIZATION_FAILED"


def test_v3_evaluation_decision_and_promotion_fail_closed_without_approval(temp_workspace, monkeypatch) -> None:
    _configure_auth(monkeypatch)
    evaluation_reviewer = SyntheticEvaluationReviewer()
    app = create_app(
        temp_workspace,
        evaluator=SyntheticEvaluator(),
        services={"evaluation_semantic_reviewer": evaluation_reviewer},
    )
    client = TestClient(app)
    source_version, snapshot_ref = _bootstrap_source(client, temp_workspace)
    text = "Synthetic evidence for a clean-room contract test."
    evidence = {
        "source_id": SOURCE_ID,
        "source_version": source_version,
        "document_id": "document-1",
        "start_char": 0,
        "end_char": len(text),
        "excerpt_hash": sha256_hex(text),
    }
    payload = {"record_id": "record-evaluable", "label": "Synthetic evaluable"}
    candidate = {
        "schema_version": "learning-artifact.v3",
        "artifact_id": "artifact-evaluable",
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
    scoped_evidence = EvidenceRef(
        source_snapshot_ref=snapshot_ref,
        source_id=SOURCE_ID,
        source_version=source_version,
        document_id="document-1",
        start=0,
        end=len(text),
        excerpt_hash=sha256_hex(text),
        normalization_version=NORMALIZATION_VERSION,
        scope=Scope(
            work_id=WORK_ID,
            branch_id=BRANCH_ID,
            as_of=0,
            purpose="candidate",
            actor="synthetic-writer",
        ),
        license="synthetic-license",
    )
    candidate["input_hash"] = candidate_input_hash(
        {
            **candidate,
            "branch_id": BRANCH_ID,
            "evidence_refs": [scoped_evidence.model_dump(mode="json")],
        }
    )
    submitted = client.post(
        "/v3/candidates",
        headers=_headers("writer-token", "candidate-evaluable"),
        json=candidate,
    )
    assert submitted.status_code == 200, submitted.text

    policy = {
        "policy_id": "synthetic-policy",
        "policy_hash": sha256_hex({"policy": "synthetic"}),
        "evaluator_id": "synthetic-evaluator",
        "evaluator_version": "synthetic-evaluator-v1",
    }
    evaluated = client.post(
        "/v3/evaluations",
        headers=_headers("writer-token", "candidate-evaluate"),
        json={"candidate_id": "artifact-evaluable", "policy": policy},
    )
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["result"]["suitability"] == "SUITABLE"

    decided = client.post(
        "/v3/decisions",
        headers=_headers("approver-token", "candidate-decision"),
        json={"candidate_id": "artifact-evaluable", "action": "APPROVE"},
    )
    assert decided.status_code == 200, decided.text

    promotion = client.post(
        "/v3/promotions",
        headers=_headers("approver-token", "candidate-promotion"),
        json={
            "candidate_id": "artifact-evaluable",
            "approval_id": "approval-not-registered",
            "approval_hash": sha256_hex({"approval": "missing"}),
            "expected_head": KNOWLEDGE_VERSION,
        },
    )
    assert promotion.status_code == 409
    assert promotion.json()["error"]["code"] == "APPROVAL_REQUIRED"


def test_v3_auth_scope_and_schema_errors_are_fail_closed(temp_workspace, monkeypatch) -> None:
    _configure_auth(monkeypatch)
    app = create_app(temp_workspace)
    client = TestClient(app)

    reader = client.post(
        "/v3/projects",
        headers=_headers("reader-token", "reader-project"),
        json={"work_id": WORK_ID, "forged": "field"},
    )
    assert reader.status_code == 422
    assert reader.json()["error"]["code"] == "INCOMPLETE_INPUT"

    forbidden = client.post(
        "/v3/projects",
        headers=_headers("reader-token", "reader-project-valid"),
        json={"work_id": WORK_ID},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "AUTHORIZATION_FAILED"

    foreign = client.get(
        "/v3/capabilities",
        params={"work_id": "foreign-work"},
        headers={"X-Fxi-Actor-Token": "writer-token", "X-Request-ID": "trace-safe"},
    )
    assert foreign.status_code == 403
    assert foreign.json()["error"]["trace_id"] == "trace-trace-safe"
