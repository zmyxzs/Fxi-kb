"""FX clean-room coverage for the public Fxi v3 boundary.

The test deliberately stays on the HTTP surface after creating a temporary
source tree.  It does not inspect ``app.state``, SQLite, projection internals,
or any repository asset; all authority mutations go through public v3 routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from fxi.api.server import create_app
from fxi.core.canonical import sha256_hex
from fxi.knowledge.contracts import EvidenceRef, Scope, StateChangeSet, StoryCoordinate
from fxi.storage.versioned_store import NORMALIZATION_VERSION


WORK_ID = "e2e-synthetic-work"
BRANCH_ID = "branch-main"
GENESIS_VERSION = "version-branch-main"
LICENSE = "synthetic-license"
CONTRACT_REVISION = "studio-fxi-v3.20260909"


class SyntheticEvaluator:
    """Deterministic evaluator used only for this temporary clean-room run."""

    def evaluate(self, candidate: Any, policy: Any) -> dict[str, str]:
        del candidate, policy
        return {"status": "EVALUATED", "suitability": "SUITABLE"}


class SyntheticEvaluationReviewer:
    reviewer_id = "e2e-evaluation-reviewer"
    reviewer_version = "e2e-v1"

    def review(self, candidate: Any, policy: Any) -> dict[str, str]:
        del candidate, policy
        return {
            "semantic_reviewer": self.reviewer_id,
            "semantic_reviewer_version": self.reviewer_version,
        }


class SyntheticWritingReviewer:
    reviewer_id = "e2e-writing-reviewer"
    reviewer_version = "e2e-v1"

    def review(self, request: Any) -> dict[str, Any]:
        del request
        return {
            "checks": [
                {
                    "check_id": "semantic",
                    "status": "PASSED",
                    "required": True,
                    "findings": [],
                }
            ]
        }


def _configure_auth(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        "FXI_API_ACTORS_JSON",
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "e2e-writer",
                    "roles": ["writer", "reviewer"],
                    "work_ids": [WORK_ID],
                },
                "approver-token": {
                    "actor_id": "e2e-approver",
                    "roles": ["approver", "reviewer"],
                    "work_ids": [WORK_ID],
                },
                "reader-token": {
                    "actor_id": "e2e-reader",
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


def _coordinate(
    knowledge_version: str,
    *,
    source_id: str | None = None,
    source_version: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "story-coordinate.v3",
        "work_id": WORK_ID,
        "branch_id": BRANCH_ID,
        "as_of": 0,
        "chapter_index": 1,
        "narrative_order": 1,
        "knowledge_version": knowledge_version,
        "source_id": source_id,
        "source_version": source_version,
    }


def _post_ok(
    client: TestClient,
    endpoint: str,
    token: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(endpoint, headers=_headers(token, key), json=payload)
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert envelope["code"] == "OK", envelope
    assert envelope["trace_id"].startswith("trace-")
    assert len(envelope["result_hash"]) == 64
    return envelope["result"]


def _candidate_input_hash(
    *,
    artifact_kind: str,
    source_id: str,
    source_version: str,
    snapshot_ref: str,
    source_text: str,
    payload: dict[str, Any],
) -> str:
    """Compute the wire hash from the frozen generic candidate fields.

    This is request preparation only.  Candidate creation itself is performed
    by ``POST /v3/candidates``; no candidate service or repository is called.
    """

    evidence = EvidenceRef(
        source_snapshot_ref=snapshot_ref,
        source_id=source_id,
        source_version=source_version,
        document_id="document-1",
        start=0,
        end=len(source_text),
        excerpt_hash=sha256_hex(source_text),
        normalization_version=NORMALIZATION_VERSION,
        scope=Scope(
            work_id=WORK_ID,
            branch_id=BRANCH_ID,
            as_of=0,
            purpose="candidate",
            actor="e2e-writer",
        ),
        license=LICENSE,
    )
    data = {
        "artifact_kind": artifact_kind,
        "work_id": WORK_ID,
        "branch_id": BRANCH_ID,
        "source_snapshot_ref": snapshot_ref,
        "evidence_refs": [evidence.model_dump(mode="json")],
        "extractor_id": "synthetic-extractor",
        "schema_version": "learning-artifact.v3",
        "domain_package_version": "1",
        "model_route": None,
        "prompt_hash": None,
        "policy_hash": sha256_hex({"policy": "e2e-synthetic"}),
        "budget_ref": None,
        "payload": payload,
    }
    return sha256_hex(data)


def _candidate(
    *,
    source_id: str,
    source_version: str,
    snapshot_ref: str,
    source_text: str,
    artifact_id: str,
    artifact_kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "learning-artifact.v3",
        "artifact_id": artifact_id,
        "artifact_kind": artifact_kind,
        "work_id": WORK_ID,
        "source_snapshot_ref": snapshot_ref,
        "evidence_refs": [
            {
                "source_id": source_id,
                "source_version": source_version,
                "document_id": "document-1",
                "start_char": 0,
                "end_char": len(source_text),
                "excerpt_hash": sha256_hex(source_text),
            }
        ],
        "input_hash": _candidate_input_hash(
            artifact_kind=artifact_kind,
            source_id=source_id,
            source_version=source_version,
            snapshot_ref=snapshot_ref,
            source_text=source_text,
            payload=payload,
        ),
        "extractor_id": "synthetic-extractor",
        "domain_package_version": "1",
        "policy_hash": sha256_hex({"policy": "e2e-synthetic"}),
        "payload": payload,
        "payload_hash": sha256_hex(payload),
    }


def _bootstrap_sources(client: TestClient, temp_workspace: Any) -> dict[str, dict[str, str]]:
    source_specs = (
        (
            "synthetic-inspiration",
            "inspiration",
            "Synthetic inspiration evidence for a clean-room run.",
        ),
        (
            "synthetic-reference",
            "reference",
            "Synthetic reference evidence for a clean-room run.",
        ),
    )
    for source_id, role, text in source_specs:
        source_dir = Path(temp_workspace.sources_dir) / source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "chapter.md").write_text(text, encoding="utf-8")

    _post_ok(
        client,
        "/v3/projects",
        "writer-token",
        "project-create",
        {"work_id": WORK_ID, "slug": WORK_ID, "title": "Synthetic Clean Room"},
    )

    result: dict[str, dict[str, str]] = {}
    for index, (source_id, role, text) in enumerate(source_specs, start=1):
        source_version = sha256_hex(text)
        _post_ok(
            client,
            "/v3/sources/bindings",
            "writer-token",
            f"source-bind-{index}",
            {
                "work_id": WORK_ID,
                "source_id": source_id,
                "role": role,
                "branch_id": BRANCH_ID,
                "priority": index,
                "license": LICENSE,
                "access": "local",
                "allowed_purposes": ["candidate", "context", "review", "query"],
                "source_dir": source_id,
            },
        )
        snapshot = _post_ok(
            client,
            "/v3/sources/snapshots",
            "writer-token",
            f"source-snapshot-{index}",
            {
                "coordinate": _coordinate(
                    GENESIS_VERSION,
                    source_id=source_id,
                    source_version=source_version,
                ),
                "source_id": source_id,
                "source_version": source_version,
            },
        )
        assert snapshot["source_id"] == source_id
        assert snapshot["source_version"] == source_version
        result[source_id] = {
            "source_id": source_id,
            "role": role,
            "source_version": source_version,
            "snapshot_ref": snapshot["snapshot_ref"],
            "text": text,
        }
    return result


def _compile_context(
    client: TestClient,
    knowledge_version: str,
    *,
    key: str,
) -> dict[str, Any]:
    return _post_ok(
        client,
        "/v3/context/views",
        "writer-token",
        key,
        {
            "coordinate": _coordinate(knowledge_version),
            "purpose": "writing",
            "budget": 400,
        },
    )


def _promote_candidate(
    client: TestClient,
    candidate: dict[str, Any],
    *,
    context: dict[str, Any],
    knowledge_version: str,
    payload: dict[str, Any],
    artifact_kind: str,
    evidence: EvidenceRef,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_id = str(candidate["artifact_id"])
    evaluation = _post_ok(
        client,
        "/v3/evaluations",
        "writer-token",
        f"evaluate-{artifact_id}",
        {
            "candidate_id": artifact_id,
            "policy": {
                "policy_id": "e2e-policy",
                "policy_hash": sha256_hex({"policy": "e2e-synthetic"}),
                "evaluator_id": "e2e-evaluator",
                "evaluator_version": "e2e-v1",
            },
        },
    )
    assert evaluation["suitability"] == "SUITABLE"

    decision = _post_ok(
        client,
        "/v3/decisions",
        "approver-token",
        f"decide-{artifact_id}",
        {"candidate_id": artifact_id, "action": "APPROVE"},
    )
    assert decision["action"] == "APPROVE"

    draft_text = f"Synthetic draft for {artifact_id}."
    draft_hash = sha256_hex(draft_text)
    review = _post_ok(
        client,
        "/v3/writing/reviews",
        "writer-token",
        f"review-{artifact_id}",
        {
            "draft_ref": f"draft-{artifact_id}",
            "draft_hash": draft_hash,
            "coordinate": _coordinate(knowledge_version),
            "context_view": context,
            "required_checks": ["semantic"],
        },
    )
    assert review["overall_status"] == "PASSED"

    coordinate = StoryCoordinate.model_validate(
        {key: value for key, value in _coordinate(knowledge_version).items() if key != "schema_version"}
    )
    object_ref = f"object-{artifact_id}"
    change = {
        "change_id": f"change-{artifact_id}",
        "type_uri": artifact_kind,
        "before": {"knowledge_version": knowledge_version},
        "after": payload,
        "delta": {"object_refs": [object_ref]},
        "scope": {
            "work_id": WORK_ID,
            "branch_id": BRANCH_ID,
            "as_of": 0,
            "purpose": "commit",
            "actor": "e2e-writer",
        },
        "narrative_order": 1,
        "evidence_refs": [evidence.model_dump(mode="json")],
        "claim_refs": [],
        "source_artifact_hash": draft_hash,
        "object_refs": [object_ref],
        "status": "PROPOSED",
    }
    state_change_set = StateChangeSet(
        change_set_id=f"changes-{artifact_id}",
        coordinate=coordinate,
        source_artifact_hash=draft_hash,
        changes=(change,),
        review_ref=review["report_id"],
    )
    proposal = _post_ok(
        client,
        "/v3/writing/proposals",
        "writer-token",
        f"proposal-{artifact_id}",
        {
            "draft_ref": f"draft-{artifact_id}",
            "draft_hash": draft_hash,
            "review": review,
            "state_change_set": state_change_set.model_dump(mode="json"),
            "context_view": context,
            "knowledge_head": {
                "work_id": WORK_ID,
                "branch_id": BRANCH_ID,
                "knowledge_version": knowledge_version,
                "version_hash": "0" * 64,
                "cas_revision": 0,
            },
        },
    )
    approval = _post_ok(
        client,
        "/v3/approvals",
        "approver-token",
        f"approval-{artifact_id}",
        {
            "proposal_id": proposal["proposal_id"],
            "proposal_hash": proposal["proposal_hash"],
            "work_id": WORK_ID,
            "expected_version": knowledge_version,
        },
    )
    promoted = _post_ok(
        client,
        "/v3/promotions",
        "approver-token",
        f"promotion-{artifact_id}",
        {
            "candidate_id": artifact_id,
            "approval_id": approval["approval_id"],
            "approval_hash": approval["approval_hash"],
            "expected_head": knowledge_version,
        },
    )
    assert promoted["candidate_id"] == artifact_id
    assert promoted["expected_head"] == knowledge_version
    return promoted, review


def test_public_v3_clean_room_closes_multi_source_to_commit_and_rebuild(
    temp_workspace: Any,
    monkeypatch: Any,
) -> None:
    _configure_auth(monkeypatch)
    app = create_app(
        temp_workspace,
        evaluator=SyntheticEvaluator(),
        semantic_reviewer=SyntheticWritingReviewer(),
        services={"evaluation_semantic_reviewer": SyntheticEvaluationReviewer()},
    )

    with TestClient(app) as client:
        health = client.get("/v3/health")
        assert health.status_code == 200, health.text
        health_result = health.json()["result"]
        assert health_result["contract_revision"] == CONTRACT_REVISION

        sources = _bootstrap_sources(client, temp_workspace)
        capabilities = client.get(
            "/v3/capabilities",
            params={"work_id": WORK_ID},
            headers={"X-Fxi-Actor-Token": "reader-token"},
        )
        assert capabilities.status_code == 200, capabilities.text
        capability_envelope = capabilities.json()
        capability_result = capability_envelope["result"]
        assert capability_result["contract_revision"] == CONTRACT_REVISION
        assert capability_result["schema_hash"] == capability_envelope["dependency_versions"]["schema_hash"]
        assert {sources["synthetic-inspiration"]["role"], sources["synthetic-reference"]["role"]} == {
            "inspiration",
            "reference",
        }
        assert {"novel.character@1", "novel.item@1", "novel.timeline_event@1"}.issubset(
            set(capability_result["registered_type_uris"])
        )

        unsafe_binding = client.post(
            "/v3/sources/bindings",
            headers=_headers("writer-token", "source-bind-unsafe"),
            json={
                "work_id": WORK_ID,
                "source_id": "unsafe-source",
                "role": "reference",
                "branch_id": BRANCH_ID,
                "license": LICENSE,
                "access": "local",
                "source_dir": "../outside",
            },
        )
        assert unsafe_binding.status_code == 403
        assert unsafe_binding.json()["error"]["code"] == "INVALID_SCOPE"

        specs = (
            (
                "synthetic-character",
                "novel.character@1",
                sources["synthetic-inspiration"],
                {"record_id": "synthetic-character", "label": "Synthetic Character"},
            ),
            (
                "synthetic-item",
                "novel.item@1",
                sources["synthetic-reference"],
                {"record_id": "synthetic-item", "label": "Synthetic Item"},
            ),
            (
                "synthetic-event",
                "novel.timeline_event@1",
                sources["synthetic-inspiration"],
                {
                    "event_id": "synthetic-event",
                    "label": "Synthetic Event",
                    "at": 1,
                    "details": {"marker": "clean-room"},
                },
            ),
        )

        current_version = GENESIS_VERSION
        promoted_types: list[str] = []
        latest_context: dict[str, Any] | None = None
        latest_review: dict[str, Any] | None = None
        latest_evidence: EvidenceRef | None = None
        latest_payload: dict[str, Any] | None = None
        latest_kind: str | None = None
        for index, (artifact_id, artifact_kind, source, payload) in enumerate(specs, start=1):
            candidate = _candidate(
                source_id=source["source_id"] if "source_id" in source else "",
                source_version=source["source_version"],
                snapshot_ref=source["snapshot_ref"],
                source_text=source["text"],
                artifact_id=artifact_id,
                artifact_kind=artifact_kind,
                payload=payload,
            )
            # The source_id is retained in the source fixture metadata below;
            # keeping this assertion public prevents accidental cross-source
            # evidence binding while preparing the request.
            assert source["source_id"] in {"synthetic-inspiration", "synthetic-reference"}
            submitted = _post_ok(
                client,
                "/v3/candidates",
                "writer-token",
                f"candidate-{index}",
                candidate,
            )
            assert submitted["artifact_id"] == artifact_id
            context = _compile_context(client, current_version, key=f"context-before-{index}")
            assert context["completeness"] == "COMPLETE"
            assert set(context["source_snapshot_refs"]) == {
                sources["synthetic-inspiration"]["snapshot_ref"],
                sources["synthetic-reference"]["snapshot_ref"],
            }
            evidence = EvidenceRef(
                source_snapshot_ref=source["snapshot_ref"],
                source_id=source["source_id"],
                source_version=source["source_version"],
                document_id="document-1",
                start=0,
                end=len(source["text"]),
                excerpt_hash=sha256_hex(source["text"]),
                normalization_version=NORMALIZATION_VERSION,
                scope=Scope(
                    work_id=WORK_ID,
                    branch_id=BRANCH_ID,
                    as_of=0,
                    purpose="commit",
                    actor="e2e-writer",
                ),
                license=LICENSE,
            )
            promoted, review = _promote_candidate(
                client,
                candidate,
                context=context,
                knowledge_version=current_version,
                payload=payload,
                artifact_kind=artifact_kind,
                evidence=evidence,
            )
            promoted_types.append(artifact_kind)
            current_version = promoted["new_knowledge_version"]
            latest_context = _compile_context(client, current_version, key=f"context-after-{index}")
            latest_review = review
            latest_evidence = evidence
            latest_payload = payload
            latest_kind = artifact_kind
            assert latest_context["completeness"] == "COMPLETE"
            assert latest_context["fact_refs"]

        assert set(promoted_types) == {
            "novel.character@1",
            "novel.item@1",
            "novel.timeline_event@1",
        }
        assert latest_context is not None
        assert latest_review is not None
        assert latest_evidence is not None
        assert latest_payload is not None
        assert latest_kind is not None

        # Complete the independent writing lifecycle after the candidate
        # promotions.  The API accepts the exact final ContextView and binds
        # the state change to the final review before external approval.
        draft_text = "Synthetic committed chapter."
        draft_hash = sha256_hex(draft_text)
        final_review = _post_ok(
            client,
            "/v3/writing/reviews",
            "writer-token",
            "final-writing-review",
            {
                "draft_ref": "draft-final-synthetic",
                "draft_hash": draft_hash,
                "coordinate": _coordinate(current_version),
                "context_view": latest_context,
                "required_checks": ["semantic"],
            },
        )
        assert final_review["overall_status"] == "PASSED"
        final_coordinate = StoryCoordinate.model_validate(
            {key: value for key, value in _coordinate(current_version).items() if key != "schema_version"}
        )
        final_object_ref = latest_context["fact_refs"][0]
        final_change = {
            "change_id": "change-final-synthetic",
            "type_uri": latest_kind,
            "before": {"knowledge_version": current_version},
            "after": latest_payload,
            "delta": {"object_refs": [final_object_ref]},
            "scope": {
                "work_id": WORK_ID,
                "branch_id": BRANCH_ID,
                "as_of": 0,
                "purpose": "commit",
                "actor": "e2e-writer",
            },
            "narrative_order": 1,
            "evidence_refs": [latest_evidence.model_dump(mode="json")],
            "claim_refs": [],
            "source_artifact_hash": draft_hash,
            "object_refs": [final_object_ref],
            "status": "PROPOSED",
        }
        final_state = StateChangeSet(
            change_set_id="changes-final-synthetic",
            coordinate=final_coordinate,
            source_artifact_hash=draft_hash,
            changes=(final_change,),
            review_ref=final_review["report_id"],
        )
        final_proposal = _post_ok(
            client,
            "/v3/writing/proposals",
            "writer-token",
            "final-writing-proposal",
            {
                "draft_ref": "draft-final-synthetic",
                "draft_hash": draft_hash,
                "review": final_review,
                "state_change_set": final_state.model_dump(mode="json"),
                "context_view": latest_context,
                "knowledge_head": {
                    "work_id": WORK_ID,
                    "branch_id": BRANCH_ID,
                    "knowledge_version": current_version,
                    "version_hash": "0" * 64,
                    "cas_revision": 0,
                },
            },
        )
        final_approval = _post_ok(
            client,
            "/v3/approvals",
            "approver-token",
            "final-writing-approval",
            {
                "proposal_id": final_proposal["proposal_id"],
                "proposal_hash": final_proposal["proposal_hash"],
                "work_id": WORK_ID,
                "expected_version": current_version,
            },
        )
        commit_payload = {
            "proposal_id": final_proposal["proposal_id"],
            "proposal_hash": final_proposal["proposal_hash"],
            "approval_id": final_approval["approval_id"],
            "approval_hash": final_approval["approval_hash"],
            "expected_knowledge_version": current_version,
            "chapter_version": "chapter-synthetic-final",
            "idempotency_key": "commit-synthetic-final",
            "work_id": WORK_ID,
            "branch_id": BRANCH_ID,
            "chapter": {"text": draft_text, "text_hash": draft_hash},
            "projection_kinds": ["fts", "oag-lite", "wiki"],
            "context_view": latest_context,
            "review": final_review,
            "state_change_set": final_state.model_dump(mode="json"),
        }
        committed = _post_ok(
            client,
            "/v3/writing/commits",
            "approver-token",
            "commit-synthetic-final",
            commit_payload,
        )
        assert committed["status"] == "COMMITTED"
        committed_replay = _post_ok(
            client,
            "/v3/writing/commits",
            "approver-token",
            "commit-synthetic-final",
            commit_payload,
        )
        assert committed_replay == committed

        rebuilt = _post_ok(
            client,
            "/v3/projections/rebuild",
            "writer-token",
            "projection-rebuild-final",
            {
                "work_id": WORK_ID,
                "branch_id": BRANCH_ID,
                "knowledge_version": committed["new_knowledge_version"],
                "projection_ids": ["fts", "oag-lite", "wiki", "vector"],
                "context_view": latest_context,
            },
        )
        manifests = {item["projection_id"]: item for item in rebuilt["manifests"]}
        assert manifests["fts"]["status"] == "BUILT"
        assert manifests["oag-lite"]["status"] == "BUILT"
        assert manifests["wiki"]["status"] == "BUILT"
        assert manifests["vector"]["error_code"] == "CAPABILITY_UNSUPPORTED"
        assert "CAPABILITY_UNSUPPORTED" in rebuilt["codes"]

        rebuilt_again = _post_ok(
            client,
            "/v3/projections/rebuild",
            "writer-token",
            "projection-rebuild-final-again",
            {
                "work_id": WORK_ID,
                "branch_id": BRANCH_ID,
                "knowledge_version": committed["new_knowledge_version"],
                "projection_ids": ["fts", "oag-lite", "wiki"],
                "context_view": latest_context,
            },
        )
        manifests_again = {item["projection_id"]: item for item in rebuilt_again["manifests"]}
        assert manifests_again["fts"]["status"] == "BUILT"
        assert manifests_again["fts"]["projection_hash"] == manifests["fts"]["projection_hash"]

        query = _post_ok(
            client,
            "/v3/query",
            "reader-token",
            "query-final-synthetic",
            {
                "coordinate": _coordinate(committed["new_knowledge_version"]),
                "query": "Synthetic",
                "budget": 100,
                "purpose": "fts",
            },
        )
        assert query["status"] == "OK"
        assert query["code"] == "OK"
        assert query["results"]
        assert query["evidence_refs"]

        readiness = client.get(
            "/v3/readiness",
            headers={"X-Fxi-Actor-Token": "reader-token"},
        )
        assert readiness.status_code == 200, readiness.text
        readiness_result = readiness.json()["result"]
        assert readiness_result["status"] == "DEGRADED"
        assert readiness_result["checks"]["contract"]["status"] == "OK"
        assert readiness_result["projections"]["vector"]["error_code"] == "CAPABILITY_UNSUPPORTED"
