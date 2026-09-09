from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from fxi.core.canonical import sha256_hex
from fxi.sources.auto_extractor import (
    ExtractionExecutionError,
    ExtractionInputError,
    ExtractionSchemaError,
    UniversalAutoExtractor,
)
from fxi.sources.batch_extractor import LunaBatchExtractor
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


def _write_chapter(config, work_id: str = "work_a") -> Path:
    directory = config.sources_dir / work_id / "chapters"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ch001.md"
    path.write_text("第一章\n\n角色A进入测试地点。\n\n章节结尾。", encoding="utf-8")
    return path


def _candidate_binding(config, *, text: str = "synthetic extraction evidence"):
    """Create the immutable synthetic binding required by extractor entrypoints."""
    input_hash = sha256_hex(text)
    VersionedStore(config.sources_dir).create_snapshot(
        "synthetic-source",
        [
            SourceDocumentInput(
                document_id="synthetic-document",
                chapter_index=1,
                raw_bytes=text.encode("utf-8"),
                text=text,
                relative_path="synthetic.txt",
                expected_content_hash=input_hash,
            )
        ],
        version="synthetic-v1",
    )
    return {
        "source_id": "synthetic-source",
        "source_version": "synthetic-v1",
        "input_hash": input_hash,
        "evidence_refs": [
            {
                "evidence_id": "synthetic-evidence",
                "source_id": "synthetic-source",
                "source_version": "synthetic-v1",
                "document_id": "synthetic-document",
                "start_char": 0,
                "end_char": len(text),
                "excerpt_hash": input_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": "synthetic-evaluation",
        "submitted_by": "synthetic-extractor",
    }


def _response(prompt: str) -> dict[str, object]:
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
                "attributes": {},
                "description": "测试角色",
                "phases": [],
                "abilities": [],
            }
        ],
        "items": [],
    }


class _Gateway:
    def __init__(self, failing_stage: str | None = None):
        self.failing_stage = failing_stage
        self.calls: list[tuple[str, str]] = []

    def complete(self, task_type: str, prompt: str, **_kwargs):
        if task_type == "style_mining":
            stage = "style_profile"
        elif "CHAPTER ENDINGS:" in prompt:
            stage = "chapter_continuity"
        elif '"causal_events"' in prompt:
            stage = "causal_events"
        elif '"voice_profiles"' in prompt:
            stage = "voice_profiles"
        elif '"relationships"' in prompt:
            stage = "relationships"
        else:
            stage = "entities"
        self.calls.append((stage, prompt))
        if stage == self.failing_stage:
            raise RuntimeError("fake model failure")
        return _response(prompt)


class _InvalidGateway:
    def complete(self, _task_type: str, _prompt: str, **_kwargs):
        return {"is_valid": True}


def _formal_count(config, table: str, work_id: str = "work_a") -> int:
    with sqlite3.connect(config.sqlite_path) as connection:
        return int(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE work_id = ?", (work_id,)
            ).fetchone()[0]
        )


def test_auto_model_failure_is_structured_and_never_creates_candidate(temp_workspace):
    _write_chapter(temp_workspace)
    gateway = _Gateway(failing_stage="style_profile")
    binding = _candidate_binding(temp_workspace)

    with pytest.raises(ExtractionExecutionError) as exc_info:
        UniversalAutoExtractor(temp_workspace, gateway=gateway).extract_and_ingest(
            "work_a", sample_chapters=1, **binding
        )

    error = exc_info.value
    assert error.code == "MODEL_EXTRACTION_FAILED"
    assert error.label == "style_profile"
    assert error.retryable is True
    assert "fake model failure" in str(error)
    assert len([stage for stage, _ in gateway.calls if stage == "style_profile"]) == 1
    assert _formal_count(temp_workspace, "entities") == 0
    assert not list((temp_workspace.materials_dir / "candidates").glob("*.json"))


def test_auto_invalid_source_is_explicitly_reported(temp_workspace):
    chapter = _write_chapter(temp_workspace)
    binding = _candidate_binding(temp_workspace)
    chapter.write_bytes(b"\xff\xfe\xfd")

    with pytest.raises(ExtractionInputError) as exc_info:
        UniversalAutoExtractor(temp_workspace, gateway=_Gateway()).extract_and_ingest(
            "work_a", sample_chapters=1, **binding
        )

    error = exc_info.value
    assert error.code == "SOURCE_READ_FAILED"
    assert error.label == "chapter[1]"
    assert error.retryable is False
    assert _formal_count(temp_workspace, "entities") == 0


def test_batch_model_failure_records_failed_batch_without_candidate(temp_workspace):
    _write_chapter(temp_workspace)
    gateway = _Gateway(failing_stage="causal_events")
    extractor = LunaBatchExtractor(temp_workspace, gateway=gateway)
    binding = _candidate_binding(temp_workspace)

    with pytest.raises(ExtractionExecutionError) as exc_info:
        extractor.extract_all_batched(
            "work_a", batch_size=1, max_concurrency=1, resume=False, **binding
        )

    error = exc_info.value
    assert error.code == "MODEL_EXTRACTION_FAILED"
    assert error.label == "causal_events"
    assert error.retryable is True
    assert len([stage for stage, _ in gateway.calls if stage == "causal_events"]) == 1

    state_path = temp_workspace.data_dir / "work_a.luna-extraction.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "FAILED"
    assert state["failure"]["code"] == "MODEL_EXTRACTION_FAILED"
    assert state["failure"]["label"] == "causal_events"
    assert state["failure"]["batch_number"] == 1
    assert state["failure"]["phase"] == "extract"
    assert state["completed"] == []
    assert _formal_count(temp_workspace, "entities") == 0
    assert not list((temp_workspace.materials_dir / "candidates").glob("*.json"))


def test_batch_invalid_model_shape_is_visible_and_not_an_empty_candidate(temp_workspace):
    _write_chapter(temp_workspace)
    extractor = LunaBatchExtractor(temp_workspace, gateway=_InvalidGateway())
    binding = _candidate_binding(temp_workspace)

    with pytest.raises(ExtractionSchemaError) as exc_info:
        extractor.extract_all_batched(
            "work_a", batch_size=1, max_concurrency=1, resume=False, **binding
        )

    error = exc_info.value
    assert error.code == "INCOMPLETE_INPUT"
    assert error.label == "characters"
    state = json.loads(
        (temp_workspace.data_dir / "work_a.luna-extraction.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["status"] == "FAILED"
    assert state["failure"]["code"] == "INCOMPLETE_INPUT"
    assert not list((temp_workspace.materials_dir / "candidates").glob("*.json"))
