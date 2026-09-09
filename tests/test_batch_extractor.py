import json
from pathlib import Path
from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.sources.batch_extractor import LunaBatchExtractor
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.storage.versioned_store import SourceDocumentInput, VersionedStore


class FakeGateway:
    def __init__(self):
        self.calls = []
        self._causal_count = 0

    def complete(self, task_type, prompt, **kwargs):
        self.calls.append((task_type, prompt, kwargs))
        if "ending_location" in prompt:
            chapters = []
            for line in prompt.splitlines():
                if line.startswith("CHAPTER ") and line.split()[1].isdigit():
                    number = int(line.split()[1])
                    chapters.append(
                        {
                            "chapter_index": number,
                            "title": f"第{number}章",
                            "ending_location": "测试地点",
                            "active_characters": ["林七夜"],
                            "ending_situation": "测试情境",
                            "unresolved_hooks": ["测试悬念"],
                        }
                    )
            return {"chapters": chapters}
        if '"voice_profiles"' in prompt:
            return {
                "voice_profiles": [
                    {
                        "name": "林七夜",
                        "tone": "冷静",
                        "speech_style": "简洁",
                        "catchphrases": [],
                        "gestures": [],
                        "taboos": [],
                    }
                ]
            }
        if '"relationships"' in prompt:
            return {"relationships": []}
        if '"causal_events"' in prompt:
            self._causal_count += 1
            return {
                "causal_events": [
                    {
                        "event_id": f"synthetic-event-{self._causal_count}",
                        "scene_uuid": f"synthetic-scene-{self._causal_count}",
                        "narrative_order": 1,
                        "physical_time": "测试时间",
                        "summary": "测试事件",
                        "is_pod_candidate": True,
                        "pod_analysis": "测试分歧",
                    }
                ]
            }
        return {
            "characters": [
                {
                    "entity_id": "char_linqiye",
                    "name": "林七夜",
                    "attributes": {"role": "主角"},
                    "description": "测试角色",
                    "phases": [],
                }
            ],
            "items": [],
        }


class _ApprovalVerifier:
    """为历史回归测试提供可消费、可复验的服务端审批边界。"""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def add(self, record: dict[str, object]) -> None:
        self.records[str(record["approval_id"])] = dict(record)

    def get_approval(self, approval_id: str) -> dict[str, object]:
        try:
            return dict(self.records[approval_id])
        except KeyError as exc:
            raise ValidationError(f"合成审批不存在: {approval_id}") from exc

    def consume(self, approval_id: str, consumer_id: str) -> None:
        record = self.records.get(approval_id)
        if record is None or record.get("consumed_by") is not None:
            raise ValidationError(f"合成审批不可消费: {approval_id}")
        record["consumed_by"] = consumer_id


def _make_chapters(config, count=3):
    directory = config.sources_dir / "work_a" / "chapters"
    directory.mkdir(parents=True)
    for index in range(1, count + 1):
        (directory / f"ch{index:03d}.md").write_text(
            f"第{index}章 测试\n\n林七夜进入测试地点。\n\n"
            f"“第{index}章对白。”\n\n章节结尾。",
            encoding="utf-8",
        )


def _candidate_binding(config):
    text = "synthetic batch evidence"
    text_hash = sha256_hex(text)
    VersionedStore(config.sources_dir).create_snapshot(
        "synthetic-source",
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
        version="source-v1",
    )
    return {
        "source_id": "synthetic-source",
        "source_version": "source-v1",
        "input_hash": text_hash,
        "evidence_refs": [
            {
                "evidence_id": "synthetic-evidence",
                "source_id": "synthetic-source",
                "source_version": "source-v1",
                "document_id": "synthetic-document",
                "start_char": 0,
                "end_char": len(text),
                "excerpt_hash": text_hash,
                "normalization_version": "newline-bom-v1",
            }
        ],
        "evaluation_ref": "synthetic-evaluation",
        "submitted_by": "synthetic-extractor",
    }


def test_batch_ranges_and_resume(temp_workspace):
    _make_chapters(temp_workspace)
    binding = _candidate_binding(temp_workspace)
    first_gateway = FakeGateway()
    extractor = LunaBatchExtractor(temp_workspace, gateway=first_gateway)

    report = extractor.extract_all_batched(
        "work_a",
        batch_size=2,
        provider_override="luna_local",
        model_override="gpt-5.6-luna",
        **binding,
    )

    assert report.total_batches == 2
    assert report.completed_batches == 2
    assert report.continuity == 3
    assert len(first_gateway.calls) == 10
    assert all(
        call[2]["provider_override"] == "luna_local"
        and call[2]["model_override"] == "gpt-5.6-luna"
        for call in first_gateway.calls
    )
    assert report.characters == 2
    assert report.formal_knowledge_written is False

    second_gateway = FakeGateway()
    resumed = LunaBatchExtractor(temp_workspace, gateway=second_gateway)
    resumed_report = resumed.extract_all_batched("work_a", batch_size=2, **binding)

    assert resumed_report.completed_batches == 2
    assert second_gateway.calls == []


def test_ranges_reject_invalid_batch_size():
    try:
        LunaBatchExtractor._ranges(3, 0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_batch_extractor_max_chapters_and_formal_ingestion(temp_workspace):
    _make_chapters(temp_workspace, count=5)
    binding = _candidate_binding(temp_workspace)
    gateway = FakeGateway()
    extractor = LunaBatchExtractor(temp_workspace, gateway=gateway)

    # 1. 测试 max_chapters=3 切片（5章中仅处理前3章，batch_size=2 -> 2个批次）
    report = extractor.extract_all_batched(
        "work_a",
        batch_size=2,
        max_chapters=3,
        provider_override="luna_local",
        model_override="gpt-5.6-luna",
        **binding,
    )
    assert report.total_batches == 2
    assert report.completed_batches == 2
    assert report.continuity == 3

    # 2. 测试候选入库引擎 ingest_candidates_to_formal
    pending_report = extractor.ingest_candidates_to_formal("work_a")
    assert pending_report["status"] == "REVIEW_REQUIRED"
    assert pending_report["formal_knowledge_written"] is False

    approval_verifier = _ApprovalVerifier()
    candidate_store = CandidateStore(temp_workspace, approval_verifier=approval_verifier)
    extractor.candidate_store = candidate_store
    for index, candidate_id in enumerate(report.candidate_ids, start=1):
        record = candidate_store.get(candidate_id)
        head_path = temp_workspace.materials_dir / "candidate_heads" / f"{record.slug}.json"
        current_version = (
            json.loads(head_path.read_text(encoding="utf-8"))["active_version"]
            if head_path.is_file()
            else "style-v0"
        )
        approval = {
            "approval_id": f"approval-{index}",
            "action": "candidate_promotion",
            "target_id": record.candidate_id,
            "target_hash": record.package_hash,
            "expected_version": current_version,
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
        approval_verifier.add(approval)
        candidate_store.promote(
            record.candidate_id,
            approval,
            actor_id="reviewer",
            approval_consumer=approval_verifier.consume,
        )

    ingest_report = extractor.ingest_candidates_to_formal("work_a")
    assert ingest_report["status"] == "SUCCESS"
    assert ingest_report["characters"] >= 1
    assert ingest_report["continuity"] == 3
    assert ingest_report["causal_events"] >= 1

    # 3. 验证 SQLite chapter_continuity 准确沉淀
    from fxi.timeline.continuity import ContinuityManager
    cm = ContinuityManager(temp_workspace)
    c1 = cm.get_continuity("work_a", 1)
    assert c1 is not None
    assert c1["ending_location"] == "测试地点"
    assert "林七夜" in c1["active_characters"]

    # 4. 验证实体卡落盘与 SQLite entities
    char_file = temp_workspace.projects_dir / "work_a" / "entities" / "characters" / "char_linqiye.md"
    assert char_file.is_file()
    char_text = char_file.read_text(encoding="utf-8")
    assert "entity_id: char_linqiye" in char_text
    assert "林七夜" in char_text

    # 5. 验证 SQLite 派生表同步
    from fxi.storage.sqlite_client import DatabaseClient
    db_client = DatabaseClient(temp_workspace.sqlite_path)
    with db_client.get_connection() as conn:
        ent_rows = conn.execute("SELECT * FROM entities WHERE work_id = 'work_a'").fetchall()
        assert len(ent_rows) >= 1
        cont_rows = conn.execute("SELECT * FROM chapter_continuity WHERE work_id = 'work_a'").fetchall()
        assert len(cont_rows) == 3


def test_batch_extractor_start_chapter_and_progress(temp_workspace):
    _make_chapters(temp_workspace, count=8)
    binding = _candidate_binding(temp_workspace)
    gateway = FakeGateway()
    extractor = LunaBatchExtractor(temp_workspace, gateway=gateway)

    progress_events = []

    def on_progress(completed_count, total_batches, ch_range, counts, event):
        progress_events.append((completed_count, total_batches, ch_range, counts, event))

    # 测试 start_chapter=3, max_chapters=4 (第3~6章), batch_size=2
    report = extractor.extract_all_batched(
        "work_a",
        batch_size=2,
        start_chapter=3,
        max_chapters=4,
        provider_override="luna_local",
        model_override="gpt-5.6-luna",
        on_progress=on_progress,
        **binding,
    )

    assert report.total_batches == 2
    assert report.completed_batches == 2
    assert report.continuity == 4

    # 验证 progress 事件触发 (1个 init, 2个 batch_done)
    assert len(progress_events) == 3
    assert progress_events[0][4] == "init"
    assert progress_events[0][1] == 2  # total_batches

    assert progress_events[1][4] == "batch_done"
    assert progress_events[1][0] == 1  # completed_count
    assert progress_events[1][2] == (3, 4)  # 第3~4章

    assert progress_events[2][4] == "batch_done"
    assert progress_events[2][0] == 2  # completed_count
    assert progress_events[2][2] == (5, 6)  # 第5~6章
