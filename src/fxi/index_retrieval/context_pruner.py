"""
fxi.index_retrieval.context_pruner - 场景化上下文剪枝器 (锁定 2500~4000 Tokens 黄金区间)
"""

from typing import Any, Mapping, Optional

from fxi.character_knowledge.knowledge_tracker import KnowledgeTracker
from fxi.character_knowledge.pov_filter import POVFilter
from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.core.types import SceneType
from fxi.domain.entities import EntityManager
from fxi.domain.phases import PhaseManager
from fxi.domain.relations import RelationManager
from fxi.game_engine.passive_radar import PassiveThreatRadar
from fxi.game_engine.projection import TieredParameterProjector
from fxi.game_engine.scene_radar import SceneFocusRadar
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.domain.mutation_ledger import MutationLedger
from fxi.materials_skills.anti_patterns import AntiPatternRepository
from fxi.materials_skills.candidate_store import package_hash as calculate_package_hash
from fxi.materials_skills.style_canonical import CanonicalRuleMapper
from fxi.materials_skills.skill_store import SkillStore
from fxi.storage.sqlite_client import DatabaseClient
from fxi.timeline.pod_filter import PODFilter
from fxi.territory.macro_tags import MacroTagGenerator


class ContextPruner:
    """场景化上下文组装与剪枝引擎"""

    _STYLE_SELECTIONS = frozenset({"none", "approved", "evaluation_candidate"})


    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.knowledge_tracker = KnowledgeTracker(self.config)
        self.pod_filter = PODFilter()
        self.mutation_ledger = MutationLedger(self.config, self.db_client)
        self.entity_mgr = EntityManager(self.config)
        self.relation_mgr = RelationManager(self.config)
        self.phase_mgr = PhaseManager(self.config)
        self.projector = TieredParameterProjector(self.config)
        self.radar = PassiveThreatRadar(self.config)
        self.scene_radar = SceneFocusRadar(self.config)
        self.macro_tags = MacroTagGenerator(self.config)
        self.fts = ChineseFTS(self.config)
        self.skills = SkillStore(self.config)
        self.anti_patterns = AntiPatternRepository(self.config)

    @staticmethod
    def _stable_hash(value: Any) -> str:
        return sha256_hex(value)

    @staticmethod
    def _object_items(items: Any, field: str) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        return [dict(item) if isinstance(item, Mapping) else {field: str(item)} for item in items]

    @staticmethod
    def _style_asset_id(item: Mapping[str, Any], field: str, index: int) -> str:
        for key in ("asset_id", "id", "method_id", "canonical_key", field, "rule", "example"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return f"{field}:{index}"

    @staticmethod
    def _style_item_text(item: Mapping[str, Any], preferred: str) -> str:
        for key in (
            preferred,
            "instruction",
            "operation",
            "method",
            "text",
            "value",
            "example",
        ):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _condition_value_matches(actual: Any, expected: Any) -> bool:
        if isinstance(expected, (list, tuple, set, frozenset)):
            return any(ContextPruner._condition_value_matches(actual, item) for item in expected)
        if isinstance(expected, Mapping) or actual is None:
            return False
        return str(actual).strip().lower() == str(expected).strip().lower()

    @classmethod
    def _qualify_style_item(
        cls,
        item: Mapping[str, Any],
        *,
        work_id: str,
        scene_data: Mapping[str, Any],
        mode: str,
        purpose: str,
        pov_character_id: str,
    ) -> tuple[bool, str]:
        """Apply only explicit, known conditions; never turn an unknown one into ALL."""

        context = {
            "work_id": work_id,
            "scene_type": scene_data.get("scene_type", "dialogue"),
            "scene_scope": scene_data.get("scene_type", "dialogue"),
            "worldview_genre": scene_data.get("worldview_genre"),
            "genre": scene_data.get("genre", scene_data.get("worldview_genre")),
            "mode": mode,
            "purpose": purpose,
            "pov_character_id": pov_character_id,
        }
        aliases = {"scene": "scene_type", "world": "worldview_genre"}

        def check_conditions(raw: Any, label: str) -> tuple[bool, str]:
            if raw is None:
                return True, ""
            if not isinstance(raw, Mapping):
                return False, f"{label} must be an explicit mapping"
            for raw_key, expected in raw.items():
                key = aliases.get(str(raw_key).strip().lower(), str(raw_key).strip().lower())
                if key not in context:
                    return False, f"unsupported {label} key: {raw_key}"
                actual = context[key]
                if key in {"scene_type", "scene_scope"}:
                    try:
                        expected_scope = CanonicalRuleMapper.normalize_scene_scope(str(expected))
                    except ValueError:
                        return False, f"unsupported condition scene scope: {expected}"
                    if expected_scope == "ALL":
                        continue
                    try:
                        actual_scope = CanonicalRuleMapper.normalize_scene_scope(str(actual))
                    except ValueError:
                        return False, f"unknown current scene scope: {actual}"
                    if expected_scope != actual_scope:
                        return False, f"{label} scene scope mismatch"
                    continue
                if actual is None:
                    return False, f"missing context for {label} key: {raw_key}"
                if not cls._condition_value_matches(actual, expected):
                    return False, f"{label} mismatch for {raw_key}"
            return True, ""

        raw_scope = item.get("scene_scope", item.get("scene_type"))
        matches, reason = check_conditions(
            {"scene_scope": raw_scope} if raw_scope is not None else None,
            "scene condition",
        )
        if not matches:
            return False, reason
        matches, reason = check_conditions(
            item.get("conditions", item.get("condition")),
            "condition",
        )
        if not matches:
            return False, reason
        for exclusion_key in ("anti_conditions", "exclusions", "excludes"):
            exclusions = item.get(exclusion_key)
            if exclusions is None:
                continue
            if not isinstance(exclusions, Mapping):
                return False, f"{exclusion_key} must be an explicit mapping"
            for key, expected in exclusions.items():
                normalized_key = aliases.get(str(key).strip().lower(), str(key).strip().lower())
                if normalized_key not in context:
                    return False, f"unsupported {exclusion_key} key: {key}"
                actual = context[normalized_key]
                if normalized_key in {"scene_type", "scene_scope"}:
                    try:
                        expected_scope = CanonicalRuleMapper.normalize_scene_scope(str(expected))
                        actual_scope = CanonicalRuleMapper.normalize_scene_scope(str(actual))
                    except ValueError:
                        return False, f"unsupported exclusion scene scope: {expected}"
                    excluded = expected_scope != "ALL" and expected_scope == actual_scope
                else:
                    if actual is None:
                        return False, f"missing context for {exclusion_key} key: {key}"
                    excluded = cls._condition_value_matches(actual, expected)
                if excluded:
                    return False, f"excluded by {exclusion_key}: {key}"
        return True, "explicit conditions qualified"

    @classmethod
    def _select_style_items(
        cls,
        items: list[dict[str, Any]],
        *,
        asset_kind: str,
        work_id: str,
        scene_data: Mapping[str, Any],
        mode: str,
        purpose: str,
        pov_character_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        selected_decisions: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            asset_id = cls._style_asset_id(item, asset_kind, index)
            qualified, reason = cls._qualify_style_item(
                item,
                work_id=work_id,
                scene_data=scene_data,
                mode=mode,
                purpose=purpose,
                pov_character_id=pov_character_id,
            )
            decision = {
                "asset_kind": asset_kind,
                "asset_id": asset_id,
                "selected": qualified,
                "reason": reason,
                "asset": item,
            }
            if qualified:
                selected.append(item)
                selected_decisions.append(decision)
            else:
                rejected.append(decision)
        return selected, selected_decisions, rejected

    def _load_claims(
        self,
        work_id: str,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """读取当前作品的正式 claims；不从其他作品或实时正文猜测事实。"""
        sql = """
        SELECT cv.claim_id, cv.statement, cv.status, cv.version,
               cv.work_id AS claim_work_id, cf.work_id AS family_work_id, cf.scope
        FROM claim_versions AS cv
        LEFT JOIN claim_families AS cf ON cf.family_key = cv.family_key
        WHERE cv.work_id = ?
          AND (cf.work_id = ? OR cf.work_id IS NULL)
          AND cv.status = 'accepted'
        ORDER BY cv.version DESC, cv.id DESC
        """
        claims: list[dict[str, Any]] = []
        with self.db_client.get_connection() as conn:
            for row in conn.execute(sql, (work_id, work_id)).fetchall():
                scope = str(row["scope"] or "").strip().lower()
                claim_id = row["claim_id"]
                evidence_sql = """
                    SELECT ce.source_id, ws.source_version, ce.scene_uuid,
                           ce.line_start, ce.line_end, ce.quote
                    FROM claim_evidence AS ce
                    LEFT JOIN work_sources AS ws
                        ON ws.source_id = ce.source_id AND ws.work_id = ?
                    WHERE ce.claim_id = ?
                """
                evidence_params: list[Any] = [work_id, claim_id]
                if source_id:
                    evidence_sql += " AND ce.source_id = ?"
                    evidence_params.append(source_id)
                if source_version:
                    evidence_sql += " AND ws.source_version = ?"
                    evidence_params.append(source_version)
                evidence_sql += " ORDER BY ce.id"
                evidence_rows = conn.execute(
                    evidence_sql,
                    evidence_params,
                ).fetchall()
                claims.append({
                    "claim_id": claim_id,
                    "statement": row["statement"],
                    "status": row["status"],
                    "version": row["version"],
                    "work_id": row["claim_work_id"],
                    "family_work_id": row["family_work_id"],
                    "scope": scope,
                    "is_secret": scope not in {"", "all", "public", "global", "canon", "world"},
                    "evidence": [dict(evidence) for evidence in evidence_rows],
                })
        return claims

    @staticmethod
    def _claim_matches_scope(
        claim: Mapping[str, Any],
        *,
        work_id: str,
        source_id: Optional[str],
        source_version: Optional[str],
        knowledge_version: Optional[str],
        narrative_order: int,
    ) -> bool:
        """只接受显式归属当前作品、版本和叙事时点的 claim。"""
        if claim.get("work_id") != work_id:
            return False
        family_work_id = claim.get("family_work_id")
        if family_work_id not in (None, "", work_id):
            return False
        if str(claim.get("status", "")).strip().lower() != "accepted":
            return False

        version_fields = {
            "source_id": source_id,
            "knowledge_version": knowledge_version,
            "source_version": source_version,
        }
        claim_versions = claim.get("versions")
        for field, expected in version_fields.items():
            if expected is None:
                continue
            actual = claim.get(field)
            if actual is None and isinstance(claim_versions, Mapping):
                actual = claim_versions.get(field)
            if actual is not None and str(actual) != str(expected):
                return False

        if source_id:
            evidence = claim.get("evidence")
            if isinstance(evidence, list):
                for item in evidence:
                    if not isinstance(item, Mapping):
                        continue
                    evidence_work_id = item.get("work_id")
                    if evidence_work_id not in (None, "", work_id):
                        return False
                scoped_evidence = [
                    item for item in evidence
                    if isinstance(item, Mapping) and item.get("source_id") == source_id
                ]
                if evidence and not scoped_evidence:
                    return False
                if source_version and scoped_evidence:
                    if not any(
                        item.get("source_version") in (None, "", source_version)
                        for item in scoped_evidence
                    ):
                        return False
        elif source_version:
            evidence = claim.get("evidence")
            if isinstance(evidence, list):
                for item in evidence:
                    if not isinstance(item, Mapping):
                        continue
                    evidence_work_id = item.get("work_id")
                    if evidence_work_id not in (None, "", work_id):
                        return False
                if evidence and not any(
                    item.get("source_version") in (None, "", source_version)
                    for item in evidence
                    if isinstance(item, Mapping)
                ):
                    return False

        try:
            point = claim.get("narrative_order")
            if point is not None and int(point) > narrative_order:
                return False
            lower = claim.get("valid_from_order")
            if lower is None:
                lower = claim.get("effective_narrative_order")
            if lower is not None and int(lower) > narrative_order:
                return False
            upper = claim.get("valid_to_order")
            if upper is not None and int(upper) < narrative_order:
                return False
        except (TypeError, ValueError):
            return False
        return True

    def _filter_claims_for_scope(
        self,
        claims: list[dict[str, Any]],
        *,
        work_id: str,
        source_id: Optional[str],
        source_version: Optional[str],
        knowledge_version: Optional[str],
        narrative_order: int,
    ) -> tuple[list[dict[str, Any]], int]:
        scoped: list[dict[str, Any]] = []
        public_scopes = {"", "all", "public", "global", "canon", "world"}
        for claim in claims:
            if not self._claim_matches_scope(
                claim,
                work_id=work_id,
                source_id=source_id,
                source_version=source_version,
                knowledge_version=knowledge_version,
                narrative_order=narrative_order,
            ):
                continue
            normalized = dict(claim)
            scope = str(normalized.get("scope") or "").strip().lower()
            if scope not in public_scopes:
                normalized["is_secret"] = True
            elif "is_secret" not in normalized:
                normalized["is_secret"] = False
            scoped.append(normalized)
        return scoped, len(claims) - len(scoped)

    @staticmethod
    def _revelation_matches_scope(
        revelation: Mapping[str, Any],
        *,
        work_id: str,
        source_id: Optional[str],
        source_version: Optional[str],
        knowledge_version: Optional[str],
        narrative_order: int,
        visible_claim_ids: set[str],
    ) -> bool:
        revelation_work_id = revelation.get("work_id")
        if revelation_work_id not in (None, "", work_id):
            return False
        for field, expected in (
            ("source_id", source_id),
            ("source_version", source_version),
            ("knowledge_version", knowledge_version),
        ):
            actual = revelation.get(field)
            if expected is not None and actual is not None and str(actual) != str(expected):
                return False
        try:
            point = revelation.get("narrative_order")
            if point is not None and int(point) > narrative_order:
                return False
            lower = revelation.get("valid_from_order")
            if lower is not None and int(lower) > narrative_order:
                return False
            upper = revelation.get("valid_to_order")
            if upper is not None and int(upper) < narrative_order:
                return False
        except (TypeError, ValueError):
            return False
        claim_id = revelation.get("claim_id") or revelation.get("target_claim_id")
        if claim_id is not None and str(claim_id) not in visible_claim_ids:
            return False
        scope = str(revelation.get("scope") or "").strip().lower()
        public_scopes = {"", "all", "public", "global", "canon", "world"}
        if scope not in public_scopes and claim_id is None:
            return False
        return True

    @staticmethod
    def _event_matches_scope(
        event: Mapping[str, Any],
        *,
        work_id: str,
        source_id: Optional[str],
        source_version: Optional[str],
        knowledge_version: Optional[str],
        narrative_order: int,
    ) -> bool:
        """过滤显式标注为其他作品、版本或未来时点的事件。"""
        event_work_id = event.get("work_id")
        if event_work_id not in (None, "", work_id):
            return False
        for field, expected in (
            ("source_id", source_id),
            ("source_version", source_version),
            ("knowledge_version", knowledge_version),
        ):
            actual = event.get(field)
            if expected is not None and actual is not None and str(actual) != str(expected):
                return False
        try:
            valid_from = event.get("valid_from_order")
            if valid_from is not None and int(valid_from) > narrative_order:
                return False
            valid_to = event.get("valid_to_order")
            if valid_to is not None and int(valid_to) < narrative_order:
                return False
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _prune_context_sections(
        sections: list[tuple[str, str, bool]],
        budget: int,
        pruning_log: list[str],
    ) -> str:
        max_chars = max(1, int(budget * 1.5))
        mandatory = [item for item in sections if item[2]]
        optional = [item for item in sections if not item[2]]
        kept: list[str] = []
        used = 0

        for label, text, _ in mandatory + optional:
            if not text:
                continue
            separator = 2 if kept else 0
            available = max_chars - used - separator
            if available <= 0:
                pruning_log.append(f"budget pruned section: {label}")
                continue
            if len(text) <= available:
                kept.append(text)
                used += separator + len(text)
                continue
            kept.append(text[:available] + "\n...[context budget pruned]...")
            pruning_log.append(f"budget truncated section: {label}")
            used = max_chars

        if not any(item.startswith("budget ") for item in pruning_log):
            pruning_log.append(f"budget retained all sections within {max_chars} chars")
        return "\n\n".join(kept)

    def assemble_writing_context(
        self,
        work_id: str,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        knowledge_version: Optional[str] = None,
        style_package_version: Optional[str] = None,
        *,
        style_selection: str = "none",
        style_package: Optional[Mapping[str, Any]] = None,
        mode: str = "original_in_world",
        purpose: str = "drafting",
        pov_character_id: str = "",
        current_narrative_order: int = 0,
        scene_beat: Optional[Mapping[str, Any]] = None,
        objective: Optional[str] = None,
        participants: Optional[list[str]] = None,
        claims: Optional[list[dict[str, Any]]] = None,
        canon_events: Optional[list[dict[str, Any]]] = None,
        divergence_narrative_order: Optional[int] = None,
        budget: int = 3000,
    ) -> dict[str, Any]:
        """组装带版本边界、角色认知和 POD 过滤的可复用写作上下文。

        该服务只消费调用者明确传入的 source/knowledge/style 版本；不会从
        当前源目录、最新数据库头或其他作品回退推断版本。返回普通字典，
        由 API 层映射到自己的响应模型，因而不依赖 router 或 contracts。
        """
        if style_selection not in self._STYLE_SELECTIONS:
            raise ValueError(f"INVALID_POLICY: unsupported style_selection={style_selection!r}")
        scene_data = dict(scene_beat or {})
        order = int(current_narrative_order)
        if order < 0:
            raise ValueError("current_narrative_order must be non-negative")
        missing_required: list[str] = []
        pruning_log: list[str] = []

        if not work_id:
            missing_required.append("work_id")
        if not source_id:
            missing_required.append("source_id")
        if not source_version:
            missing_required.append("source_version")
        if not knowledge_version:
            missing_required.append("knowledge_version")
        if mode == "original_in_world" and not pov_character_id:
            missing_required.append("pov_character_id")
        if style_selection != "none" and not style_package_version:
            missing_required.append("style_package_version")
        if style_selection != "none" and style_package is None:
            missing_required.append("style_package")
        if style_selection == "none" and (style_package_version or style_package is not None):
            missing_required.append("style_selection")

        package_body: dict[str, Any] = {}
        style_package_hash: Optional[str] = None
        package_version: Optional[str] = None
        style_package_usable = style_selection == "none"
        if style_selection != "none" and style_package_version and style_package is not None:
            if not isinstance(style_package, Mapping):
                raise ValueError("STYLE_PACKAGE_INVALID: style_package must be an object")
            package_body_value = style_package.get("package")
            package_body = dict(package_body_value) if isinstance(package_body_value, Mapping) else dict(style_package)
            package_version = (
                style_package.get("version")
                or package_body.get("version")
                or package_body.get("style_package_version")
            )
            if package_version and package_version != style_package_version:
                missing_required.append("style_package_version_match")
            expected_status = (
                "APPROVED" if style_selection == "approved" else "EVALUATION_CANDIDATE"
            )
            actual_status = str(style_package.get("status") or "").strip().upper()
            approved_head_alias = (
                style_selection == "approved"
                and actual_status == "AVAILABLE"
                and style_package.get("source") == "v2_style_head"
            )
            if actual_status != expected_status and not approved_head_alias:
                raise ValueError(
                    f"STYLE_SELECTION_STATUS_CONFLICT: expected {expected_status}, got {actual_status or '[missing]'}"
                )
            declared_hash = style_package.get("package_hash") or style_package.get("style_package_hash")
            if not declared_hash:
                raise ValueError("STYLE_PACKAGE_HASH_REQUIRED: selected style package has no package_hash")
            actual_hash = calculate_package_hash(package_body)
            if str(declared_hash) != actual_hash:
                raise ValueError(
                    "STYLE_PACKAGE_HASH_MISMATCH: selected style package does not match its original package_hash"
                )
            style_package_hash = str(declared_hash)
            style_package_usable = not any(
                item == "style_package_version_match" for item in missing_required
            )

        participants_list: list[str] = []
        if pov_character_id:
            participants_list.append(pov_character_id)
        for participant in participants or []:
            if participant and participant not in participants_list:
                participants_list.append(participant)

        source_snapshot = {
            "type": "source_snapshot",
            "source_id": source_id,
            "source_version": source_version,
        }
        facts: list[dict[str, Any]] = [source_snapshot]
        if objective:
            facts.append({"type": "objective", "value": objective})

        loaded_claims = (
            list(claims)
            if claims is not None
            else self._load_claims(
                work_id,
                source_id=source_id,
                source_version=source_version,
            )
        )
        all_claims, scope_filtered_count = self._filter_claims_for_scope(
            loaded_claims,
            work_id=work_id,
            source_id=source_id,
            source_version=source_version,
            knowledge_version=knowledge_version,
            narrative_order=order,
        )
        if scope_filtered_count:
            pruning_log.append(
                f"scope filtered {scope_filtered_count} claim(s) outside work/version/time boundary"
            )
        pod_order = divergence_narrative_order
        if pod_order is None:
            for key in ("divergence_narrative_order", "divergence_chapter_order"):
                if scene_data.get(key) is not None:
                    pod_order = int(scene_data[key])
                    break
        scoped_events = [
            event for event in (canon_events or [])
            if self._event_matches_scope(
                event,
                work_id=work_id,
                source_id=source_id,
                source_version=source_version,
                knowledge_version=knowledge_version,
                narrative_order=order,
            )
        ]
        if canon_events is not None and len(scoped_events) != len(canon_events):
            pruning_log.append(
                f"scope filtered {len(canon_events) - len(scoped_events)} event(s) outside work/version/time boundary"
            )
        filtered_events = self.pod_filter.filter_events(scoped_events, pod_order)
        if len(filtered_events) != len(scoped_events):
            pruning_log.append(
                f"POD filtered {len(scoped_events) - len(filtered_events)} dynamic event(s) after {pod_order}"
            )

        known_by_character: dict[str, set[str]] = {}
        for participant in participants_list:
            known_by_character[participant] = self.knowledge_tracker.get_known_claims_at_narrative_order(
                work_id=work_id,
                character_id=participant,
                narrative_order=order,
            )
        known_claim_ids = known_by_character.get(pov_character_id, set()) if pov_character_id else set()
        sanitized = POVFilter.sanitize_context_for_pov(
            {"claims": all_claims},
            observer_char_id=pov_character_id,
            known_claim_ids=known_claim_ids,
        )
        visible_claims = sanitized.get("claims", [])
        hidden_count = len(all_claims) - len(visible_claims)
        if hidden_count:
            pruning_log.append(
                f"POV filtered {hidden_count} claim(s) unknown at narrative_order={order}"
            )
        for claim in visible_claims:
            facts.append({"type": "claim", **claim})

        raw_revelations = scene_data.get("planned_revelations", [])
        planned_revelations = [
            revelation
            for revelation in raw_revelations
            if isinstance(revelation, Mapping)
            and self._revelation_matches_scope(
                revelation,
                work_id=work_id,
                source_id=source_id,
                source_version=source_version,
                knowledge_version=knowledge_version,
                narrative_order=order,
                visible_claim_ids={
                    str(claim.get("claim_id")) for claim in visible_claims
                },
            )
        ]
        if isinstance(raw_revelations, list) and len(planned_revelations) != len(raw_revelations):
            pruning_log.append(
                f"scope filtered {len(raw_revelations) - len(planned_revelations)} planned revelation(s)"
            )
        if scene_data:
            filtered_scene_data = dict(scene_data)
            filtered_scene_data["planned_revelations"] = planned_revelations
            facts.append({"type": "scene_beat", "value": filtered_scene_data})
        for event in filtered_events:
            facts.append({"type": "causal_event", "value": event})

        eff_chapter = order // 10 if order >= 100 else order
        active_mutations = self.mutation_ledger.list_mutations(
            work_id=work_id,
            chapter=eff_chapter,
            status="active",
        )

        for m in active_mutations:
            facts.append({
                "type": "mutation_override",
                "mutation_id": m.mutation_id,
                "entity_id": m.entity_id,
                "mutation_type": m.mutation_type,
                "target_name": m.target_name,
                "trigger_chapter": m.trigger_chapter,
                "cause_event": m.cause_event,
                "payload": m.payload,
            })

        style_rules: list[dict[str, Any]] = []
        style_examples: list[dict[str, Any]] = []
        exact_text_rules: list[dict[str, Any]] = []
        selected_decisions: list[dict[str, Any]] = []
        rejected_decisions: list[dict[str, Any]] = []
        if style_selection != "none" and style_package_usable:
            raw_style_rules = self._object_items(
                package_body.get("style_rules", package_body.get("rules", [])),
                "rule",
            )
            raw_style_examples = self._object_items(
                package_body.get("style_examples", package_body.get("positive_exemplars", package_body.get("examples", []))),
                "example",
            )
            raw_exact_text_rules = self._object_items(
                package_body.get("exact_text_rules", package_body.get("negative_rules", [])),
                "rule",
            )
            style_rules, rule_decisions, rule_rejections = self._select_style_items(
                raw_style_rules,
                asset_kind="style_rule",
                work_id=work_id,
                scene_data=scene_data,
                mode=mode,
                purpose=purpose,
                pov_character_id=pov_character_id,
            )
            style_examples, example_decisions, example_rejections = self._select_style_items(
                raw_style_examples,
                asset_kind="style_example",
                work_id=work_id,
                scene_data=scene_data,
                mode=mode,
                purpose=purpose,
                pov_character_id=pov_character_id,
            )
            exact_text_rules, exact_decisions, exact_rejections = self._select_style_items(
                raw_exact_text_rules,
                asset_kind="exact_text_rule",
                work_id=work_id,
                scene_data=scene_data,
                mode=mode,
                purpose=purpose,
                pov_character_id=pov_character_id,
            )
            selected_decisions = [*rule_decisions, *example_decisions, *exact_decisions]
            rejected_decisions = [*rule_rejections, *example_rejections, *exact_rejections]
        if len(style_rules) > 3:
            pruning_log.append("style_rules capped at 3")
        if len(style_examples) > 1:
            pruning_log.append("style_examples capped at 1")
        style_rules = style_rules[:3]
        style_examples = style_examples[:1]
        exact_text_rules = exact_text_rules[:3]

        character_knowledge = {
            participant: [
                {
                    "claim_id": claim_id,
                    "known_at_narrative_order": order,
                }
                for claim_id in sorted(known_by_character[participant])
            ]
            for participant in participants_list
        }

        scene_type = scene_data.get("scene_type", "dialogue")
        legacy_context = self.assemble_and_prune(
            work_id=work_id,
            scene_type=scene_type,
            pov_character_id=pov_character_id,
            current_narrative_order=order,
            query=objective,
            characters=participants_list,
            events=scene_data.get("events", []),
            budget=budget,
            include_style_constraints=False,
        )
        context_sections = [
            ("versioned source", f"### Source\n- source_id: {source_id}\n- source_version: {source_version}", True),
            ("versioned knowledge", f"### Knowledge\n- knowledge_version: {knowledge_version}\n- narrative_order: {order}", True),
            ("pov", f"### POV\n- character_id: {pov_character_id or '[missing]'}", mode == "original_in_world"),
            ("legacy context", legacy_context, False),
            ("claims", "### Visible Claims\n" + "\n".join(
                f"- [{claim.get('claim_id')}] {claim.get('statement', '')}" for claim in visible_claims
            ), False),
            ("POD events", "### POD-safe Events\n" + "\n".join(
                f"- [{event.get('event_id', '')}] {event.get('summary', '')}" for event in filtered_events
            ), False),
            ("mutations", "### Active Fanfic Mutations (Overrides Canon Baseline)\n" + "\n".join(
                f"- [{m.mutation_id}] 实体: {m.entity_id} | 变动类型: {m.mutation_type} | 标的: {m.target_name} (起因: {m.cause_event})"
                for m in active_mutations
            ), True if active_mutations else False),
            ("style", f"### Style (style_package_version: {style_package_version})\n" + "\n".join(
                f"- {self._style_item_text(item, 'rule')}" for item in style_rules
            ) + ("\n" if style_rules and style_examples else "") + "\n".join(
                f"- {self._style_item_text(item, 'example')}" for item in style_examples
            ), False),
        ]
        assembled_context = self._prune_context_sections(context_sections, budget, pruning_log)

        if style_selection == "none":
            selection_reason = "style_selection=none; no learned style asset was selected"
        elif not style_package_usable:
            selection_reason = "style package view unavailable because its requested version did not match"
        else:
            selection_reason = str(
                style_package.get("selection_reason")
                if isinstance(style_package, Mapping) and style_package.get("selection_reason")
                else f"{style_selection} style package explicitly selected; conditions qualified"
            )
            if rejected_decisions:
                selection_reason += f"; rejected {len(rejected_decisions)} asset(s) by explicit conditions"
        style_view_payload = {
            "selection": style_selection,
            "style_package_version": style_package_version,
            "package_hash": style_package_hash,
            "selection_reason": selection_reason,
            "selected": selected_decisions,
            "rejected": rejected_decisions,
        }
        style_view_hash = self._stable_hash(style_view_payload)
        style_view = {**style_view_payload, "view_hash": style_view_hash}

        result: dict[str, Any] = {
            "work_id": work_id,
            "source_id": source_id or "",
            "source_version": source_version or "",
            "style_package_version": style_package_version,
            "package_hash": style_package_hash,
            "style_package_hash": style_package_hash,
            "style_view_hash": style_view_hash,
            "selection_reason": selection_reason,
            "style_view": style_view,
            "knowledge_version": knowledge_version or "",
            "versions": {
                "source_version": source_version,
                "knowledge_version": knowledge_version,
                "style_package_version": style_package_version,
            },
            "scope": {
                "work_id": work_id,
                "source_id": source_id,
                "source_version": source_version,
                "knowledge_version": knowledge_version,
                "narrative_order": order,
                "divergence_narrative_order": pod_order,
            },
            "mode": mode,
            "purpose": purpose,
            "style_selection": style_selection,
            "facts": facts,
            "claims": visible_claims,
            "causal_events": filtered_events,
            "active_mutations": [m.to_dict() for m in active_mutations],
            "character_knowledge": character_knowledge,
            "planned_revelations": planned_revelations,
            "exact_text_rules": exact_text_rules,
            "style_rules": style_rules,
            "style_examples": style_examples,
            "sacred_whitelist": scene_data.get("sacred_whitelist", []),
            "prompt_tax_limits": {"max_rules": 3, "max_examples": 1},
            "dialogue_inertia_spec": {
                "enabled": bool(participants_list),
                "participants": participants_list,
            },
            "assembled_context": assembled_context,
            "legacy_context": legacy_context,
            "pruning_log": pruning_log,
            "completeness_status": "INCOMPLETE" if missing_required else "COMPLETE",
            "missing_required": list(dict.fromkeys(missing_required)),
        }
        result["view_hash"] = self._stable_hash({
            "schema_version": "fxi-writing-context-view-v2",
            **{key: value for key, value in result.items() if key != "content_hash"},
        })
        result["content_hash"] = self._stable_hash({
            key: value for key, value in result.items() if key != "content_hash"
        })
        return result

    def assemble_and_prune(
        self,
        work_id: str,
        scene_type: str | SceneType,
        pov_character_id: str,
        current_narrative_order: int,
        query: Optional[str] = None,
        territory_id: Optional[str] = None,
        skill_slug: Optional[str] = None,
        budget: int = 3500,
        characters: Optional[list[str]] = None,
        props: Optional[list[str]] = None,
        events: Optional[list[str]] = None,
        include_style_constraints: bool = True,
    ) -> str:
        """
        组装纯净、结构化、无视点穿帮的场景写作上下文，并严格剪枝在 budget 预算内
        """
        norm_scene_type = SceneType.normalize(scene_type)
        events_list = list(events or [])
        if query and query not in events_list:
            events_list.append(query)

        # 扫描场景焦点 (对白样板、图腾物证、场景分流)
        focus_res = self.scene_radar.scan_scene_context(
            work_id=work_id,
            scene_type=norm_scene_type,
            pov_character_id=pov_character_id,
            characters=characters,
            props=props,
            events=events_list,
        )

        sections: list[str] = []

        # 1. 视点与当前性格阶段 (约 400 字符)
        sections.append(f"### 1. 当前视点 (POV: {pov_character_id})")
        phase = self.phase_mgr.get_active_phase(work_id, pov_character_id, current_narrative_order)
        if phase:
            traits_str = ", ".join(phase.get("traits", []))
            sections.append(f"- 性格阶段: 【{phase['phase_name']}】 (特征: {traits_str})")
            if phase.get("tone_examples"):
                sections.append(f"- 语气范例: \"{phase['tone_examples'][0]}\"")

        # 2. 阶梯化能力面板 (Active Deck) - 仅在非纯文戏/非日常场景展示
        if not focus_res["suppress_active_deck"]:
            proj = self.projector.project_for_prompt(
                work_id=work_id,
                character_id=pov_character_id,
                scene_type=norm_scene_type,
                current_order=current_narrative_order
            )
            if "active_deck" in proj and proj["active_deck"]:
                deck_strs = [f"{s['name']}({s['status']})" for s in proj["active_deck"]]
                sections.append(f"### 2. 焦点能力面板 (Active Deck)\n- 快捷神通/招式: {', '.join(deck_strs)}")
            if "status_stages" in proj:
                sections.append(f"- 状态阶位: 生命 {proj['status_stages']['health']} | 法力 {proj['status_stages']['energy']}")

        # 3. 角色音色切片 (文戏与日常场景高优先级注入)
        if focus_res.get("formatted_dialogue_section"):
            sections.append(focus_res["formatted_dialogue_section"])

        # 3.5 角色人际张力与表里双轨 (Interpersonal Dynamics & Subtext)
        all_chars = []
        if pov_character_id:
            all_chars.append(pov_character_id)
        for c in characters or []:
            if c and c not in all_chars:
                all_chars.append(c)

        if len(all_chars) >= 2:
            tensions = self.relation_mgr.get_tensions(work_id, all_chars)
            if tensions:
                t_lines = ["### 角色人际张力与表里双轨 (Interpersonal Dynamics & Subtext)"]
                for t in tensions:
                    pair_str = " × ".join(t.get("pair", []))
                    t_lines.append(f"- 【{pair_str}】 动态关系: {t.get('dynamic', '')}")
                    if t.get("tension"):
                        t_lines.append(f"  * 心理博弈与张力: {t['tension']}")
                    if t.get("shared_secret"):
                        t_lines.append(f"  * 共同隐秘底牌: {t['shared_secret']}")
                    att = t.get("interpersonal_attitude", {})
                    if att:
                        att_strs = [f"{k}: {v}" for k, v in att.items() if k in all_chars]
                        if att_strs:
                            t_lines.append(f"  * 表里态度: {'；'.join(att_strs)}")
                sections.append("\n".join(t_lines))

        # 4. 场景物证与图腾铭文 (若命中物证道具则注入)
        if focus_res.get("formatted_symbolic_section"):
            sections.append(focus_res["formatted_symbolic_section"])

        # 5. 宏观据点态势标签 (若有据点)
        if territory_id:
            tags = self.macro_tags.generate_semantic_brief(work_id, territory_id)
            if tags:
                sections.append("### 宏观基业态势\n" + "\n".join([f"- {t}" for t in tags]))

        # 6. 被动威胁雷达防吃书警报 (若命中则注入)
        threat_alerts = focus_res.get("threat_alerts") or []
        if threat_alerts:
            sections.append("### 被动抗性防吃书警报\n" + "\n".join([f"- {a}" for a in threat_alerts]))

        # 6.5 同人生效变动事实 (覆写原著基准设定)
        order_val = current_narrative_order // 10 if current_narrative_order >= 100 else current_narrative_order
        mutations = self.mutation_ledger.list_mutations(work_id=work_id, chapter=order_val, status="active")
        if mutations:
            m_lines = ["### 同人生效变动事实 (覆写原著基准设定)"]
            for m in mutations:
                m_lines.append(f"- 【{m.entity_id}】{m.mutation_type}: {m.target_name} (因果源: {m.cause_event})")
            sections.append("\n".join(m_lines))

        # 7. 写作负向禁写教条与高分规则 (约 300 字符)
        anti_rules = []
        if include_style_constraints:
            anti_rules = self.anti_patterns.get_negative_constraints(
                skill_slug=skill_slug,
                scene_type=norm_scene_type.value,
                work_id=work_id,
            )
        if anti_rules:
            top_anti = anti_rules[:4]
            sections.append("### 负面行文禁令 (Strict Negative Constraints)\n" + "\n".join([f"- 严禁: {r}" for r in top_anti]))

        full_context = "\n\n".join(sections)

        # 预算截断守卫 (粗估 1 token ~= 1.5 字符中文)
        max_chars = int(budget * 1.5)
        if len(full_context) > max_chars:
            full_context = full_context[:max_chars] + "\n\n...[已达 Token 预算上限，其余冷参数自动剪枝]..."

        return full_context
