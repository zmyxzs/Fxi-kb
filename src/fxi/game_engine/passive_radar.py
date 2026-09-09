"""
fxi.game_engine.passive_radar - 作品级被动抗性与威胁词雷达
"""

from collections.abc import Mapping
from typing import Any, Optional
from fxi.core.config import FxiConfig, load_config
from fxi.domain.entities import (
    get_worldview_genre,
    load_work_config,
    select_work_section,
    validate_work_id,
)
from fxi.storage.sqlite_client import DatabaseClient


class PassiveThreatRadar:
    """被动技能与沉睡抗性防吃书雷达"""

    # 保留公开属性以兼容旧调用方；实际关键词必须来自作品配置。
    THREAT_KEYWORDS: dict[str, list[str]] = {}

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.last_status: dict[str, Any] = {"status": "UNINITIALIZED"}

    def _load_threat_keywords(self, work_id: str) -> dict[str, list[str]]:
        settings, error_code = load_work_config(self.config, work_id)
        if settings is None:
            self.last_status = {
                "work_id": work_id,
                "status": "INCOMPLETE",
                "error_code": error_code or "WORK_CONFIG_UNAVAILABLE",
            }
            return {}

        genre = get_worldview_genre(settings)
        raw: Any = None
        for section_name in ("threat_keywords", "threat_rules", "passive_threats"):
            raw = select_work_section(settings, section_name, genre)
            if raw is not None:
                break
        if not isinstance(raw, Mapping):
            self.last_status = {
                "work_id": work_id,
                "worldview_genre": genre,
                "status": "INCOMPLETE",
                "error_code": "THREAT_KEYWORDS_MISSING",
            }
            return {}

        keywords: dict[str, list[str]] = {}
        for category, values in raw.items():
            if not isinstance(category, str) or not category.strip():
                continue
            if isinstance(values, Mapping):
                values = values.get("keywords")
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                continue
            terms = [value.strip() for value in values if isinstance(value, str) and value.strip()]
            if terms:
                keywords[category.strip()] = terms

        if not keywords:
            self.last_status = {
                "work_id": work_id,
                "worldview_genre": genre,
                "status": "INCOMPLETE",
                "error_code": "THREAT_KEYWORDS_INVALID",
            }
            return {}
        self.last_status = {
            "work_id": work_id,
            "worldview_genre": genre,
            "status": "AVAILABLE",
        }
        return keywords

    def scan_scene_threats(
        self,
        work_id: str,
        character_id: str,
        scene_outline_text: str
    ) -> list[str]:
        """
        当大纲或正文出现威胁时，单点检索唤醒沉睡的被动抗性，生成防吃书提示
        """
        work_id = validate_work_id(work_id)
        if not isinstance(scene_outline_text, str):
            raise TypeError("scene_outline_text 必须是字符串")
        threat_keywords = self._load_threat_keywords(work_id)
        detected_categories = []
        for cat, keywords in threat_keywords.items():
            for kw in keywords:
                if kw in scene_outline_text:
                    detected_categories.append(cat)
                    break

        if not detected_categories:
            return []

        # 在角色全部已学技能 (包括冷门未装备的被动) 中扫描
        sql = """
        SELECT st.name, st.description
        FROM character_skills cs
        JOIN skills_tree st ON cs.skill_id = st.skill_id AND cs.work_id = st.work_id
        WHERE cs.work_id = ? AND cs.entity_id = ?
        """
        advisories = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, character_id))
            for row in cur.fetchall():
                name = row["name"]
                desc = row["description"] or ""
                for cat in detected_categories:
                    if cat in name or cat in desc:
                        advisories.append(
                            f"【防吃书警报】：角色掌握被动能力【{name}】({desc})，面对场景中的【{cat}属性威胁】具备配置中的克制抗性，请勿无依据地描写其被此类手段击穿！"
                        )
                        break

        return advisories
