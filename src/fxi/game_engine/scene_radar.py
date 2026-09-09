"""
fxi.game_engine.scene_radar - 场景焦点雷达 (全场景实体音色、图腾物证与威胁聚焦唤醒)
"""

import json
from typing import Any, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.types import SceneType
from fxi.domain.entities import EntityManager, validate_work_id
from fxi.game_engine.passive_radar import PassiveThreatRadar
from fxi.storage.sqlite_client import DatabaseClient


class SceneFocusRadar:
    """
    场景焦点雷达：
    1. 动态扫描在场人物，提取 Few-shot 级别的对抗性对白切片 (dialogue_samples)；
    2. 动态扫描场上核心道具与事件，唤醒物证图腾铭文 (symbolic_text) 与交互动作 (interaction_rituals)；
    3. 多场景路由：文戏/日常场景屏蔽技能数值，战斗场景屏蔽生活切片，保持 Token 极精简与注意力聚焦。
    """

    DIALOGUE_SCENE_TYPES = {
        "dialogue",
        "campus_dialogue",
        "family_warmth",
        "slice_of_life",
        SceneType.DIALOGUE.value,
    }

    COMBAT_SCENE_TYPES = {
        "combat",
        SceneType.COMBAT.value,
    }

    INVESTIGATION_SCENE_TYPES = {
        "investigation",
        "exploration",
        SceneType.EXPLORATION.value,
    }

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.entity_mgr = EntityManager(self.config)
        self.threat_radar = PassiveThreatRadar(self.config)

    def scan_scene_context(
        self,
        work_id: str,
        scene_type: str | SceneType,
        pov_character_id: str,
        characters: Optional[list[str]] = None,
        props: Optional[list[str]] = None,
        events: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        全景扫描当前场景所需的微观实体事实与图腾
        """
        work_id = validate_work_id(work_id)
        scene_type_str = scene_type.value if isinstance(scene_type, SceneType) else str(scene_type)
        is_dialogue = scene_type_str in self.DIALOGUE_SCENE_TYPES
        is_combat = scene_type_str in self.COMBAT_SCENE_TYPES

        # 1. 汇总在场人物
        all_chars = []
        if pov_character_id:
            all_chars.append(pov_character_id)
        for c in characters or []:
            if c and c not in all_chars:
                all_chars.append(c)

        # 2. 提取在场角色的音色与对白样本 (文戏与日常优先)
        dialogue_samples_map: dict[str, list[dict[str, str]]] = {}
        character_voice_briefs: dict[str, dict[str, Any]] = {}

        for char_name in all_chars:
            vp = self.entity_mgr.get_character_voice(work_id, char_name)

            if vp:
                character_voice_briefs[char_name] = {
                    "tone": vp.get("tone", ""),
                    "speech_style": vp.get("speech_style", ""),
                    "catchphrases": vp.get("catchphrases", [])[:4],
                    "gestures": vp.get("gestures", [])[:3],
                    "taboos": vp.get("taboos", [])[:3],
                }
                samples = vp.get("dialogue_samples", [])
                if samples:
                    dialogue_samples_map[char_name] = samples[:3]

        # 3. 扫描核心物证道具图腾
        symbolic_props = self._scan_symbolic_props(
            work_id=work_id,
            props=props or [],
            events=events or [],
        )

        # 4. 扫描被动威胁雷达 (战斗或含危机事件场景)
        threat_alerts = []
        events_str_list = [
            e if isinstance(e, str) else str(e.get("content") or e.get("summary") or "")
            for e in (events or [])
        ]
        events_blob = " ".join(events_str_list)
        if events_blob:
            threat_alerts = self.threat_radar.scan_scene_threats(work_id, pov_character_id, events_blob)

        # 5. 组装格式化文本段落
        formatted_dialogue_section = ""
        if is_dialogue and (dialogue_samples_map or character_voice_briefs):
            lines = ["### 角色音色切片 (对白交锋样板，对齐说话短句惯性与反套路态度)"]
            for cname, samples in dialogue_samples_map.items():
                lines.append(f"- 【{cname} 对话短样板】:")
                for s in samples:
                    ctx = s.get("context", "交锋情境")
                    usr = s.get("user", "")
                    rep = s.get("reply", s.get("char", ""))
                    if usr and rep:
                        lines.append(f"  * [{ctx}] 他人: “{usr}” -> {cname}: “{rep}”")
                    elif rep:
                        lines.append(f"  * [{ctx}] {cname}: “{rep}”")
            # 针对无历史对白样本但具备人设声线的新角色，注入关键口吻与口癖指引
            for cname, brief in character_voice_briefs.items():
                if cname not in dialogue_samples_map:
                    tone = brief.get("tone", "")
                    catchphrases = brief.get("catchphrases", [])[:2]
                    taboos = brief.get("taboos", [])[:2]
                    parts = []
                    if tone:
                        parts.append(f"声线口吻: {tone}")
                    if catchphrases:
                        parts.append(f"经典口癖: {' / '.join(f'“{cp}”' for cp in catchphrases)}")
                    if taboos:
                        parts.append(f"言行禁忌: {'；'.join(taboos)}")
                    if parts:
                        lines.append(f"- 【{cname} 音色与口吻指引】:")
                        for p in parts:
                            lines.append(f"  * {p}")
            formatted_dialogue_section = "\n".join(lines)

        formatted_symbolic_section = ""
        if symbolic_props:
            lines = ["### 场景物证与图腾铭文 (Symbolic Totems)"]
            for sp in symbolic_props:
                lines.append(f"- 【{sp['name']}】:")
                if sp.get("symbolic_text"):
                    lines.append(f"  * 核心图腾/铭文: {sp['symbolic_text']}")
                if sp.get("rituals"):
                    lines.append(f"  * 交互动作/微仪式: {'；'.join(sp['rituals'])}")
            formatted_symbolic_section = "\n".join(lines)

        return {
            "work_id": work_id,
            "scene_type": scene_type_str,
            "is_dialogue_oriented": is_dialogue,
            "is_combat": is_combat,
            "dialogue_samples": dialogue_samples_map,
            "character_voice_briefs": character_voice_briefs,
            "symbolic_props": symbolic_props,
            "threat_alerts": threat_alerts,
            "formatted_dialogue_section": formatted_dialogue_section,
            "formatted_symbolic_section": formatted_symbolic_section,
            "suppress_active_deck": is_dialogue,
        }

    def _scan_symbolic_props(
        self,
        work_id: str,
        props: list[Any],
        events: list[Any],
    ) -> list[dict[str, Any]]:
        """
        在大纲道具与核心事件中扫描具有精神图腾与誓词意象的物品实体
        """
        work_id = validate_work_id(work_id)
        props_str = [p if isinstance(p, str) else str(p.get("name") or p.get("id") or "") for p in (props or [])]
        events_str = [e if isinstance(e, str) else str(e.get("content") or e.get("summary") or "") for e in (events or [])]
        all_props_text = " ".join(props_str + events_str)
        if not all_props_text.strip():
            return []

        sql = f"""
        SELECT entity_id, name, aliases_json, attributes_yaml
        FROM entities
        WHERE work_id = ? AND category = 'item'
        """

        matched: list[dict[str, Any]] = []
        already_added = set()

        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id,))
            for row in cur.fetchall():
                ent_id = row["entity_id"]
                if ent_id in already_added:
                    continue

                name = row["name"]
                aliases = json.loads(row["aliases_json"] or "[]")
                names_to_match = [name] + aliases

                is_hit = False
                for n in names_to_match:
                    if n and n in all_props_text:
                        is_hit = True
                        break

                if is_hit:
                    attrs = yaml.safe_load(row["attributes_yaml"] or "{}")
                    symbolic_text = attrs.get("symbolic_text")
                    rituals = attrs.get("interaction_rituals", [])
                    if symbolic_text or rituals:
                        already_added.add(ent_id)
                        matched.append({
                            "entity_id": ent_id,
                            "name": name,
                            "symbolic_text": symbolic_text,
                            "rituals": rituals,
                            "function": attrs.get("function", ""),
                        })

        return matched
