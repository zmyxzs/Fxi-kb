"""
fxi.game_engine.passive_radar - 被动抗性与冷门功法防吃书雷达
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class PassiveThreatRadar:
    """被动技能与沉睡抗性防吃书雷达"""

    # 常见威胁与被动关键词映射
    THREAT_KEYWORDS = {
        "毒": ["毒", "蛊", "瘴气", "剧毒", "蛇毒"],
        "神识": ["神识", "精神", "搜魂", "窥探", "幻术", "魅惑"],
        "火": ["烈火", "火毒", "丹火", "天火", "熔岩"],
        "冰": ["玄冰", "寒气", "冰冻", "阴煞"],
        "暗杀": ["潜行", "暗杀", "伏击", "隐形", "影匿"],
    }

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def scan_scene_threats(
        self,
        work_id: str,
        character_id: str,
        scene_outline_text: str
    ) -> list[str]:
        """
        当大纲或正文出现威胁时，单点检索唤醒沉睡的被动抗性，生成防吃书提示
        """
        detected_categories = []
        for cat, keywords in self.THREAT_KEYWORDS.items():
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
                desc = row["description"]
                for cat in detected_categories:
                    if cat in name or cat in desc:
                        advisories.append(
                            f"【防吃书警报】：主角掌握被动能力【{name}】({desc})，面对场景中的【{cat}属性威胁】具备克制抗性，请勿描写主角被寻常此类手段算计！"
                        )
                        break

        return advisories
