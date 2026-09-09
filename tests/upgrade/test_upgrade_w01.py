from __future__ import annotations

import pytest
from pydantic import ValidationError

from fxi.api.contracts_v3 import ReadinessResultV3, V3Response
from fxi.api.server import create_app
from fxi.core.config import FxiConfig
from fxi.knowledge.contracts import (
    CONTRACT_REVISION,
    PUBLIC_CONTRACT_SCHEMA,
    SCHEMA_HASH,
    SCHEMA_VERSION,
)


EXPECTED_REVISION = "studio-fxi-v3.20260909"
EXPECTED_SCHEMA_VERSION = "knowledge-contract.v3"
EXPECTED_SCHEMA_HASH = "9714ca83189e4e8c552755005d0130267e5193d2fcb88bb288546465e3ee40a8"
EXPECTED_SCHEMA_KEYS = {
    "StoryCoordinate",
    "ContextManifest",
    "LearningArtifactEnvelope",
    "NarrativePlan",
    "GroundedChapterPlan",
    "ProductionJob",
    "ReviewReport",
    "StateChangeSet",
    "Proposal",
    "CommitReceipt",
}


def test_cross_repository_contract_identity_is_frozen() -> None:
    assert CONTRACT_REVISION == EXPECTED_REVISION
    assert SCHEMA_VERSION == EXPECTED_SCHEMA_VERSION
    assert SCHEMA_HASH == EXPECTED_SCHEMA_HASH
    assert set(PUBLIC_CONTRACT_SCHEMA) == EXPECTED_SCHEMA_KEYS


def test_readiness_advertises_the_frozen_identity() -> None:
    readiness = ReadinessResultV3(status="READY", liveness=True)
    assert readiness.contract_revision == EXPECTED_REVISION
    assert readiness.schema_hash == EXPECTED_SCHEMA_HASH


def test_v3_wire_envelope_rejects_hash_mismatch_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        V3Response(trace_id="trace:test", result={"ok": True}, result_hash="0" * 64)
    with pytest.raises(ValidationError):
        V3Response(trace_id="trace:test", unexpected_field=True)


def test_create_app_wires_one_shared_authority_store(tmp_path) -> None:
    data_dir = tmp_path / "data"
    config = FxiConfig(
        workspace_root=tmp_path,
        data_dir=data_dir,
        projects_dir=tmp_path / "projects",
        skills_dir=tmp_path / "skills",
        sources_dir=tmp_path / "sources",
        materials_dir=tmp_path / "materials",
        sqlite_path=data_dir / "manifest.sqlite",
        cache_db_path=data_dir / "cache.sqlite",
        jieba_custom_dict_path=data_dir / "project_lexicon.txt",
    )
    app = create_app(config=config)
    assert app.state.contract_revision == EXPECTED_REVISION
    assert app.state.schema_hash == EXPECTED_SCHEMA_HASH
    assert app.state.knowledge_store is not None
    assert app.state.work_registry is not None
