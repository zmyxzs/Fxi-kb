"""Contract and OpenAPI checks for the public Fxi v3 HTTP boundary."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fxi.api.contracts_v3 import StoryCoordinateV3, V3Response
from fxi.api.server import create_app
from fxi.knowledge.contracts import CONTRACT_REVISION, SCHEMA_HASH, SCHEMA_VERSION
from fxi.ops.operation_manifest import openapi_schema_hash


def _coordinate() -> dict[str, object]:
    return {
        "work_id": "synthetic-work",
        "branch_id": "branch-main",
        "as_of": 0,
        "chapter_index": 1,
        "narrative_order": 1,
        "knowledge_version": "version-branch-main",
    }


def test_v3_wire_models_are_strict_and_hash_bound() -> None:
    with pytest.raises(ValidationError):
        StoryCoordinateV3(**_coordinate(), forged_actor="writer")

    response = V3Response(trace_id="trace-synthetic", result={"b": 2, "a": 1})
    assert len(response.result_hash) == 64
    with pytest.raises(ValidationError):
        V3Response(
            trace_id="trace-synthetic",
            result={"ok": True},
            result_hash="0" * 64,
        )


def test_v3_app_publishes_frozen_contract_and_all_routes(temp_workspace) -> None:
    app = create_app(temp_workspace)
    client = TestClient(app)

    health = client.get("/v3/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["result"]["contract_revision"] == CONTRACT_REVISION
    assert payload["result"]["schema_hash"] == SCHEMA_HASH
    assert payload["dependency_versions"]["schema_version"] == SCHEMA_VERSION
    assert payload["dependency_versions"]["openapi_hash"] == app.state.openapi_hash

    expected_paths = {
        "/v3/capabilities",
        "/v3/projects",
        "/v3/sources/bindings",
        "/v3/sources/snapshots",
        "/v3/candidates",
        "/v3/evaluations",
        "/v3/decisions",
        "/v3/promotions",
        "/v3/context/views",
        "/v3/query",
        "/v3/writing/reviews",
        "/v3/writing/proposals",
        "/v3/approvals",
        "/v3/writing/commits",
        "/v3/projections/rebuild",
        "/v3/health",
        "/v3/readiness",
    }
    assert expected_paths <= set(app.openapi()["paths"])
    assert app.state.openapi_hash == openapi_schema_hash(app.openapi())


def test_v3_error_envelope_contains_trace_dependencies_and_result_hash(temp_workspace, monkeypatch) -> None:
    monkeypatch.delenv("FXI_API_ACTORS_JSON", raising=False)
    response = TestClient(create_app(temp_workspace)).get(
        "/v3/capabilities",
        params={"work_id": "synthetic-work"},
        headers={"X-Request-ID": "request-synthetic"},
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "AUTH_REQUIRED"
    assert error["trace_id"] == "trace-request-synthetic"
    assert error["dependency_versions"]["contract_revision"] == CONTRACT_REVISION
    assert len(error["result_hash"]) == 64
