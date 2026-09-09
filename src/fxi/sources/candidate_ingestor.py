"""
src.fxi.sources.candidate_ingestor - 候选知识规整聚合与正式入库引擎

将分批抽取生成的候选包（CandidatePackages）按作品聚合、去重，
根据三位一体模型（Trinity Model）生成规整的 Markdown 实体卡与关系文件，
并将连续性台账与因果 DAG 写入 SQLite，实现候选到正式知识的规范落地。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import FxiError
from fxi.domain.canonical import CanonicalRegistry
from fxi.domain.entities import get_worldview_genre, load_work_config, select_work_section
from fxi.materials_skills.candidate_store import CandidateStore
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
from fxi.storage.text_io import write_markdown_frontmatter
from fxi.timeline.continuity import ContinuityManager
from fxi.timeline.dag import CausalDAG


class CandidatePackageError(ValueError):
    """候选包不满足正式入库所需的结构契约。"""

    code = "INCOMPLETE_INPUT"


class CandidateIngestor:
    """将 CandidateStore 中的批量抽取候选规整入库为正式实体、因果与连续性台账"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.candidate_store = CandidateStore(self.config)
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.canonical_reg = CanonicalRegistry(self.config)
        self.continuity_mgr = ContinuityManager(self.config)
        self.dag = CausalDAG(self.config)

    @staticmethod
    def _clean_slug(name: str, prefix: str = "char") -> str:
        safe = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("_")
        if not safe or len(safe) < 2:
            h = sha256_hex(name)[:8]
            safe = f"id_{h}"
        return f"{prefix}_{safe.lower()}"

    @staticmethod
    def _infer_ability_category(
        name: str,
        desc: str,
        category_rules: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> str:
        combined = f"{name} {desc}"
        for category, keywords in (category_rules or {}).items():
            if any(keyword and keyword in combined for keyword in keywords):
                return category
        return "innate"

    @staticmethod
    def _normalize_ability_category_rules(raw: Any) -> dict[str, list[str]]:
        if not isinstance(raw, Mapping):
            return {}
        normalized: dict[str, list[str]] = {}
        for category, values in raw.items():
            if not isinstance(category, str) or not category.strip():
                continue
            if isinstance(values, Mapping):
                values = values.get("keywords")
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                continue
            keywords = [
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            ]
            if keywords:
                normalized[category.strip()] = keywords
        return normalized

    def _load_ability_category_rules(
        self,
        work_id: str,
    ) -> tuple[dict[str, list[str]], Optional[str]]:
        settings, error_code = load_work_config(self.config, work_id)
        if settings is None:
            return {}, error_code or "WORK_CONFIG_UNAVAILABLE"
        genre = get_worldview_genre(settings)
        raw = select_work_section(settings, "ability_category_rules", genre)
        if raw is None:
            raw = select_work_section(settings, "ability_categories", genre)
        rules = self._normalize_ability_category_rules(raw)
        if not rules:
            return {}, "ABILITY_CATEGORY_RULES_MISSING"
        return rules, None

    @staticmethod
    def _infer_ability_sequence(name: str, desc: str) -> Optional[str]:
        combined = f"{name} {desc}"
        m = re.search(r"序列\s*[:：]?\s*(\d+)", combined)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _list_field(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise CandidatePackageError(f"候选包 {label} 必须是列表")
        result: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise CandidatePackageError(f"候选包 {label}[{index}] 必须是对象")
            result.append(dict(item))
        return result

    @classmethod
    def _section(
        cls,
        package: Mapping[str, Any],
        section_name: str,
        item_name: str,
        *,
        legacy_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        value = package.get(section_name)
        if value is None and legacy_name:
            value = package.get(legacy_name)
        if isinstance(value, Mapping):
            value = value.get(item_name)
        return cls._list_field(value, f"{section_name}.{item_name}")

    @staticmethod
    def _require_explicit_identifier(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise CandidatePackageError(f"候选包 {field} 必须由来源证据显式提供")
        return value.strip()

    @classmethod
    def _validate_explicit_projection_fields(
        cls,
        characters: Sequence[Mapping[str, Any]],
        items: Sequence[Mapping[str, Any]],
        causal_events: Sequence[Mapping[str, Any]],
        relationships: Sequence[Mapping[str, Any]],
    ) -> None:
        """拒绝投影阶段补造身份、能力、场景或因果等未经证据支持的事实。"""

        character_names: set[str] = set()
        for index, character in enumerate(characters):
            name = cls._require_explicit_identifier(character.get("name"), f"entities.characters[{index}].name")
            cls._require_explicit_identifier(character.get("entity_id"), f"entities.characters[{index}].entity_id")
            character_names.add(name)
            phases = character.get("phases", [])
            if not isinstance(phases, list):
                raise CandidatePackageError(f"候选包 entities.characters[{index}].phases 必须是列表")
            for phase_index, phase in enumerate(phases):
                if not isinstance(phase, Mapping):
                    raise CandidatePackageError(f"候选包 entities.characters[{index}].phases[{phase_index}] 必须是对象")
                cls._require_explicit_identifier(
                    phase.get("phase_id"), f"entities.characters[{index}].phases[{phase_index}].phase_id"
                )
                cls._require_explicit_identifier(
                    phase.get("phase_name"), f"entities.characters[{index}].phases[{phase_index}].phase_name"
                )
            abilities = character.get("abilities", [])
            if not isinstance(abilities, list):
                raise CandidatePackageError(f"候选包 entities.characters[{index}].abilities 必须是列表")
            for ability_index, ability in enumerate(abilities):
                if not isinstance(ability, Mapping):
                    raise CandidatePackageError(f"候选包 entities.characters[{index}].abilities[{ability_index}] 必须是对象")
                prefix = f"entities.characters[{index}].abilities[{ability_index}]"
                cls._require_explicit_identifier(ability.get("ability_id") or ability.get("skill_id"), f"{prefix}.ability_id")
                cls._require_explicit_identifier(ability.get("name"), f"{prefix}.name")
                cls._require_explicit_identifier(ability.get("category") or ability.get("ability_category"), f"{prefix}.category")

        for index, item in enumerate(items):
            cls._require_explicit_identifier(item.get("entity_id"), f"entities.items[{index}].entity_id")
            cls._require_explicit_identifier(item.get("name"), f"entities.items[{index}].name")

        for index, event in enumerate(causal_events):
            cls._require_explicit_identifier(event.get("event_id"), f"causal_events[{index}].event_id")
            cls._require_explicit_identifier(event.get("scene_uuid"), f"causal_events[{index}].scene_uuid")
            if not isinstance(event.get("narrative_order"), int):
                raise CandidatePackageError(f"候选包 causal_events[{index}].narrative_order 必须显式为整数")

        for index, relation in enumerate(relationships):
            pair = relation.get("pair")
            if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(name, str) or not name.strip() for name in pair):
                raise CandidatePackageError(f"候选包 relationships[{index}].pair 必须是两个显式角色名")
            if any(name.strip() not in character_names for name in pair):
                raise CandidatePackageError(f"候选包 relationships[{index}] 引用了未显式抽取的角色")

    @classmethod
    def _normalize_package(cls, package: Any, work_id: str) -> dict[str, Any]:
        if not isinstance(package, Mapping):
            raise CandidatePackageError("候选 package 必须是对象")
        if package.get("work_id") != work_id:
            raise CandidatePackageError("候选包 work_id 与入库作品不匹配")

        entities = package.get("entities")
        if isinstance(entities, Mapping):
            characters = cls._list_field(entities.get("characters"), "entities.characters")
            items = cls._list_field(entities.get("items"), "entities.items")
        elif entities is None and ("characters" in package or "items" in package):
            # 兼容早期 UniversalAutoExtractor 产生的扁平候选包。
            characters = cls._list_field(package.get("characters", []), "entities.characters")
            items = cls._list_field(package.get("items", []), "entities.items")
        else:
            raise CandidatePackageError("候选包缺少 entities 对象")

        causal_events = cls._section(
            package, "causal_events", "causal_events", legacy_name="causal_events"
        )
        voice_profiles = cls._section(
            package, "voice_profiles", "voice_profiles", legacy_name="voices"
        )
        relationships = cls._section(package, "relationships", "relationships")
        continuity = cls._section(package, "continuity", "chapters")
        cls._validate_explicit_projection_fields(characters, items, causal_events, relationships)

        normalized_voices: list[dict[str, Any]] = []
        for voice in voice_profiles:
            profile = voice.get("voice_profile")
            if isinstance(profile, Mapping):
                normalized_voice = {"name": voice.get("name", ""), **dict(profile)}
                if voice.get("entity_id"):
                    normalized_voice["entity_id"] = voice["entity_id"]
                normalized_voices.append(normalized_voice)
            else:
                normalized_voices.append(voice)

        normalized_continuity: list[dict[str, Any]] = []
        for chapter in continuity:
            normalized_chapter = dict(chapter)
            if "unresolved_hooks" not in normalized_chapter and "hooks" in normalized_chapter:
                normalized_chapter["unresolved_hooks"] = normalized_chapter.pop("hooks")
            normalized_continuity.append(normalized_chapter)

        return {
            **dict(package),
            "entities": {"characters": characters, "items": items},
            "causal_events": {"causal_events": causal_events},
            "voice_profiles": {"voice_profiles": normalized_voices},
            "relationships": {"relationships": relationships},
            "continuity": {"chapters": normalized_continuity},
        }

    @staticmethod
    def _report(work_id: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "work_id": work_id,
            "status": status,
            "message": message,
            "formal_knowledge_written": False,
            "characters": 0,
            "items": 0,
            "phases": 0,
            "relationships": 0,
            "continuity": 0,
            "causal_events": 0,
            **extra,
        }

    @staticmethod
    def _candidate_ids(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise CandidatePackageError("candidate_ids 必须是字符串列表")
        values = list(value)
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise CandidatePackageError("candidate_ids 必须只包含非空字符串")
        return list(dict.fromkeys(values))

    def _formal_projection_is_empty(self, work_id: str) -> bool:
        """只允许向空投影首次发布，禁止候选流程重建或覆盖既有正式知识。"""

        entities_dir = self.config.projects_dir / work_id / "entities"
        if entities_dir.exists() and any(entities_dir.iterdir()):
            return False
        try:
            with self.db_client.transaction() as cur:
                counts = [
                    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE work_id = ?", (work_id,)).fetchone()[0]
                    for table in (
                        "entities",
                        "entity_phases",
                        "entity_abilities",
                        "entity_relations",
                        "chapter_continuity",
                        "causal_events",
                        "causal_links",
                    )
                ]
        except (FxiError, OSError) as exc:
            raise CandidatePackageError(f"无法确认正式投影是否为空: {type(exc).__name__}") from exc
        return not any(counts)

    def _workspace_relative_file_path(self, file_path: Path) -> str:
        """Return a configured projection path in the workspace-relative DB form."""

        try:
            workspace_root = self.config.workspace_root.resolve(strict=True)
            resolved_file = Path(file_path).resolve(strict=False)
            relative = resolved_file.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CandidatePackageError(
                f"正式知识文件路径必须位于 workspace_root 内: {file_path}"
            ) from exc
        return relative.as_posix()

    def ingest_work_candidates(
        self,
        work_id: str,
        candidate_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        """仅聚合已审批候选并写入正式库；待审候选只返回结构化状态。"""
        try:
            candidate_ids_list = self._candidate_ids(candidate_ids)
        except CandidatePackageError as exc:
            return self._report(work_id, "FAILED", str(exc), error_code=exc.code)
        if not candidate_ids_list:
            state_path = self.config.data_dir / f"{work_id}.luna-extraction.json"
            if state_path.is_file():
                try:
                    state_data = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, TypeError, ValueError) as exc:
                    return self._report(
                        work_id,
                        "FAILED",
                        f"候选进度文件无法解析: {type(exc).__name__}",
                        error_code="CANDIDATE_STATE_INVALID",
                    )
                if not isinstance(state_data, Mapping):
                    return self._report(
                        work_id,
                        "FAILED",
                        "候选进度文件必须是对象",
                        error_code="CANDIDATE_STATE_INVALID",
                    )
                try:
                    candidate_ids_list = self._candidate_ids(
                        state_data.get("all_candidate_ids") or state_data.get("candidate_ids", [])
                    )
                except CandidatePackageError as exc:
                    return self._report(work_id, "FAILED", str(exc), error_code=exc.code)

        # 扫描 CandidateStore 中该作品的所有历史候选包（自动补全多切片）
        candidate_dir = self.config.materials_dir / "candidates"
        if candidate_dir.is_dir():
            for cfile in sorted(candidate_dir.glob("candidate-*.json")):
                cid = cfile.stem
                if cid not in candidate_ids_list:
                    try:
                        rec = self.candidate_store.get(cid)
                    except (FxiError, OSError, UnicodeError, TypeError, ValueError) as exc:
                        return self._report(
                            work_id,
                            "FAILED",
                            f"候选文件 {cfile.name} 无法读取: {type(exc).__name__}",
                            error_code="CANDIDATE_RECORD_INVALID",
                            candidate_id=cid,
                        )
                    if not isinstance(rec.package, Mapping):
                        return self._report(
                            work_id,
                            "FAILED",
                            f"候选文件 {cfile.name} 的 package 必须是对象",
                            error_code="INCOMPLETE_INPUT",
                            candidate_id=cid,
                        )
                    if rec.package.get("work_id") == work_id:
                        candidate_ids_list.append(cid)

        if not candidate_ids_list:
            return self._report(work_id, "EMPTY", "未找到待入库的候选包")

        records = []
        for candidate_id in candidate_ids_list:
            try:
                record = self.candidate_store.get(candidate_id)
            except (FxiError, OSError, UnicodeError, TypeError, ValueError) as exc:
                return self._report(
                    work_id,
                    "FAILED",
                    f"候选 {candidate_id} 无法读取: {type(exc).__name__}",
                    error_code="CANDIDATE_RECORD_INVALID",
                    candidate_id=candidate_id,
                )
            if not isinstance(record.package, Mapping):
                return self._report(
                    work_id,
                    "FAILED",
                    f"候选 {candidate_id} 的 package 必须是对象",
                    error_code="INCOMPLETE_INPUT",
                    candidate_id=candidate_id,
                )
            # 作品范围以候选记录的受控绑定为准；package 内可选声明仅用于冲突校验。
            record_work_id = getattr(record, "work_id", None)
            package_work_id = record.package.get("work_id")
            if record_work_id != work_id or (
                package_work_id is not None and package_work_id != work_id
            ):
                return self._report(
                    work_id,
                    "FAILED",
                    f"候选 {candidate_id} 的作品绑定不匹配",
                    error_code="CANDIDATE_SCOPE_MISMATCH",
                    candidate_id=candidate_id,
                )
            records.append(record)

        pending = [
            getattr(record, "candidate_id", "")
            for record in records
            if not self.candidate_store.is_trusted_approved(record)
        ]
        if pending:
            return self._report(
                work_id,
                "REVIEW_REQUIRED",
                "候选尚未获得人工审批，未写入正式知识",
                error_code="CANDIDATE_APPROVAL_REQUIRED",
                candidate_ids=pending,
                requires_approval=True,
            )

        normalized_records = []
        for record in records:
            try:
                normalized_records.append(
                    (record, self._normalize_package(record.package, work_id))
                )
            except CandidatePackageError as exc:
                return self._report(
                    work_id,
                    "FAILED",
                    str(exc),
                    error_code=exc.code,
                    candidate_id=getattr(record, "candidate_id", None),
                )

        if not self._formal_projection_is_empty(work_id):
            return self._report(
                work_id,
                "FAILED",
                "既有正式投影只能经版本化提交服务更新；候选入库拒绝重建或覆盖。",
                error_code="FORMAL_PROJECTION_NOT_EMPTY",
            )

        ability_category_rules, ability_rules_error = self._load_ability_category_rules(work_id)
        ingest_diagnostics: list[dict[str, Any]] = []
        if ability_rules_error:
            ingest_diagnostics.append(
                {
                    "code": "ABILITY_CATEGORY_RULES_UNAVAILABLE",
                    "message": "作品未提供可用的能力分类规则；未声明分类的能力保持 innate",
                    "details": {"reason": ability_rules_error},
                }
            )

        characters_by_id: dict[str, dict[str, Any]] = {}
        items_by_id: dict[str, dict[str, Any]] = {}
        voices_by_name: dict[str, dict[str, Any]] = {}
        relationships_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        continuity_by_index: dict[int, dict[str, Any]] = {}
        causal_events: list[dict[str, Any]] = []

        # 1. 遍历收集并去重候选
        for record, pkg in normalized_records:

            # 1.1 收集连续性
            cont_data = pkg.get("continuity", {})
            for ch in cont_data.get("chapters", []):
                idx = int(ch.get("chapter_index", 0))
                if idx > 0:
                    continuity_by_index[idx] = ch

            # 1.2 收集因果事件
            causal_data = pkg.get("causal_events", {})
            for ev in causal_data.get("causal_events", []):
                causal_events.append(ev)

            # 1.3 收集并合并声线档案
            voice_data = pkg.get("voice_profiles", {})
            for vp in voice_data.get("voice_profiles", []):
                v_name = vp.get("name", "").strip()
                if not v_name:
                    continue
                if v_name not in voices_by_name:
                    voices_by_name[v_name] = {
                        "name": v_name,
                        "tone": vp.get("tone", ""),
                        "speech_style": vp.get("speech_style", ""),
                        "catchphrases": list(vp.get("catchphrases", [])),
                        "gestures": list(vp.get("gestures", [])),
                        "taboos": list(vp.get("taboos", [])),
                        "dialogue_samples": list(vp.get("dialogue_samples", [])),
                    }
                else:
                    existing_vp = voices_by_name[v_name]
                    if not existing_vp.get("tone") and vp.get("tone"):
                        existing_vp["tone"] = vp["tone"]
                    if not existing_vp.get("speech_style") and vp.get("speech_style"):
                        existing_vp["speech_style"] = vp["speech_style"]
                    for cp in vp.get("catchphrases", []):
                        if cp and cp not in existing_vp["catchphrases"]:
                            existing_vp["catchphrases"].append(cp)
                    for g in vp.get("gestures", []):
                        if g and g not in existing_vp["gestures"]:
                            existing_vp["gestures"].append(g)
                    for tb in vp.get("taboos", []):
                        if tb and tb not in existing_vp["taboos"]:
                            existing_vp["taboos"].append(tb)
                    existing_ctxs = {s.get("context") for s in existing_vp["dialogue_samples"]}
                    for s in vp.get("dialogue_samples", []):
                        if s.get("context") not in existing_ctxs:
                            existing_vp["dialogue_samples"].append(s)
                            existing_ctxs.add(s.get("context"))

            # 1.4 收集人物关系
            rel_data = pkg.get("relationships", {})
            for rel in rel_data.get("relationships", []):
                pair = rel.get("pair", [])
                if len(pair) == 2 and pair[0] and pair[1]:
                    key = tuple(sorted([pair[0].strip(), pair[1].strip()]))
                    relationships_by_pair[key] = rel

            # 1.5 收集角色与道具
            ent_data = pkg.get("entities", {})
            for char in ent_data.get("characters", []):
                cname = char.get("name", "").strip()
                if not cname:
                    continue
                cid = char["entity_id"]
                canonical_id = self.canonical_reg.get_canonical_id(
                    work_id, cname, category="character", default_id=cid
                )
                cid = canonical_id or cid

                ch_range = pkg.get("chapter_range", [])
                start_ch = int(ch_range[0]) if (ch_range and len(ch_range) > 0) else 1
                batch_num = pkg.get("batch_number")

                if cid not in characters_by_id:
                    characters_by_id[cid] = {
                        "entity_id": cid,
                        "name": cname,
                        "category": "character",
                        "is_unique": True,
                        "aliases": set(char.get("aliases", [])),
                        "attributes": dict(char.get("attributes", {})),
                        "description": char.get("description", ""),
                        "phases": [],
                        "abilities": {},
                    }
                else:
                    characters_by_id[cid]["aliases"].update(char.get("aliases", []))
                    if not characters_by_id[cid]["description"] and char.get("description"):
                        characters_by_id[cid]["description"] = char["description"]

                # 合并 phases
                existing_pids = {p["phase_id"] for p in characters_by_id[cid]["phases"]}
                for ph in char.get("phases", []):
                    pid = ph["phase_id"]
                    if pid not in existing_pids:
                        characters_by_id[cid]["phases"].append({
                            "phase_id": pid,
                            "phase_name": ph["phase_name"],
                            "valid_from_order": ph.get("valid_from", 0),
                            "valid_to_order": ph.get("valid_to"),
                            "traits": ph.get("traits", []),
                            "anti_behaviors": ph.get("anti_behaviors", []),
                        })
                        existing_pids.add(pid)

                # 合并 abilities
                char_abilities_map = characters_by_id[cid].setdefault("abilities", {})
                for a in char.get("abilities", []):
                    raw_name = a.get("name", "").strip()
                    clean_name = raw_name.strip("【】").strip()
                    if not clean_name:
                        continue
                    desc = a.get("description", "").strip()
                    tier = a.get("tier")
                    skill_id = a.get("ability_id") or a["skill_id"]
                    explicit_category = a.get("category") or a["ability_category"]
                    if clean_name not in char_abilities_map:
                        char_abilities_map[clean_name] = {
                            "ability_id": skill_id,
                            "name": clean_name,
                            "category": explicit_category,
                            "sequence_num": self._infer_ability_sequence(clean_name, desc),
                            "valid_from_chapter": start_ch,
                            "valid_to_chapter": None,
                            "cost_description": "",
                            "description": desc,
                            "source_origin": "",
                            "metadata": {"tier": tier, "batches": [batch_num]} if tier is not None else {"batches": [batch_num]},
                        }
                    else:
                        entry = char_abilities_map[clean_name]
                        if start_ch < entry["valid_from_chapter"]:
                            entry["valid_from_chapter"] = start_ch
                        if len(desc) > len(entry["description"]):
                            entry["description"] = desc
                        if not entry["sequence_num"]:
                            entry["sequence_num"] = self._infer_ability_sequence(clean_name, desc)
                        if batch_num and batch_num not in entry["metadata"].get("batches", []):
                            entry["metadata"].setdefault("batches", []).append(batch_num)

            for item in ent_data.get("items", []):
                iname = item.get("name", "").strip()
                if not iname:
                    continue
                iid = item["entity_id"]
                canonical_id = self.canonical_reg.get_canonical_id(
                    work_id, iname, category="item", default_id=iid
                )
                iid = canonical_id or iid
                item_attrs = dict(item.get("attributes", {}))

                if iid not in items_by_id:
                    items_by_id[iid] = {
                        "entity_id": iid,
                        "name": iname,
                        "category": "item",
                        "is_unique": True,
                        "current_owner": item.get("current_owner", ""),
                        "source_origin": item.get("source_origin", ""),
                        "transfer_type": item.get("transfer_type", "gifted"),
                        "attributes": item_attrs,
                        "description": item.get("description", ""),
                    }
                else:
                    existing_item = items_by_id[iid]
                    cur_attrs = existing_item["attributes"]
                    if not cur_attrs.get("symbolic_text") and item_attrs.get("symbolic_text"):
                        cur_attrs["symbolic_text"] = item_attrs["symbolic_text"]
                    if not cur_attrs.get("interaction_rituals") and item_attrs.get("interaction_rituals"):
                        cur_attrs["interaction_rituals"] = item_attrs["interaction_rituals"]
                    elif item_attrs.get("interaction_rituals"):
                        for r in item_attrs["interaction_rituals"]:
                            if r not in cur_attrs["interaction_rituals"]:
                                cur_attrs["interaction_rituals"].append(r)

        # 2. 写入物理实体 Markdown 文件
        entities_dir = self.config.projects_dir / work_id / "entities"
        chars_dir = entities_dir / "characters"
        items_dir = entities_dir / "items"
        chars_dir.mkdir(parents=True, exist_ok=True)
        items_dir.mkdir(parents=True, exist_ok=True)

        total_phases = 0
        total_abilities = 0
        for cid, cdata in characters_by_id.items():
            cname = cdata["name"]
            vp = dict(voices_by_name.get(cname, {}))

            attrs = dict(cdata["attributes"])
            if vp:
                attrs["voice_profile"] = vp

            abilities_dict = cdata.get("abilities", {})
            if isinstance(abilities_dict, dict):
                abilities_list = list(abilities_dict.values())
            else:
                abilities_list = list(abilities_dict)
            sorted_abilities = sorted(
                abilities_list,
                key=lambda x: (int(x.get("valid_from_chapter", 1)), str(x.get("name", ""))),
            )
            total_abilities += len(sorted_abilities)
            if sorted_abilities:
                attrs["abilities"] = [dict(a) for a in sorted_abilities]

            phases = cdata["phases"]
            total_phases += len(phases)

            card_meta = {
                "entity_id": cid,
                "category": "character",
                "is_unique": True,
                "name": cname,
                "aliases": sorted(list(cdata["aliases"])),
                "attributes": attrs,
                "phases": phases,
            }
            if sorted_abilities:
                card_meta["abilities"] = sorted_abilities
            if vp:
                card_meta["voice_profile"] = vp
            write_markdown_frontmatter(chars_dir / f"{cid}.md", card_meta, cdata["description"])

        for iid, idata in items_by_id.items():
            card_meta = {
                "entity_id": iid,
                "category": "item",
                "is_unique": True,
                "name": idata["name"],
                "current_owner": idata["current_owner"],
                "source_origin": idata["source_origin"],
                "transfer_type": idata["transfer_type"],
                "attributes": idata["attributes"],
            }
            write_markdown_frontmatter(items_dir / f"{iid}.md", card_meta, idata["description"])

        # 3. 写入 relationships.yaml
        rel_list = list(relationships_by_pair.values())
        rel_path = entities_dir / "relationships.yaml"
        rel_path.write_text(
            yaml.safe_dump({"relationships": rel_list}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        # 4. 写入 SQLite chapter_continuity
        for ch_idx, ch in sorted(continuity_by_index.items()):
            self.continuity_mgr.record_chapter(
                work_id=work_id,
                chapter_index=ch_idx,
                title=ch.get("title", f"第{ch_idx}章"),
                tail_snippet="",
                ending_location=ch.get("ending_location", "未知"),
                active_characters=ch.get("active_characters", []),
                ending_situation=ch.get("ending_situation", ""),
                unresolved_hooks=ch.get("unresolved_hooks", []),
            )

        # 5. 写入 SQLite causal DAG
        # 按 order 升序排序
        sorted_events = sorted(
            causal_events,
            key=lambda x: int(x.get("narrative_order", 0) or x.get("order", 0)),
        )
        registered_event_count = 0
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)

        for i, ev in enumerate(sorted_events, start=1):
            order = int(ev.get("narrative_order", 0) or ev.get("order", 0)) or i
            eid = ev["event_id"]
            self.dag.register_event(
                event_id=eid,
                work_id=work_id,
                scene_uuid=ev["scene_uuid"],
                narrative_order=order,
                physical_time=ev.get("physical_time", ""),
                summary=ev.get("summary", ""),
            )
            registered_event_count += 1

        # 6. 同步写入 SQLite entities / entity_phases / entity_relations
        self._sync_entities_to_sqlite(work_id, characters_by_id, items_by_id, rel_list)

        return {
            "work_id": work_id,
            "status": "SUCCESS",
            "formal_knowledge_written": True,
            "requires_review": False,
            "characters": len(characters_by_id),
            "items": len(items_by_id),
            "phases": total_phases,
            "abilities": total_abilities,
            "relationships": len(rel_list),
            "continuity": len(continuity_by_index),
            "causal_events": registered_event_count,
            "diagnostics": ingest_diagnostics,
        }

    def _sync_entities_to_sqlite(
        self,
        work_id: str,
        characters: dict[str, dict[str, Any]],
        items: dict[str, dict[str, Any]],
        relationships: list[dict[str, Any]],
    ) -> None:
        """在空投影中单事务写入实体投影；绝不删除既有正式记录。"""
        entities_dir = self.config.projects_dir / work_id / "entities"
        chars_dir = entities_dir / "characters"
        items_dir = entities_dir / "items"
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)

            for cid, cdata in characters.items():
                rel_file = self._workspace_relative_file_path(chars_dir / f"{cid}.md")
                cur.execute(
                    """
                    INSERT INTO entities
                    (entity_id, work_id, category, is_unique, name, aliases_json, attributes_yaml, file_path, updated_at)
                    VALUES (?, ?, 'character', 1, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        cid,
                        work_id,
                        cdata["name"],
                        json.dumps(sorted(list(cdata["aliases"])), ensure_ascii=False),
                        yaml.dump(cdata["attributes"], allow_unicode=True),
                        rel_file,
                    ),
                )
                for ph in cdata["phases"]:
                    cur.execute(
                        """
                        INSERT INTO entity_phases
                        (phase_id, work_id, entity_id, phase_name, valid_from_order, valid_to_order, traits_json, anti_behaviors_json, tone_examples_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', datetime('now'))
                        """,
                        (
                            ph["phase_id"],
                            work_id,
                            cid,
                            ph["phase_name"],
                            ph.get("valid_from_order", 0),
                            ph.get("valid_to_order"),
                            json.dumps(ph.get("traits", []), ensure_ascii=False),
                            json.dumps(ph.get("anti_behaviors", []), ensure_ascii=False),
                        ),
                    )

                abilities_val = cdata.get("abilities", {})
                ab_list = list(abilities_val.values()) if isinstance(abilities_val, dict) else list(abilities_val)
                seen_aids = set()
                for ab in ab_list:
                    aid = ab["ability_id"]
                    if aid in seen_aids:
                        aid = f"{aid}_{len(seen_aids)}"
                    seen_aids.add(aid)
                    cur.execute(
                        """
                        INSERT INTO entity_abilities
                        (ability_id, work_id, entity_id, ability_name, category, sequence_num, valid_from_chapter, valid_to_chapter, cost_description, effect_description, source_origin, metadata_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                        """,
                        (
                            aid,
                            work_id,
                            cid,
                            ab["name"],
                            ab.get("category", "innate"),
                            ab.get("sequence_num"),
                            int(ab.get("valid_from_chapter", 1)),
                            ab.get("valid_to_chapter"),
                            ab.get("cost_description", ""),
                            ab.get("description", ""),
                            ab.get("source_origin", ""),
                            json.dumps(ab.get("metadata", {}), ensure_ascii=False),
                        ),
                    )

            for iid, idata in items.items():
                rel_file = self._workspace_relative_file_path(items_dir / f"{iid}.md")
                cur.execute(
                    """
                    INSERT INTO entities
                    (entity_id, work_id, category, is_unique, name, aliases_json, attributes_yaml, file_path, updated_at)
                    VALUES (?, ?, 'item', 1, ?, '[]', ?, ?, datetime('now'))
                    """,
                    (
                        iid,
                        work_id,
                        idata["name"],
                        yaml.dump(idata["attributes"], allow_unicode=True),
                        rel_file,
                    ),
                )

        if relationships:
            from fxi.domain.relations import RelationManager
            RelationManager(self.config).batch_sync_to_sqlite(work_id, relationships)
