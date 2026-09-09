"""Isolated tests for W2 actor authentication and explicit work/source scope."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fxi.api.auth import ACTOR_CREDENTIALS_ENV, require_actor
from fxi.api.registry import (
    SourceBindingConflictError,
    SourceNotFoundError,
    WorkNotFoundError,
    WorkRegistry,
)


def _request(token: str | None = None, *, body: object | None = None) -> SimpleNamespace:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return SimpleNamespace(headers=headers, body=body)


def _registry() -> tuple[sqlite3.Connection, WorkRegistry]:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE works (
            work_id TEXT PRIMARY KEY NOT NULL,
            owner_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            title TEXT NOT NULL,
            source_dir TEXT,
            skill_root TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO works (work_id, owner_id, slug, title) VALUES (?, ?, ?, ?)",
        [("work_a", "owner", "work-a", "A"), ("work_b", "owner", "work-b", "B")],
    )
    connection.commit()
    return connection, WorkRegistry(connection)


def _configure_actor(monkeypatch: pytest.MonkeyPatch, *, work_ids: list[str] | None = None) -> None:
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "test-token": {
                    "actor_id": "writer-1",
                    "roles": ["writer", "reviewer"],
                    "work_ids": work_ids or ["work_a"],
                }
            }
        ),
    )


def test_require_actor_uses_server_identity_and_explicit_work(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, registry = _registry()
    try:
        registry.register_source("work_a", "source_a", source_dir="sources/source_a")
        _configure_actor(monkeypatch)

        actor = require_actor(_request("test-token", body={"actor_id": "forged"}), "writer", "work_a", registry=registry)

        assert actor == actor.__class__("writer-1", frozenset({"writer", "reviewer"}), frozenset({"work_a"}))
        assert registry.require_source("work_a", "source_a").work_id == "work_a"
        with pytest.raises(SourceNotFoundError):
            registry.require_source("work_b", "source_a")
    finally:
        connection.close()


def test_missing_credentials_fail_closed_with_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ACTOR_CREDENTIALS_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        require_actor(_request(), "writer")
    assert raised.value.status_code == 401
    assert raised.value.detail["code"] == "AUTH_REQUIRED"


def test_missing_server_mapping_with_supplied_token_is_not_an_allow_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ACTOR_CREDENTIALS_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        require_actor(_request("test-token"), "writer")
    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "AUTH_CONFIG_UNAVAILABLE"


def test_role_and_work_scope_fail_with_403(monkeypatch: pytest.MonkeyPatch) -> None:
    _, registry = _registry()
    _configure_actor(monkeypatch)

    with pytest.raises(HTTPException) as role_error:
        require_actor(_request("test-token"), "admin", "work_a", registry=registry)
    assert role_error.value.status_code == 403

    with pytest.raises(HTTPException) as work_error:
        require_actor(_request("test-token"), "writer", "work_b", registry=registry)
    assert work_error.value.status_code == 403


def test_registered_scope_unknown_work_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _, registry = _registry()
    _configure_actor(monkeypatch, work_ids=["missing-work"])

    with pytest.raises(HTTPException) as raised:
        require_actor(_request("test-token"), "writer", "missing-work", registry=registry)
    assert raised.value.status_code == 404
    assert raised.value.detail["code"] == "WORK_NOT_FOUND"


def test_source_registration_rejects_cross_work_rebinding() -> None:
    connection, registry = _registry()
    try:
        registry.register_source("work_a", "source_a")
        with pytest.raises(SourceBindingConflictError):
            registry.register_source("work_b", "source_a")
        with pytest.raises(WorkNotFoundError):
            registry.register_source("missing-work", "source_b")
    finally:
        connection.close()
