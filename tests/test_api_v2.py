"""Versioned source, context, review and style workflow API tests."""

import hashlib
import json

from fastapi.testclient import TestClient

from fxi.api.auth import ACTOR_CREDENTIALS_ENV
from fxi.api.registry import WorkRegistry
from fxi.api.server import create_app
from fxi.core.config import FxiConfig
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


def _hash(value):
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source(cfg: FxiConfig, source_id: str = "book") -> str:
    source_dir = cfg.sources_dir / source_id
    chapters = source_dir / "chapters"
    chapters.mkdir(parents=True, exist_ok=True)
    text = "第一章\n雨停了。"
    (chapters / "ch001.md").write_text(text, encoding="utf-8")
    version = _hash(text)
    (source_dir / "source.yaml").write_text(
        f"source_id: {source_id}\nsha256: {version}\ntotal_chapters: 1\n",
        encoding="utf-8",
    )

    (cfg.projects_dir / "work_v2").mkdir(parents=True, exist_ok=True)
    (cfg.projects_dir / "work_v2" / "work.yaml").write_text("continuity_rules: []\n", encoding="utf-8")

    database = DatabaseClient(cfg.sqlite_path)
    with database.transaction() as cur:
        ensure_work(cur, "work_v2")
    WorkRegistry.from_database_client(database).register_source(
        "work_v2",
        source_id,
        source_dir=source_id,
        source_version=version,
    )
    return version


def test_v2_snapshot_context_review_and_style(temp_workspace: FxiConfig, monkeypatch):
    source_version = _source(temp_workspace)
    monkeypatch.setenv(
        ACTOR_CREDENTIALS_ENV,
        json.dumps(
            {
                "actor-token": {
                    "actor_id": "actor-1",
                    "roles": ["writer", "reader", "reviewer", "approver"],
                    "work_ids": ["work_v2"],
                },
            }
        ),
    )
    monkeypatch.setenv("FXI_HUMAN_APPROVAL_TOKEN", "test-token")

    headers = {
        "X-Fxi-Actor-Token": "actor-token",
        "X-Fxi-Human-Approval-Token": "test-token",
    }
    client = TestClient(create_app(temp_workspace))
    snapshot_body = {
        "work_id": "work_v2",
        "source_id": "book",
        "expected_source_version": source_version,
        "idempotency_key": "snapshot-1",
    }
    snapshot = client.post("/v2/sources/book/snapshots", headers=headers, json=snapshot_body)
    assert snapshot.status_code == 200
    snapshot_data = snapshot.json()
    assert snapshot_data["version"] == source_version
    assert snapshot_data["documents"][0]["document_id"] == "ch001"
    replay = client.post("/v2/sources/book/snapshots", headers=headers, json=snapshot_body)
    assert replay.status_code == 200
    assert replay.json()["manifest_hash"] == snapshot_data["manifest_hash"]

    document = client.get(
        f"/v2/sources/book/snapshots/{source_version}/documents/ch001",
        headers=headers,
    )
    assert document.status_code == 200
    assert document.json()["content"] == "第一章\n雨停了。"

    context = client.post("/v2/writing/context", headers=headers, json={
        "work_id": "work_v2",
        "source_id": "book",
        "source_version": source_version,
        "pov_character_id": "hero",
        "objective": "承接雨后的行动",
        "scene_beat": {"planned_revelations": [{"id": "r1"}]},
    })
    assert context.status_code == 200
    assert context.json()["completeness_status"] == "COMPLETE"
    assert context.json()["content_hash"]

    text = "雨停后，英雄走向门口。"
    review = client.post("/v2/writing/review", headers=headers, json={
        "work_id": "work_v2",
        "source_id": "book",
        "source_version": source_version,
        "knowledge_version": "knowledge-v0",
        "chapter_index": 2,
        "text": text,
        "text_hash": _hash(text),
        "plan_hash": "plan-1",
        "context_hash": context.json()["content_hash"],
        "required_checks": ["continuity"],
    })
    assert review.status_code == 200
    assert review.json()["overall_status"] == "INCOMPLETE"
    assert any(c["error_code"] == "MODEL_REVIEWER_NOT_CONFIGURED" for c in review.json()["checks"])

    bad_review = client.post("/v2/writing/review", headers=headers, json={
        "work_id": "work_v2",
        "source_id": "book",
        "source_version": source_version,
        "knowledge_version": "knowledge-v0",
        "chapter_index": 2,
        "text": text,
        "text_hash": "wrong",
        "plan_hash": "plan-1",
        "context_hash": "ctx-1",
    })
    assert bad_review.status_code == 409

    package = {"style_rules": ["短段落", "重动词"], "style_examples": ["例句"]}
    candidate = client.post("/v2/style/candidates", headers=headers, json={
        "work_id": "work_v2",
        "version": "style-v1",
        "package": package,
        "package_hash": _hash(package),
        "idempotency_key": "style-1",
    })
    assert candidate.status_code == 200
    candidate_id = candidate.json()["candidate_id"]
    assert client.get("/v2/style/packages/style-v1?work_id=work_v2", headers=headers).status_code == 200

    approval = client.post(
        "/v2/approvals",
        headers=headers,
        json={
            "action": "style_promotion",
            "target_id": candidate_id,
            "target_hash": _hash(package),
            "expected_version": "style-v0",
        },
    )
    assert approval.status_code == 200
    promotion = client.post(
        "/v2/style/promotions",
        headers=headers,
        json={
            "work_id": "work_v2",
            "candidate_id": candidate_id,
            "candidate_version": "style-v1",
            "candidate_hash": _hash(package),
            "approval_id": approval.json()["approval_id"],
            "expected_active_version": "style-v0",
            "idempotency_key": "promotion-1",
        },
    )
    assert promotion.status_code == 200
    approved_context = client.post("/v2/writing/context", headers=headers, json={
        "work_id": "work_v2",
        "source_id": "book",
        "source_version": source_version,
        "style_package_version": "style-v1",
        "style_selection": "approved",
        "pov_character_id": "hero",
    })
    assert approved_context.status_code == 200
    assert approved_context.json()["style_rules"] == [{"rule": "短段落"}, {"rule": "重动词"}]
    assert approved_context.json()["package_hash"] == _hash(package)
    assert approved_context.json()["style_view_hash"] == approved_context.json()["style_view"]["view_hash"]
    assert approved_context.json()["view_hash"]
    assert "approved" in approved_context.json()["selection_reason"]
