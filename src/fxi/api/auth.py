"""Environment-backed API actor authentication and work authorization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from fxi.api.registry import (
    RegistryUnavailableError,
    WorkNotFoundError,
    WorkRegistry,
)


ACTOR_CREDENTIALS_ENV = "FXI_API_ACTORS_JSON"
ACTOR_TOKEN_HEADER = "X-Fxi-Actor-Token"


@dataclass(frozen=True)
class AuthenticatedActor:
    """Identity and explicit project scope derived only from a server token."""

    actor_id: str
    roles: frozenset[str]
    work_ids: frozenset[str]

    def can_access_work(self, work_id: str) -> bool:
        return work_id in self.work_ids


@dataclass(frozen=True)
class _ActorCredential:
    token_digest: bytes
    actor: AuthenticatedActor


class AuthConfigurationError(RuntimeError):
    """The server-side actor mapping is absent or malformed."""


def _http_failure(status_code: int, code: str, message: str) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthConfigurationError(f"actor credential field {field} is invalid")
    return value.strip()


def _string_set(value: Any, field: str) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        raise AuthConfigurationError(f"actor credential field {field} is invalid")
    result = frozenset(_non_empty_string(item, field) for item in value)
    if len(result) != len(value):
        raise AuthConfigurationError(f"actor credential field {field} contains duplicates")
    return result


def _load_credentials(environment: Mapping[str, str]) -> tuple[_ActorCredential, ...]:
    raw = environment.get(ACTOR_CREDENTIALS_ENV, "")
    if not isinstance(raw, str) or not raw.strip():
        raise AuthConfigurationError("actor credentials are not configured")
    try:
        mapping = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuthConfigurationError("actor credentials are not valid JSON") from exc
    if not isinstance(mapping, dict) or not mapping:
        raise AuthConfigurationError("actor credentials must be a non-empty JSON object")

    credentials: list[_ActorCredential] = []
    for token, descriptor in mapping.items():
        if not isinstance(token, str) or not token.strip():
            raise AuthConfigurationError("actor credential token is invalid")
        if not isinstance(descriptor, dict):
            raise AuthConfigurationError("actor credential descriptor is invalid")
        actor = AuthenticatedActor(
            actor_id=_non_empty_string(descriptor.get("actor_id"), "actor_id"),
            roles=_string_set(descriptor.get("roles"), "roles"),
            work_ids=_string_set(descriptor.get("work_ids"), "work_ids"),
        )
        credentials.append(_ActorCredential(token_digest=_digest(token), actor=actor))
    return tuple(credentials)


def _header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return ""
    value = headers.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _request_token(request: Any) -> str:
    explicit = _header(request, ACTOR_TOKEN_HEADER)
    authorization = _header(request, "Authorization")
    bearer = ""
    if authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()

    if explicit and bearer and not hmac.compare_digest(_digest(explicit), _digest(bearer)):
        _http_failure(403, "AUTH_FORBIDDEN", "conflicting actor credentials")
    supplied = explicit or bearer
    if not supplied:
        _http_failure(401, "AUTH_REQUIRED", "actor credentials are required")
    return supplied


def authenticate_request(
    request: Any,
    *,
    environment: Mapping[str, str] | None = None,
) -> AuthenticatedActor:
    """Authenticate a request without consulting its body or query fields."""

    token = _request_token(request)
    env = os.environ if environment is None else environment
    try:
        credentials = _load_credentials(env)
    except AuthConfigurationError as exc:
        _http_failure(503, "AUTH_CONFIG_UNAVAILABLE", str(exc))

    supplied_digest = _digest(token)
    matched_actor: AuthenticatedActor | None = None
    for credential in credentials:
        if hmac.compare_digest(supplied_digest, credential.token_digest):
            matched_actor = credential.actor
    if matched_actor is None:
        _http_failure(403, "AUTH_FORBIDDEN", "actor credentials are invalid")
    return matched_actor


def require_actor(
    request: Any,
    role: str,
    work_id: str | None = None,
    *,
    registry: WorkRegistry | None = None,
    environment: Mapping[str, str] | None = None,
) -> AuthenticatedActor:
    """Require authenticated actor, role, and explicit work scope.

    When ``work_id`` is supplied, the registry is resolved from the explicit
    argument or ``request.app.state.work_registry``.  Missing registry state is
    a configuration failure; it never becomes an allow-all fallback.
    """

    if not isinstance(role, str) or not role.strip():
        raise ValueError("role must be a non-empty string")

    actor = authenticate_request(request, environment=environment)
    if role not in actor.roles and "admin" not in actor.roles:
        _http_failure(403, "AUTH_FORBIDDEN", "actor lacks the required role")

    if work_id is None:
        return actor
    if not isinstance(work_id, str) or not work_id.strip():
        _http_failure(404, "WORK_NOT_FOUND", "work is not registered")
    work_id = work_id.strip()
    if not actor.can_access_work(work_id):
        _http_failure(403, "AUTH_FORBIDDEN", "actor is outside the work scope")

    selected_registry = registry
    if selected_registry is None:
        app = getattr(request, "app", None)
        state = getattr(app, "state", None)
        selected_registry = getattr(state, "work_registry", None)
    if selected_registry is None:
        _http_failure(503, "WORK_REGISTRY_UNAVAILABLE", "work registry is not configured")

    try:
        selected_registry.require_work(work_id)
    except WorkNotFoundError:
        _http_failure(404, "WORK_NOT_FOUND", "work is not registered")
    except RegistryUnavailableError as exc:
        _http_failure(503, "WORK_REGISTRY_UNAVAILABLE", str(exc))
    return actor


__all__ = [
    "ACTOR_CREDENTIALS_ENV",
    "ACTOR_TOKEN_HEADER",
    "AuthenticatedActor",
    "AuthConfigurationError",
    "authenticate_request",
    "require_actor",
]
