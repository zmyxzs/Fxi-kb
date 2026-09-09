"""Public Fxi v3 CLI.

This module is deliberately a transport client, not a second service layer.  It
only talks to the versioned HTTP boundary, which also makes the commands easy
to exercise with a synthetic transport in tests.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import httpx
import typer


class ApiTransport(Protocol):
    """Minimal public transport seam used by the CLI and synthetic tests."""

    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class HttpxTransport:
    """Default network transport; no Fxi implementation details are imported."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = httpx.Client(timeout=timeout)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return self._client.request(method, url, **kwargs)

    def close(self) -> None:
        self._client.close()


@dataclass
class V3Client:
    """Small, testable client for the Studio/Fxi v3 public contract."""

    base_url: str = "http://127.0.0.1:8000"
    auth: str | None = None
    idempotency_key: str | None = None
    transport: ApiTransport | None = None
    timeout: float = 30.0
    _headers: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url:
            raise ValueError("base_url is required")
        if self.transport is None:
            self.transport = HttpxTransport(timeout=self.timeout)
        if self.auth:
            self._headers["Authorization"] = (
                self.auth if self.auth.lower().startswith(("bearer ", "basic ")) else f"Bearer {self.auth}"
            )
        self._headers["Accept"] = "application/json"

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Call one public endpoint and raise a useful CLI-facing error."""
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        headers = dict(self._headers)
        key = idempotency_key or self.idempotency_key
        if key:
            headers["Idempotency-Key"] = key
        body = None if payload is None else dict(payload)
        response = self.transport.request(method.upper(), f"{self.base_url}{path}", json=body, headers=headers)
        status = int(getattr(response, "status_code", 200))
        try:
            result = response.json()
        except (ValueError, TypeError) as exc:
            raise V3ClientError("INVALID_RESPONSE", f"v3 returned non-JSON response ({status})", status) from exc
        if status >= 400:
            if isinstance(result, Mapping):
                detail = result.get("detail", result)
                if isinstance(detail, Mapping):
                    code = str(detail.get("code", "HTTP_ERROR"))
                    message = str(detail.get("message", detail))
                else:
                    code, message = "HTTP_ERROR", str(detail)
            else:
                code, message = "HTTP_ERROR", str(result)
            raise V3ClientError(code, message, status, result)
        return result

    def get(self, endpoint: str, **query: Any) -> Any:
        clean = {key: value for key, value in query.items() if value is not None}
        if clean:
            from urllib.parse import urlencode

            endpoint = f"{endpoint}?{urlencode(clean, doseq=True)}"
        return self.request("GET", endpoint)

    def post(self, endpoint: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        return self.request("POST", endpoint, payload=payload, idempotency_key=idempotency_key)

    def capabilities(self, work_id: str) -> Any:
        return self.get("/v3/capabilities", work_id=work_id)

    def operation(self, endpoint: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        return self.post(endpoint, payload, idempotency_key=idempotency_key)

    def project(self, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        return self.operation("/v3/projects", payload, idempotency_key=idempotency_key)

    def source(self, payload: Mapping[str, Any], *, snapshot: bool = False, idempotency_key: str | None = None) -> Any:
        endpoint = "/v3/sources/snapshots" if snapshot else "/v3/sources/bindings"
        return self.operation(endpoint, payload, idempotency_key=idempotency_key)

    def candidate(self, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        return self.operation("/v3/candidates", payload, idempotency_key=idempotency_key)

    def lifecycle(self, kind: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        endpoints = {
            "evaluation": "/v3/evaluations",
            "decision": "/v3/decisions",
            "promotion": "/v3/promotions",
            "review": "/v3/writing/reviews",
            "proposal": "/v3/writing/proposals",
            "approval": "/v3/approvals",
            "commit": "/v3/writing/commits",
            "projection": "/v3/projections/rebuild",
        }
        try:
            endpoint = endpoints[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported v3 operation: {kind}") from exc
        return self.operation(endpoint, payload, idempotency_key=idempotency_key)

    def context(self, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        return self.post("/v3/context/views", payload, idempotency_key=idempotency_key)

    def query(self, payload: Mapping[str, Any], *, idempotency_key: str | None = None) -> Any:
        return self.post("/v3/query", payload, idempotency_key=idempotency_key)

    def health(self) -> Any:
        return self.get("/v3/health")

    def readiness(self) -> Any:
        return self.get("/v3/readiness")


class V3ClientError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int, response: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status_code, self.response = code, message, status_code, response


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Return the canonical JSON digest used for CLI diagnostics and fixtures."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _emit(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str))


def _json_payload(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("payload must be valid JSON") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter("payload must be a JSON object")
    return value


def _client(base_url: str, auth: str | None, idempotency: str | None) -> V3Client:
    return V3Client(base_url=base_url, auth=auth, idempotency_key=idempotency)


app = typer.Typer(name="v3", help="Fxi v3 public API commands", add_completion=False)


@app.command("capabilities")
def capabilities(work_id: str = typer.Option(...), base_url: str = typer.Option("http://127.0.0.1:8000"), auth: str | None = typer.Option(None), idempotency: str | None = typer.Option(None)) -> None:
    client = _client(base_url, auth, idempotency)
    try:
        _emit(client.capabilities(work_id))
    except V3ClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


def _operation_command(name: str, endpoint_kind: str | None = None) -> Any:
    kind = endpoint_kind or name

    def command(payload: str = typer.Argument(..., help="JSON request object"), base_url: str = typer.Option("http://127.0.0.1:8000"), auth: str | None = typer.Option(None), idempotency: str | None = typer.Option(None)) -> None:
        client = _client(base_url, auth, idempotency)
        try:
            data = _json_payload(payload)
            if kind == "project":
                result = client.project(data, idempotency_key=idempotency)
            elif kind in {"source", "snapshot"}:
                result = client.source(data, snapshot=kind == "snapshot", idempotency_key=idempotency)
            elif kind == "candidate":
                result = client.candidate(data, idempotency_key=idempotency)
            else:
                result = client.lifecycle(kind, data, idempotency_key=idempotency)
            _emit(result)
        except (V3ClientError, typer.BadParameter) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        finally:
            client.close()

    command.__name__ = name
    return command


for _name, _kind in (("project", "project"), ("source-bind", "source"), ("source-snapshot", "snapshot"), ("candidate", "candidate"), ("evaluate", "evaluation"), ("decide", "decision"), ("promote", "promotion"), ("review", "review"), ("proposal", "proposal"), ("approve", "approval"), ("commit", "commit"), ("projection-rebuild", "projection")):
    app.command(_name)(_operation_command(_name, _kind))


@app.command("context")
def context(payload: str = typer.Argument(...), base_url: str = typer.Option("http://127.0.0.1:8000"), auth: str | None = typer.Option(None), idempotency: str | None = typer.Option(None)) -> None:
    client = _client(base_url, auth, idempotency)
    try:
        _emit(client.context(_json_payload(payload), idempotency_key=idempotency))
    except (V3ClientError, typer.BadParameter) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


@app.command("query")
def query(payload: str = typer.Argument(...), base_url: str = typer.Option("http://127.0.0.1:8000"), auth: str | None = typer.Option(None), idempotency: str | None = typer.Option(None)) -> None:
    client = _client(base_url, auth, idempotency)
    try:
        _emit(client.query(_json_payload(payload), idempotency_key=idempotency))
    except (V3ClientError, typer.BadParameter) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


for _name in ("health", "readiness"):
    def _status(name: str = _name, base_url: str = typer.Option("http://127.0.0.1:8000"), auth: str | None = typer.Option(None)) -> None:
        client = _client(base_url, auth, None)
        try:
            _emit(client.health() if name == "health" else client.readiness())
        except V3ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        finally:
            client.close()

    app.command(_name)(_status)


__all__ = ["ApiTransport", "HttpxTransport", "V3Client", "V3ClientError", "app", "payload_hash"]
