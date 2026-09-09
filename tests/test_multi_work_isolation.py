from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from fxi.core.canonical import sha256_hex
from fxi.character_knowledge.ooc_checker import OOCChecker
from fxi.domain.canonical import CanonicalRegistry
from fxi.domain.relations import RelationManager
from fxi.materials_skills.style_manager import StyleManager
from fxi.sources.auto_extractor import UniversalAutoExtractor
from fxi.sources.batch_extractor import LunaBatchExtractor
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore
from fxi.timeline.fact_checker import ContinuityFactChecker


class FakeGateway:
    def complete(self, task_type, prompt, **kwargs):
        if "CHAPTER ENDINGS:" in prompt:
            return {
                "chapters": [
                    {
                        "chapter_index": 1,
                        "title": "第一章",
                        "ending_location": "测试地点",
                        "active_characters": ["角色A"],
                        "ending_situation": "测试情境",
                        "unresolved_hooks": ["测试悬念"],
                    }
                ]
            }
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
                    "phases": [],
                }
            ],
            "items": [],
        }


class FailingGateway:
    def complete(self, task_type, prompt, **kwargs):
        raise RuntimeError("fake model failure")


class InvalidShapeGateway:
    def complete(self, task_type, prompt, **kwargs):
        return {"is_valid": True}


def _chapter(config, work_id: str) -> Path:
    directory = config.sources_dir / work_id / "chapters"
    directory.mkdir(parents=True)
    path = directory / "ch001.md"
    path.write_text("第一章\n\n角色A进入测试地点。", encoding="utf-8")
    return path


def _candidate_binding(config, work_id: str, text: str) -> dict[str, object]:
    source_id = f"synthetic-source-{work_id}"
    source_version = "synthetic-v1"
    text_hash = sha256_hex(text)
    VersionedStore(config.sources_dir).create_snapshot(
        source_id,
        [
            SourceDocumentInput(
                document_id="synthetic-document",
                chapter_index=1,
                raw_bytes=text.encode("utf-8"),
                text=text,
                relative_path="synthetic.md",
                expected_content_hash=text_hash,
            )
        ],
        version=source_version,
    )
    return {
        "source_id": source_id,
        "source_version": source_version,
        "input_hash": text_hash,
        "evidence_refs": [
            {
                "evidence_id": f"synthetic-evidence-{work_id}",
                "source_id": source_id,
                "source_version": source_version,
                "document_id": "synthetic-document",
                "start_char": 0,
                "end_char": len(text),
                "excerpt_hash": text_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": f"synthetic-evaluation-{work_id}",
        "submitted_by": "synthetic-extractor",
    }


def _count(config, table: str, work_id: str) -> int:
    with sqlite3.connect(config.sqlite_path) as conn:
        row = conn.execute(
            f"SELECT count(*) FROM {table} WHERE work_id = ?", (work_id,)
        ).fetchone()
    return int(row[0])


def test_rules_and_files_are_work_scoped_without_fanfic_fallback(temp_workspace):
    for work_id, entity_id in (("work_a", "char_a"), ("work_b", "char_b")):
        directory = temp_workspace.projects_dir / work_id / "entities"
        directory.mkdir(parents=True)
        (directory / "canonical.yaml").write_text(
            yaml.safe_dump({"canonical_ids": {"共享名称": entity_id}}, allow_unicode=True),
            encoding="utf-8",
        )

    registry = CanonicalRegistry(temp_workspace)
    assert registry.get_canonical_id("work_a", "共享名称") == "char_a"
    assert registry.get_canonical_id("work_b", "共享名称") == "char_b"
    assert registry.get_canonical_id("work_a", "未配置名称") != "char_lin_qiye"

    parent_style = temp_workspace.projects_dir / "source" / "style"
    parent_style.mkdir(parents=True)
    (parent_style / "style_profile.yaml").write_text(
        "work_id: source\nstatus: AVAILABLE\n", encoding="utf-8"
    )
    assert StyleManager(temp_workspace).get_style_profile("source_fanfic")["status"] == "UNAVAILABLE"

    parent_rel = temp_workspace.projects_dir / "source" / "entities"
    parent_rel.mkdir(parents=True)
    (parent_rel / "relationships.yaml").write_text(
        yaml.safe_dump({"relationships": [{"pair": ["角色A", "角色B"]}]}, allow_unicode=True),
        encoding="utf-8",
    )
    assert RelationManager(temp_workspace).load_relationships("source_fanfic") == []


def test_missing_work_rules_and_model_failure_are_visible(temp_workspace):
    continuity = ContinuityFactChecker(temp_workspace, gateway=InvalidShapeGateway())
    result = continuity.check_continuity("work_a", 1, "普通文本", use_llm=False)
    assert any(
        item.issue_type == "configuration_unavailable"
        and item.severity == "incomplete"
        and "INCOMPLETE" in item.message
        for item in result
    )

    ooc = OOCChecker(temp_workspace, gateway=InvalidShapeGateway())
    result = ooc.scan_draft("普通文本", "work_a", speaker_id="char_a", use_llm=False)
    assert any(item.issue_type == "configuration_unavailable" for item in result)

    continuity.gateway = FailingGateway()
    result = continuity._llm_semantic_check(
        "work_a",
        chapter_index=2,
        prev_index=1,
        prev_data={"title": "前章"},
        draft_text="测试文本",
    )
    assert any(item.issue_type == "model_unavailable" for item in result)


def test_auto_extraction_only_creates_candidate(temp_workspace):
    chapter = _chapter(temp_workspace, "work_a")
    binding = _candidate_binding(temp_workspace, "work_a", chapter.read_text(encoding="utf-8"))
    extractor = UniversalAutoExtractor(temp_workspace, gateway=FakeGateway())
    report = extractor.extract_and_ingest("work_a", sample_chapters=1, **binding)

    assert report["status"] == "EVALUATION_CANDIDATE"
    assert report["formal_knowledge_written"] is False
    assert report["configuration_status"] == "UNAVAILABLE"
    assert report["candidate_id"]
    assert not (temp_workspace.projects_dir / "work_a" / "entities").exists()
    assert _count(temp_workspace, "entities", "work_a") == 0
    assert _count(temp_workspace, "causal_events", "work_a") == 0

    candidate = extractor.candidate_store.get(report["candidate_id"])
    assert candidate.package["work_id"] == "work_a"
    assert candidate.package["style_profile"]["work_id"] == "work_a"


def test_batch_extraction_only_creates_candidates_and_isolated_resume_state(temp_workspace):
    chapter = _chapter(temp_workspace, "work_b")
    binding = _candidate_binding(temp_workspace, "work_b", chapter.read_text(encoding="utf-8"))
    extractor = LunaBatchExtractor(temp_workspace, gateway=FakeGateway())
    report = extractor.extract_all_batched(
        "work_b", batch_size=1, max_concurrency=1, resume=False, **binding
    )

    assert report.status == "EVALUATION_CANDIDATE"
    assert report.formal_knowledge_written is False
    assert report.requires_review is True
    assert report.configuration_status == "UNAVAILABLE"
    assert len(report.candidate_ids) == 1
    assert _count(temp_workspace, "entities", "work_b") == 0
    assert _count(temp_workspace, "causal_events", "work_b") == 0
    assert not (temp_workspace.projects_dir / "work_b" / "entities").exists()


def test_extraction_model_failure_is_not_converted_to_empty_candidate(temp_workspace):
    chapter = _chapter(temp_workspace, "work_a")
    binding = _candidate_binding(temp_workspace, "work_a", chapter.read_text(encoding="utf-8"))
    extractor = UniversalAutoExtractor(temp_workspace, gateway=FailingGateway())
    with pytest.raises(RuntimeError, match="fake model failure"):
        extractor.extract_and_ingest("work_a", sample_chapters=1, **binding)
    assert _count(temp_workspace, "entities", "work_a") == 0


def test_legacy_batch_progress_does_not_skip_candidate_generation(temp_workspace):
    chapter = _chapter(temp_workspace, "work_a")
    binding = _candidate_binding(temp_workspace, "work_a", chapter.read_text(encoding="utf-8"))
    state_path = temp_workspace.data_dir / "work_a.luna-extraction.json"
    state_path.write_text(
        json.dumps(
            {
                "work_id": "work_a",
                "title": "work_a",
                "batch_size": 1,
                "provider": "luna_local",
                "model": "gpt-5.6-luna",
                "total_batches": 1,
                "completed": [1],
                "counts": {"characters": 1},
            }
        ),
        encoding="utf-8",
    )

    extractor = LunaBatchExtractor(temp_workspace, gateway=FakeGateway())
    report = extractor.extract_all_batched("work_a", batch_size=1, max_concurrency=1, **binding)

    assert report.completed_batches == 1
    assert report.candidate_ids
    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved_state["pipeline"] == "candidate-only-v1"


def test_invalid_work_config_and_model_shape_are_visible(temp_workspace):
    work_dir = temp_workspace.projects_dir / "work_invalid"
    work_dir.mkdir(parents=True)
    (work_dir / "work.yaml").write_text("[]", encoding="utf-8")

    auto = UniversalAutoExtractor(temp_workspace, gateway=InvalidShapeGateway())
    batch = LunaBatchExtractor(temp_workspace, gateway=InvalidShapeGateway())
    assert auto._work_scope_status("work_invalid")["status"] == "INCOMPLETE"
    assert batch._work_scope_status("work_invalid")["status"] == "INCOMPLETE"

    continuity = ContinuityFactChecker(temp_workspace, gateway=InvalidShapeGateway())
    continuity.gateway = InvalidShapeGateway()
    result = continuity._llm_semantic_check(
        "work_invalid",
        chapter_index=2,
        prev_index=1,
        prev_data={"title": "前章", "active_characters": []},
        draft_text="测试文本",
    )
    assert any(item.issue_type == "model_unavailable" for item in result)

    ooc = OOCChecker(temp_workspace, gateway=InvalidShapeGateway())
    ooc.entity_mgr.get_character_voice = lambda work_id, char_id: {"tone": "克制"}
    ooc.entity_mgr.get_entity = lambda work_id, char_id: {"name": "角色A"}
    ooc.gateway = InvalidShapeGateway()
    result = ooc._llm_voice_check("work_invalid", "char_a", "角色A说话")
    assert any(item.issue_type == "model_unavailable" for item in result)
