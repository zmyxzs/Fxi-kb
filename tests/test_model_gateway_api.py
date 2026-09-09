import json

from fastapi.testclient import TestClient

from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.server import create_app
from fxi.core.config import FxiConfig
from fxi.core.exceptions import GatewayError


class FakeModelGateway:
    def __init__(self) -> None:
        self.health_calls: list[dict[str, object]] = []
        self.chat_calls: list[dict[str, object]] = []
        self.failure: Exception | None = None

    def is_healthy(self, **kwargs) -> bool:
        self.health_calls.append(kwargs)
        return True

    def complete_chat(self, **kwargs) -> str:
        self.chat_calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return "  unified response  "


def _client(temp_workspace: FxiConfig, monkeypatch) -> tuple[TestClient, FakeModelGateway]:
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "writer-token": {
                    "actor_id": "studio-model-client",
                    "roles": ["writer"],
                    "work_ids": ["model-service"],
                }
            }
        ),
    )
    app = create_app(temp_workspace)
    gateway = FakeModelGateway()
    app.state.model_gateway = gateway
    return (
        TestClient(app, headers={"X-Fxi-Actor-Token": "writer-token"}),
        gateway,
    )


def test_model_health_uses_selected_route(temp_workspace: FxiConfig, monkeypatch) -> None:
    client, gateway = _client(temp_workspace, monkeypatch)

    response = client.post(
        "/v1/models/health",
        json={
            "task_type": "scene_drafting",
            "provider_override": "agnes",
            "model_override": "agnes-3.0-flash",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"healthy": True}
    assert gateway.health_calls == [
        {
            "task_type": "scene_drafting",
            "provider_override": "agnes",
            "model_override": "agnes-3.0-flash",
        }
    ]


def test_model_chat_forwards_only_route_and_generation_fields(
    temp_workspace: FxiConfig,
    monkeypatch,
) -> None:
    client, gateway = _client(temp_workspace, monkeypatch)

    response = client.post(
        "/v1/models/chat",
        json={
            "task_type": "scene_drafting",
            "provider_override": "agnes",
            "model_override": "agnes-3.0-flash",
            "system_prompt": "system",
            "user_prompt": "user",
            "temperature": 0.4,
            "max_tokens": 900,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"content": "unified response"}
    assert gateway.chat_calls == [
        {
            "task_type": "scene_drafting",
            "provider_override": "agnes",
            "model_override": "agnes-3.0-flash",
            "system_prompt": "system",
            "user_prompt": "user",
            "temperature": 0.4,
            "max_tokens": 900,
        }
    ]


def test_model_proxy_requires_writer_auth_and_valid_prompt(
    temp_workspace: FxiConfig,
    monkeypatch,
) -> None:
    client, _ = _client(temp_workspace, monkeypatch)

    unauthenticated = TestClient(client.app).post(
        "/v1/models/chat",
        json={"task_type": "scene_drafting", "user_prompt": "user"},
    )
    invalid = client.post(
        "/v1/models/chat",
        json={"task_type": "scene_drafting", "unknown": "ignored before"},
    )

    assert unauthenticated.status_code == 401
    assert invalid.status_code == 422


def test_model_provider_failure_is_generic_and_does_not_leak_details(
    temp_workspace: FxiConfig,
    monkeypatch,
) -> None:
    client, gateway = _client(temp_workspace, monkeypatch)
    gateway.failure = GatewayError("provider response contained sensitive details")

    response = client.post(
        "/v1/models/chat",
        json={"task_type": "scene_drafting", "user_prompt": "user"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_GATEWAY_ERROR"
    assert "sensitive" not in response.text
