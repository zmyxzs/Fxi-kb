"""
Batch extraction for large reference novels.

This module deliberately omits style mining. It extracts only the structured
knowledge that is useful to downstream fan-fiction retrieval.
"""

from __future__ import annotations

import json
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from json_repair import repair_json
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.core.identifiers import validate_work_id
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.model_gateway.gateway import ModelGateway
from fxi.sources.auto_extractor import (
    ExtractionError,
    ExtractionExecutionError,
    ExtractionInputError,
    ExtractionSchemaError,
    _call_model,
    _read_source_text,
    PROMPT_EXTRACT_CAUSAL_EVENTS,
    PROMPT_EXTRACT_ENTITIES,
    PROMPT_EXTRACT_RELATIONSHIPS,
    PROMPT_EXTRACT_VOICE_PROFILES,
    UniversalAutoExtractor,
)
from fxi.storage.sqlite_client import DatabaseClient


PROMPT_EXTRACT_BATCH_CONTINUITY = """You are a novel continuity ledger analyst.
Read the chapter ending snippets below and return one structured snapshot for
each chapter. Do not invent facts that are absent from the snippets.

WORK: {title}
CHAPTER ENDINGS:
{chapter_endings}

Return JSON only:
{{
  "chapters": [
    {{
      "chapter_index": 1,
      "title": "chapter title",
      "ending_location": "specific physical location",
      "active_characters": ["character"],
      "ending_situation": "precise situation at the ending",
      "unresolved_hooks": ["open action or question"]
    }}
  ]
}}
"""


@dataclass
class BatchExtractionReport:
    work_id: str
    title: str
    total_batches: int
    completed_batches: int
    characters: int
    items: int
    events: int
    relationships: int
    continuity: int
    status: str = "EVALUATION_CANDIDATE"
    candidate_ids: tuple[str, ...] = ()
    formal_knowledge_written: bool = False
    configuration_status: str = "UNAVAILABLE"
    configuration_message: str = ""
    requires_review: bool = True


class LunaBatchExtractor:
    """Extract structured story knowledge in resumable Luna-sized batches."""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        gateway: Optional[ModelGateway] = None,
    ):
        self.config = config or load_config()
        self.gateway = gateway or ModelGateway(self.config)
        self.candidate_store = CandidateStore(self.config)

    @staticmethod
    def _validate_work_id(work_id: str) -> str:
        try:
            return validate_work_id(work_id)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _number(path: Path) -> int:
        match = re.search(r"\d+", path.stem)
        return int(match.group(0)) if match else 999999

    def _chapter_files(self, work_id: str) -> list[Path]:
        work_id = self._validate_work_id(work_id)
        directory = self.config.sources_dir / work_id / "chapters"
        if not directory.is_dir():
            raise FileNotFoundError(f"chapter directory does not exist: {directory}")
        files = sorted(
            [
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in {".md", ".txt"}
            ],
            key=self._number,
        )
        if not files:
            raise FileNotFoundError(f"no chapter files found: {directory}")
        return files

    def _title(self, work_id: str) -> str:
        work_id = self._validate_work_id(work_id)
        client = DatabaseClient(self.config.sqlite_path)
        with client.get_connection() as conn:
            row = conn.execute(
                "SELECT title FROM works WHERE work_id = ?", (work_id,)
            ).fetchone()
        return row["title"] if row and row["title"] else work_id

    def _state_path(self, work_id: str) -> Path:
        work_id = self._validate_work_id(work_id)
        return self.config.data_dir / f"{work_id}.luna-extraction.json"

    def _load_state(self, work_id: str) -> dict[str, Any]:
        path = self._state_path(work_id)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法读取批量抽取进度 {path.name}: {type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"批量抽取进度 {path.name} 必须是对象")
        return value

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path(state["work_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(state, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(serialized)
            temp_name = handle.name
        Path(temp_name).replace(path)
        if path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"批量抽取进度读回内容与写入内容不一致: {path.name}")

    @staticmethod
    def _failure_error(exc: Exception, phase: str) -> ExtractionError:
        if isinstance(exc, ExtractionError):
            return exc
        return ExtractionExecutionError(
            f"批量抽取 {phase} 阶段失败: {type(exc).__name__}",
            label=phase,
            cause_type=type(exc).__name__,
        )

    def _record_failure(
        self,
        state: dict[str, Any],
        exc: Exception,
        *,
        batch_number: Optional[int],
        phase: str,
    ) -> ExtractionError:
        """记录失败后仍抛出；失败状态不能被伪装为可恢复完成。"""
        error = self._failure_error(exc, phase)
        state["status"] = "FAILED"
        state["failure"] = error.to_diagnostic(
            batch_number=batch_number,
            phase=phase,
        )
        state["failed_batch"] = batch_number
        self._save_state(state)
        return error

    def _raise_recorded_failure(
        self,
        state: dict[str, Any],
        exc: Exception,
        *,
        batch_number: Optional[int],
        phase: str,
    ) -> None:
        error = self._record_failure(
            state,
            exc,
            batch_number=batch_number,
            phase=phase,
        )
        if error is exc:
            raise
        raise error from exc

    @staticmethod
    def _ranges(total: int, batch_size: int) -> list[tuple[int, int]]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        return [
            (start, min(start + batch_size - 1, total))
            for start in range(1, total + 1, batch_size)
        ]

    def _merge_entity(
        self,
        work_id: str,
        item: dict[str, Any],
        category: str,
        entity_map: dict[str, str],
        phase_count: list[int],
    ) -> Optional[str]:
        raise RuntimeError(
            "批量抽取已迁移为 candidate-only；正式实体/相态必须经过审核提交"
        )

    def _merge_voice(
        self,
        work_id: str,
        voice: dict[str, Any],
        entity_map: dict[str, str],
    ) -> None:
        raise RuntimeError(
            "批量抽取已迁移为 candidate-only；正式声线必须经过审核提交"
        )

    def _merge_relationships(
        self, work_id: str, incoming: list[dict[str, Any]]
    ) -> int:
        raise RuntimeError(
            "批量抽取已迁移为 candidate-only；正式关系必须经过审核提交"
        )

    def _register_events(
        self,
        work_id: str,
        batch_number: int,
        start: int,
        end: int,
        events: list[dict[str, Any]],
        previous_event_id: Optional[str],
    ) -> tuple[int, Optional[str]]:
        raise RuntimeError(
            "批量抽取已迁移为 candidate-only；正式事件必须经过审核提交"
        )

    @staticmethod
    def _parse(raw: Any, fallback: Optional[dict[str, Any]] = None, *, label: str = "result") -> dict[str, Any]:
        if isinstance(raw, Mapping):
            return dict(raw)
        try:
            value = repair_json(raw, return_objects=True)
        except (TypeError, ValueError) as exc:
            raise ExtractionSchemaError(
                f"模型抽取 {label} 结果无法解析: {type(exc).__name__}",
                label=label,
            ) from exc
        if not isinstance(value, Mapping):
            raise ExtractionSchemaError(
                f"模型抽取 {label} 结果必须是对象",
                label=label,
            )
        return dict(value)

    @staticmethod
    def _candidate_slug(work_id: str, batch_number: int, start_ch: Optional[int] = None) -> str:
        digest = sha256_hex(work_id)[:16]
        if start_ch is not None and start_ch > 0:
            return f"fxi-batch-{digest}-ch{start_ch:04d}"
        return f"fxi-batch-{digest}-{batch_number:04d}"

    def _work_scope_status(self, work_id: str) -> dict[str, str]:
        """读取指定作品的 work.yaml 状态，不从其他作品继承配置。"""
        work_id = self._validate_work_id(work_id)
        path = self.config.projects_dir / work_id / "work.yaml"
        if not path.is_file():
            return {
                "status": "UNAVAILABLE",
                "message": f"作品 [{work_id}] 没有 work.yaml；候选不得注入默认作品规则。",
            }
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            return {
                "status": "INCOMPLETE",
                "message": f"作品 [{work_id}] 的 work.yaml 不可读取: {type(exc).__name__}",
            }
        if not isinstance(data, dict):
            return {
                "status": "INCOMPLETE",
                "message": f"作品 [{work_id}] 的 work.yaml 必须是对象。",
            }
        return {"status": "AVAILABLE", "message": "已加载指定作品的 work.yaml。"}

    @staticmethod
    def _tail(path: Path) -> tuple[str, str]:
        text = _read_source_text(path, f"chapter[{LunaBatchExtractor._number(path)}]")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return (lines[0] if lines else path.stem, "\n".join(lines[-12:]))

    def _continuity_prompt(self, title: str, files: list[Path]) -> str:
        endings = []
        for path in files:
            chapter = self._number(path)
            chapter_title, tail = self._tail(path)
            endings.append(f"CHAPTER {chapter} | {chapter_title}\n{tail}")
        return PROMPT_EXTRACT_BATCH_CONTINUITY.format(
            title=title,
            chapter_endings="\n\n".join(endings),
        )

    def _persist_continuity(
        self, work_id: str, files: list[Path], raw: Any
    ) -> int:
        raise RuntimeError(
            "批量抽取已迁移为 candidate-only；正式连续性台账必须经过审核提交"
        )

    def _map_batch(
        self,
        work_id: str,
        title: str,
        batch_number: int,
        start: int,
        end: int,
        files: list[Path],
        provider_override: str,
        model_override: str,
    ) -> dict[str, Any]:
        """Map 阶段：纯只读无状态并发提取模型输出 (无 SQLite / 文件写入，安全并发)"""
        scope_status = self._work_scope_status(work_id)
        scope_note = (
            "\nWORK-SCOPED CONFIGURATION STATUS:\n"
            + json.dumps(scope_status, ensure_ascii=False)
            + "\nUse only this work's source text and configuration; never inherit another work's rules.\n"
        )
        summary, dialogue, _ = UniversalAutoExtractor._build_text_corpora(
            files, sample_chapters=len(files)
        )
        prompts = {
            "entities": scope_note + PROMPT_EXTRACT_ENTITIES.format(
                title=title, chapters_summary=summary
            ),
            "causal": scope_note + PROMPT_EXTRACT_CAUSAL_EVENTS.format(
                title=title, work_id=work_id, chapters_summary=summary
            ),
            "voice": scope_note + PROMPT_EXTRACT_VOICE_PROFILES.format(
                title=title, chapters_summary=dialogue
            ),
            "relationships": scope_note + PROMPT_EXTRACT_RELATIONSHIPS.format(
                title=title, chapters_summary=dialogue
            ),
        }
        labels = {
            "entities": "entities",
            "causal": "causal_events",
            "voice": "voice_profiles",
            "relationships": "relationships",
        }
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                name: executor.submit(
                    _call_model,
                    self.gateway,
                    labels[name],
                    "fast_extraction",
                    prompt,
                    provider_override=provider_override,
                    model_override=model_override,
                )
                for name, prompt in prompts.items()
            }
            raw = {name: future.result() for name, future in futures.items()}

        continuity_raw = _call_model(
            self.gateway,
            "chapter_continuity",
            "fast_extraction",
            scope_note + self._continuity_prompt(title, files),
            provider_override=provider_override,
            model_override=model_override,
        )
        return {
            "batch_number": batch_number,
            "start": start,
            "end": end,
            "files": files,
            "raw": raw,
            "continuity_raw": continuity_raw,
        }

    def _reduce_batch(
        self,
        work_id: str,
        title: str,
        payload: dict[str, Any],
        candidate_binding: Mapping[str, Any],
    ) -> tuple[dict[str, int], Optional[str]]:
        """Reduce 阶段：校验并保存作品级候选，不写入正式知识。"""
        batch_number = payload["batch_number"]
        start = payload["start"]
        end = payload["end"]
        files = payload["files"]
        raw = payload["raw"]

        entity_data = self._parse(raw["entities"], label="entities")
        causal_data = self._parse(raw["causal"], label="causal_events")
        voice_data = self._parse(raw["voice"], label="voice_profiles")
        relation_data = self._parse(raw["relationships"], label="relationships")
        continuity_data = self._parse(payload["continuity_raw"], label="chapter_continuity")
        collections = (
            (entity_data, "characters"),
            (entity_data, "items"),
            (causal_data, "causal_events"),
            (voice_data, "voice_profiles"),
            (relation_data, "relationships"),
            (continuity_data, "chapters"),
        )
        for value, key in collections:
            if not isinstance(value.get(key), list):
                raise ExtractionSchemaError(
                    f"模型抽取 {key} 结果缺少列表字段",
                    label=key,
                )

        candidate_package = {
            "work_id": work_id,
            "title": title,
            "batch_number": batch_number,
            "chapter_range": [start, end],
            "source_files": [path.name for path in files],
            "entities": entity_data,
            "causal_events": causal_data,
            "voice_profiles": voice_data,
            "relationships": relation_data,
            "continuity": continuity_data,
            "configuration": self._work_scope_status(work_id),
            "status": "EVALUATION_CANDIDATE",
        }
        slug = self._candidate_slug(work_id, batch_number, start_ch=start if start > 0 else None)
        version_str = f"batch-ch{start:04d}" if start and 0 < start < 999999 else f"batch-{batch_number:04d}"
        candidate = self.candidate_store.submit(
            {
                "slug": slug,
                "version": version_str,
                "package": candidate_package,
            },
            work_id=work_id,
            source_id=candidate_binding.get("source_id"),
            source_version=candidate_binding.get("source_version"),
            input_hash=candidate_binding.get("input_hash"),
            evidence_refs=candidate_binding.get("evidence_refs"),
            evaluation_ref=candidate_binding.get("evaluation_ref"),
            submitted_by=candidate_binding.get("submitted_by"),
            require_scope=True,
        )
        return (
            {
                "characters": len(entity_data["characters"]),
                "items": len(entity_data["items"]),
                "phases": sum(len(item.get("phases") or []) for item in entity_data["characters"] if isinstance(item, dict)),
                "events": len(causal_data["causal_events"]),
                "relationships": len(relation_data["relationships"]),
                "continuity": len(continuity_data["chapters"]),
            },
            candidate.candidate_id,
        )

    def _extract_batch(
        self,
        work_id: str,
        title: str,
        batch_number: int,
        start: int,
        end: int,
        files: list[Path],
        provider_override: str,
        model_override: str,
        candidate_binding: Mapping[str, Any],
    ) -> tuple[dict[str, int], Optional[str]]:
        payload = self._map_batch(
            work_id=work_id,
            title=title,
            batch_number=batch_number,
            start=start,
            end=end,
            files=files,
            provider_override=provider_override,
            model_override=model_override,
        )
        return self._reduce_batch(
            work_id=work_id,
            title=title,
            payload=payload,
            candidate_binding=candidate_binding,
        )

    def extract_all_batched(
        self,
        work_id: str,
        batch_size: int = 20,
        max_concurrency: int = 4,
        provider_override: str = "luna_local",
        model_override: str = "gpt-5.6-luna",
        resume: bool = True,
        start_chapter: Optional[int] = None,
        max_chapters: Optional[int] = None,
        on_progress: Optional[Callable[[int, int, tuple[int, int], dict[str, int], str], None]] = None,
        *,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        input_hash: Optional[str] = None,
        evidence_refs: Optional[list[Mapping[str, Any]]] = None,
        evaluation_ref: Optional[str] = None,
        submitted_by: Optional[str] = None,
    ) -> BatchExtractionReport:
        work_id = self._validate_work_id(work_id)
        if not all((source_id, source_version, input_hash, evidence_refs, evaluation_ref, submitted_by)):
            raise ExtractionInputError(
                "候选抽取必须提供来源版本、输入哈希、证据、评估引用和受控提交身份",
                label="candidate_binding",
            )
        candidate_binding = {
            "source_id": source_id,
            "source_version": source_version,
            "input_hash": input_hash,
            "evidence_refs": evidence_refs,
            "evaluation_ref": evaluation_ref,
            "submitted_by": submitted_by,
        }
        scope_status = self._work_scope_status(work_id)
        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须是正整数")
        files = self._chapter_files(work_id)
        if start_chapter is not None:
            if start_chapter < 1:
                raise ValueError("start_chapter 必须是正整数")
            numbered = [(self._number(p), p) for p in files]
            has_real_numbers = any(n != 999999 for n, _ in numbered)
            if has_real_numbers:
                files = [p for n, p in numbered if n >= start_chapter]
            else:
                files = files[start_chapter - 1 :]
        if max_chapters is not None:
            if max_chapters < 1:
                raise ValueError("max_chapters 必须是正整数")
            files = files[:max_chapters]
        if not files:
            raise ValueError(f"指定的章节范围无可用章节文件 (start_chapter={start_chapter}, max_chapters={max_chapters})")
        title = self._title(work_id)
        ranges = self._ranges(len(files), batch_size)
        state = self._load_state(work_id) if resume else {}
        expected = {
            "pipeline": "candidate-only-v1",
            "work_id": work_id,
            "title": title,
            "batch_size": batch_size,
            "provider": provider_override,
            "model": model_override,
            "total_batches": len(ranges),
        }
        if start_chapter is not None:
            expected["start_chapter"] = start_chapter
        if max_chapters is not None:
            expected["max_chapters"] = max_chapters
        if any(state.get(key) != value for key, value in expected.items()):
            prev_ids = list(state.get("all_candidate_ids") or state.get("candidate_ids", []))
            state = {**expected, "completed": [], "candidate_ids": [], "counts": {}, "all_candidate_ids": prev_ids}
        completed = {int(value) for value in state.get("completed", [])}
        counts = {
            key: int(value)
            for key, value in (state.get("counts") or {}).items()
            if key in {"characters", "items", "phases", "events", "relationships", "continuity"}
        }
        candidate_ids = [str(value) for value in state.get("candidate_ids", []) if value]

        state["status"] = "RUNNING"
        state.pop("failure", None)
        state.pop("failed_batch", None)
        self._save_state(state)

        if on_progress:
            on_progress(len(completed), len(ranges), (0, 0), counts, "init")

        uncompleted = [
            (b_idx, r_start, r_end)
            for b_idx, (r_start, r_end) in enumerate(ranges, start=1)
            if b_idx not in completed
        ]

        if uncompleted:
            if max_concurrency <= 1:
                for b_idx, r_start, r_end in uncompleted:
                    batch_files = files[r_start - 1 : r_end]
                    ch_s = self._number(batch_files[0])
                    ch_e = self._number(batch_files[-1])
                    ch_range = (ch_s, ch_e) if ch_s < 999999 else (r_start, r_end)
                    try:
                        batch_counts, candidate_id = self._extract_batch(
                            work_id,
                            title,
                            b_idx,
                            ch_range[0],
                            ch_range[1],
                            batch_files,
                            provider_override,
                            model_override,
                            candidate_binding,
                        )
                    except Exception as exc:
                        self._raise_recorded_failure(
                            state,
                            exc,
                            batch_number=b_idx,
                            phase="extract",
                        )
                    completed.add(b_idx)
                    state["completed"] = sorted(completed)
                    for key, value in batch_counts.items():
                        counts[key] = counts.get(key, 0) + value
                    if candidate_id and candidate_id not in candidate_ids:
                        candidate_ids.append(candidate_id)
                    all_cids = state.setdefault("all_candidate_ids", [])
                    if candidate_id and candidate_id not in all_cids:
                        all_cids.append(candidate_id)
                    state["counts"] = counts
                    state["candidate_ids"] = candidate_ids
                    self._save_state(state)
                    if on_progress:
                        on_progress(len(completed), len(ranges), ch_range, batch_counts, "batch_done")
            else:
                from concurrent.futures import as_completed
                completed_payloads: dict[int, dict[str, Any]] = {}
                next_reduce_batch = min(b[0] for b in uncompleted)

                with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
                    futures = {}
                    for b_idx, r_start, r_end in uncompleted:
                        batch_files = files[r_start - 1 : r_end]
                        ch_s = self._number(batch_files[0])
                        ch_e = self._number(batch_files[-1])
                        ch_range = (ch_s, ch_e) if ch_s < 999999 else (r_start, r_end)
                        futures[
                            executor.submit(
                                self._map_batch,
                                work_id,
                                title,
                                b_idx,
                                ch_range[0],
                                ch_range[1],
                                batch_files,
                                provider_override,
                                model_override,
                            )
                        ] = b_idx

                    for future in as_completed(futures):
                        b_idx = futures[future]
                        try:
                            payload = future.result()
                        except Exception as exc:
                            self._raise_recorded_failure(
                                state,
                                exc,
                                batch_number=b_idx,
                                phase="map",
                            )
                        b_idx = payload["batch_number"]
                        completed_payloads[b_idx] = payload

                        while next_reduce_batch in completed_payloads:
                            p = completed_payloads.pop(next_reduce_batch)
                            p_files = p["files"]
                            ch_s = self._number(p_files[0]) if p_files else p["start"]
                            ch_e = self._number(p_files[-1]) if p_files else p["end"]
                            ch_range = (ch_s, ch_e) if ch_s < 999999 else (p["start"], p["end"])
                            try:
                                batch_counts, candidate_id = self._reduce_batch(
                                    work_id,
                                    title,
                                    p,
                                    candidate_binding,
                                )
                            except Exception as exc:
                                self._raise_recorded_failure(
                                    state,
                                    exc,
                                    batch_number=next_reduce_batch,
                                    phase="reduce",
                                )
                            completed.add(next_reduce_batch)
                            state["completed"] = sorted(completed)
                            for key, value in batch_counts.items():
                                counts[key] = counts.get(key, 0) + value
                            if candidate_id and candidate_id not in candidate_ids:
                                candidate_ids.append(candidate_id)
                            all_cids = state.setdefault("all_candidate_ids", [])
                            if candidate_id and candidate_id not in all_cids:
                                all_cids.append(candidate_id)
                            state["counts"] = counts
                            state["candidate_ids"] = candidate_ids
                            self._save_state(state)
                            if on_progress:
                                on_progress(len(completed), len(ranges), ch_range, batch_counts, "batch_done")
                            next_reduce_batch += 1

        state["status"] = "COMPLETED"
        self._save_state(state)

        return BatchExtractionReport(
            work_id=work_id,
            title=title,
            total_batches=len(ranges),
            completed_batches=len(completed),
            characters=counts.get("characters", 0),
            items=counts.get("items", 0),
            events=counts.get("events", 0),
            relationships=counts.get("relationships", 0),
            continuity=counts.get("continuity", 0),
            status="EVALUATION_CANDIDATE",
            candidate_ids=tuple(candidate_ids),
            formal_knowledge_written=False,
            configuration_status=scope_status["status"],
            configuration_message=scope_status["message"],
            requires_review=True,
        )

    def ingest_candidates_to_formal(
        self,
        work_id: str,
        candidate_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """将指定作品的候选包规整入库为正式实体 Markdown、关系与 SQLite 连续性/因果图"""
        from fxi.sources.candidate_ingestor import CandidateIngestor
        ingestor = CandidateIngestor(self.config)
        # Preserve the extractor's candidate store and its approval verifier.
        # Rebuilding a default store here would discard an injected server
        # receipt boundary and make a valid promotion unverifiable.
        ingestor.candidate_store = self.candidate_store
        return ingestor.ingest_work_candidates(work_id=work_id, candidate_ids=candidate_ids)
