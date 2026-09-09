"""Synthetic tests for the transport-only v3 CLI boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fxi.cli.commands_v3 import V3Client, V3ClientError, app, payload_hash


@dataclass
class FakeResponse:
    status_code: int = 200
    body: Any = None

    def json(self) -> Any:
        return self.body if self.body is not None else {"code": "OK"}


class FakeTransport:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.response = response or FakeResponse(body={"code": "OK"})

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self.response


def test_client_uses_public_http_transport_and_common_headers() -> None:
    transport = FakeTransport(FakeResponse(body={"code": "OK", "result": {"ok": True}}))
    client = V3Client(base_url="https://fxi.test/", auth="synthetic-token", idempotency_key="idem-1", transport=transport)

    result = client.project({"work_id": "synthetic-work"})

    assert result["code"] == "OK"
    method, url, kwargs = transport.calls[0]
    assert (method, url) == ("POST", "https://fxi.test/v3/projects")
    assert kwargs["json"] == {"work_id": "synthetic-work"}
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-token"
    assert kwargs["headers"]["Idempotency-Key"] == "idem-1"


def test_all_v3_operations_map_to_versioned_public_endpoints() -> None:
    transport = FakeTransport()
    client = V3Client(transport=transport)
    client.capabilities("w")
    client.source({}, snapshot=False)
    client.source({}, snapshot=True)
    client.candidate({})
    for kind in ("evaluation", "decision", "promotion", "review", "proposal", "approval", "commit", "projection"):
        client.lifecycle(kind, {})
    client.context({})
    client.query({})
    client.health()
    client.readiness()

    paths = [url.split("?", 1)[0] for _, url, _ in transport.calls]
    assert paths == [
        "http://127.0.0.1:8000/v3/capabilities",
        "http://127.0.0.1:8000/v3/sources/bindings",
        "http://127.0.0.1:8000/v3/sources/snapshots",
        "http://127.0.0.1:8000/v3/candidates",
        "http://127.0.0.1:8000/v3/evaluations",
        "http://127.0.0.1:8000/v3/decisions",
        "http://127.0.0.1:8000/v3/promotions",
        "http://127.0.0.1:8000/v3/writing/reviews",
        "http://127.0.0.1:8000/v3/writing/proposals",
        "http://127.0.0.1:8000/v3/approvals",
        "http://127.0.0.1:8000/v3/writing/commits",
        "http://127.0.0.1:8000/v3/projections/rebuild",
        "http://127.0.0.1:8000/v3/context/views",
        "http://127.0.0.1:8000/v3/query",
        "http://127.0.0.1:8000/v3/health",
        "http://127.0.0.1:8000/v3/readiness",
    ]


def test_get_query_and_error_envelope_are_preserved() -> None:
    transport = FakeTransport(FakeResponse(409, {"detail": {"code": "STALE_VERSION", "message": "stale"}}))
    client = V3Client(transport=transport)
    try:
        client.capabilities("synthetic work")
    except V3ClientError as exc:
        assert exc.code == "STALE_VERSION"
        assert exc.status_code == 409
    else:
        raise AssertionError("expected the public error envelope to be raised")
    assert "work_id=synthetic+work" in transport.calls[0][1]


def test_payload_hash_is_stable_and_cli_registers_required_commands() -> None:
    assert payload_hash({"b": 2, "a": 1}) == payload_hash({"a": 1, "b": 2})
    names = {command.name for command in app.registered_commands}
    assert {"capabilities", "project", "candidate", "query", "review", "commit", "readiness"} <= names
