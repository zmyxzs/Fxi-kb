from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from fxi.cli.commands_project import extract_all_work
from fxi.core.canonical import sha256_hex
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.sources.auto_extractor import ExtractionSchemaError, UniversalAutoExtractor
from fxi.sources.candidate_ingestor import CandidateIngestor
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


class ExtractionGateway:
    def complete(self, task_type: str, prompt: str, **kwargs):
        if "ending_location" in prompt:
            return {
                "ending_location": "测试地点",
                "active_characters": ["角色A"],
                "ending_situation": "测试情境",
                "unresolved_hooks": ["测试悬念"],
            }
        if '"voice_profiles"' in prompt:
            return {"voice_profiles": [{"name": "角色A", "tone": "克制"}]}
        if '"relationships"' in prompt:
            return {"relationships": [{"pair": ["角色A", "角色B"], "dynamic": "同伴"}]}
        if '"causal_events"' in prompt:
            return {
                "causal_events": [
                    {
                        "event_id": "event-1",
                        "narrative_order": 1,
                        "physical_time": "测试时间",
                        "summary": "测试事件",
                    }
                ]
            }
        if '"metaphors"' in prompt:
            return {"metaphors": {"preferred_style": "仅来自输入文本"}}
        return {
            "characters": [
                {
                    "entity_id": "char_a",
                    "name": "角色A",
                    "aliases": [],
                    "attributes": {},
                    "description": "测试角色",
                    "phases": [],
                    "abilities": [],
                }
            ],
            "items": [],
        }


class _ApprovalVerifier:
    """为合成测试提供可消费、可复验的服务端审批记录。"""

    def __init__(self, record: dict[str, object]):
        self.record = dict(record)

    def get_approval(self, approval_id: str) -> dict[str, object]:
        if approval_id != self.record["approval_id"]:
            raise ValueError("synthetic approval not found")
        return dict(self.record)

    def consume(self, approval_id: str, consumer_id: str) -> None:
        if approval_id != self.record["approval_id"] or self.record["consumed_by"] is not None:
            raise ValueError("synthetic approval is not consumable")
        self.record["consumed_by"] = consumer_id


def _chapter(config, work_id: str) -> Path:
    directory = config.sources_dir / work_id / "chapters"
    directory.mkdir(parents=True)
    path = directory / "ch001.md"
    path.write_text("第一章\n\n角色A进入测试地点。", encoding="utf-8")
    return path


def _formal_package(work_id: str) -> dict[str, object]:
    return {
        "work_id": work_id,
        "title": "测试作品",
        "entities": {
            "characters": [
                {
                    "entity_id": "char_hero",
                    "name": "测试角色",
                    "aliases": [],
                    "attributes": {},
                    "description": "测试角色",
                    "phases": [],
                    "abilities": [],
                }
            ],
            "items": [],
        },
        "causal_events": {"causal_events": []},
        "voice_profiles": {"voice_profiles": []},
        "relationships": {"relationships": []},
        "continuity": {"chapters": []},
        "status": "EVALUATION_CANDIDATE",
    }


def _synthetic_binding(config, *, text: str = "synthetic evidence"):
    synthetic_text = text
    evidence_hash = sha256_hex(synthetic_text)
    VersionedStore(config.sources_dir).create_snapshot(
        "source-a",
        [
            SourceDocumentInput(
                document_id="document-a",
                chapter_index=1,
                raw_bytes=synthetic_text.encode("utf-8"),
                text=synthetic_text,
                relative_path="synthetic.md",
                expected_content_hash=evidence_hash,
            )
        ],
        version="source-v1",
    )
    return {
        "work_id": "work_a",
        "source_id": "source-a",
        "source_version": "source-v1",
        "input_hash": evidence_hash,
        "evidence_refs": [
            {
                "evidence_id": "evidence-a",
                "source_id": "source-a",
                "source_version": "source-v1",
                "document_id": "document-a",
                "start_char": 0,
                "end_char": len(synthetic_text),
                "excerpt_hash": evidence_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": "evaluation-a",
        "submitted_by": "extractor-a",
    }


def _promote_synthetic_candidate(store: CandidateStore, record):
    approval_id = "approval-a"
    server = _ApprovalVerifier(
        {
            "approval_id": approval_id,
            "action": "candidate_promotion",
            "target_id": record.candidate_id,
            "target_hash": record.package_hash,
            "expected_version": "style-v0",
            "work_id": record.work_id,
            "source_id": record.source_id,
            "source_version": record.source_version,
            "candidate_version": record.version,
            "evaluation_ref": record.evaluation_ref,
            "actor_id": "reviewer",
            "expires_at": None,
            "validity": "VALID",
            "consumed_by": None,
            "created_at": "2026-09-09T00:00:00+00:00",
        }
    )
    store.approval_verifier = server
    return store.promote(
        record.candidate_id,
        {
            "approval_id": approval_id,
            "actor_id": "reviewer",
            "action": "candidate_promotion",
            "target_id": record.candidate_id,
            "target_hash": record.package_hash,
            "work_id": record.work_id,
            "source_id": record.source_id,
            "source_version": record.source_version,
            "candidate_version": record.version,
            "evaluation_ref": record.evaluation_ref,
            "expected_version": "style-v0",
        },
        actor_id="reviewer",
        approval_consumer=server.consume,
    )


def test_auto_extractor_emits_the_ingestor_schema_and_only_a_candidate(temp_workspace):
    chapter = _chapter(temp_workspace, "work_a")
    binding = _synthetic_binding(temp_workspace, text=chapter.read_text(encoding="utf-8"))
    extractor = UniversalAutoExtractor(temp_workspace, gateway=ExtractionGateway())

    report = extractor.extract_and_ingest(
        "work_a",
        sample_chapters=1,
        source_id=binding["source_id"],
        source_version=binding["source_version"],
        input_hash=binding["input_hash"],
        evidence_refs=binding["evidence_refs"],
        evaluation_ref=binding["evaluation_ref"],
        submitted_by=binding["submitted_by"],
    )
    record = extractor.candidate_store.get(report["candidate_id"])
    package = record.package

    assert report["status"] == "EVALUATION_CANDIDATE"
    assert report["formal_knowledge_written"] is False
    assert package["entities"]["characters"][0]["name"] == "角色A"
    assert package["causal_events"]["causal_events"][0]["narrative_order"] == 1
    assert package["voice_profiles"]["voice_profiles"][0]["tone"] == "克制"
    assert package["continuity"]["chapters"][0]["unresolved_hooks"] == ["测试悬念"]
    assert "characters" not in package
    assert not (temp_workspace.projects_dir / "work_a" / "entities").exists()


def test_bad_model_shape_is_a_structured_schema_failure():
    with pytest.raises(ExtractionSchemaError) as exc_info:
        UniversalAutoExtractor._parse_model_result(
            {"items": []}, "entities", "characters"
        )

    assert exc_info.value.code == "INCOMPLETE_INPUT"
    assert exc_info.value.label == "entities"


def test_unapproved_candidate_cannot_project_formal_knowledge(temp_workspace):
    store = CandidateStore(temp_workspace)
    record = store.submit(
        {
            "slug": "story-candidate",
            "version": "candidate-v1",
            "package": _formal_package("work_a"),
        },
        **_synthetic_binding(temp_workspace),
    )
    ingestor = CandidateIngestor(temp_workspace)

    pending = ingestor.ingest_work_candidates("work_a", [record.candidate_id])

    assert pending["status"] == "REVIEW_REQUIRED"
    assert pending["error_code"] == "CANDIDATE_APPROVAL_REQUIRED"
    assert pending["formal_knowledge_written"] is False
    assert not (temp_workspace.projects_dir / "work_a" / "entities").exists()
    with sqlite3.connect(temp_workspace.sqlite_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM entities WHERE work_id = 'work_a'"
        ).fetchone()[0] == 0


def test_approved_candidate_uses_normalized_schema_for_formal_projection(temp_workspace):
    store = CandidateStore(temp_workspace)
    record = store.submit(
        {
            "slug": "approved-story-candidate",
            "version": "candidate-v1",
            "package": _formal_package("work_a"),
        },
        **_synthetic_binding(temp_workspace),
    )
    _promote_synthetic_candidate(store, record)

    ingestor = CandidateIngestor(temp_workspace)
    ingestor.candidate_store = store
    report = ingestor.ingest_work_candidates("work_a", [record.candidate_id])

    assert report["status"] == "SUCCESS"
    assert report["formal_knowledge_written"] is True
    assert (temp_workspace.projects_dir / "work_a" / "entities" / "relationships.yaml").is_file()
    with sqlite3.connect(temp_workspace.sqlite_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM entities WHERE work_id = 'work_a'"
        ).fetchone()[0] == 1


def test_candidate_ingestor_persists_configured_project_relative_file_path(temp_workspace):
    custom_projects = temp_workspace.workspace_root / "custom-projects"
    config = temp_workspace.model_copy(update={"projects_dir": custom_projects})
    config.ensure_directories()
    binding = _synthetic_binding(config)
    store = CandidateStore(config)
    record = store.submit(
        {
            "slug": "custom-project-candidate",
            "version": "candidate-v1",
            "package": _formal_package("work_a"),
        },
        **binding,
    )
    _promote_synthetic_candidate(store, record)

    ingestor = CandidateIngestor(config)
    ingestor.candidate_store = store
    report = ingestor.ingest_work_candidates("work_a", [record.candidate_id])

    assert report["status"] == "SUCCESS"
    with sqlite3.connect(config.sqlite_path) as connection:
        row = connection.execute(
            "SELECT file_path FROM entities WHERE work_id = ? AND entity_id = ?",
            ("work_a", "char_hero"),
        ).fetchone()
    assert row == ("custom-projects/work_a/entities/characters/char_hero.md",)


def test_extract_all_defaults_to_candidate_only():
    parameter = inspect.signature(extract_all_work).parameters["auto_ingest"]

    assert parameter.default.default is False
