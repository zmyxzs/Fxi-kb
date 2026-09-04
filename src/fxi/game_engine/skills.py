"""
fxi.game_engine.skills - 技能树、冷却步长与法力/真元消耗状态机
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import SkillCastIllegalError
from fxi.storage.sqlite_client import DatabaseClient


class SkillTreeEngine:
    """技能与功法秘术状态机引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def register_skill(
        self,
        work_id: str,
        skill_id: str,
        name: str,
        tier: int = 1,
        skill_type: str = "active",
        cost_mp: int = 0,
        cooldown_steps: int = 0,
        prerequisite_id: Optional[str] = None,
        description: str = ""
    ) -> None:
        """在全书技能库中注册技能模板"""
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT OR REPLACE INTO skills_tree
                (skill_id, work_id, name, tier, skill_type, cost_mp, cooldown_narrative_steps, prerequisite_skill_id, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (skill_id, work_id, name, tier, skill_type, cost_mp, cooldown_steps, prerequisite_id, description)
            )

    def learn_skill(
        self,
        work_id: str,
        entity_id: str,
        skill_id: str,
        is_equipped: bool = False
    ) -> None:
        """角色习得技能，并设置是否进入快捷装备栏"""
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, entity_id)
            cur.execute(
                """
                INSERT OR REPLACE INTO character_skills
                (work_id, entity_id, skill_id, skill_level, is_equipped, last_cast_narrative_order)
                VALUES (?, ?, ?, 1, ?, 0)
                """,
                (work_id, entity_id, skill_id, 1 if is_equipped else 0)
            )

    def equip_skill(self, work_id: str, entity_id: str, skill_id: str, is_equipped: bool = True) -> None:
        """装配或卸下快捷栏技能 (Active Deck)"""
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                UPDATE character_skills SET is_equipped = ?
                WHERE work_id = ? AND entity_id = ? AND skill_id = ?
                """,
                (1 if is_equipped else 0, work_id, entity_id, skill_id)
            )

    def validate_cast(
        self,
        work_id: str,
        entity_id: str,
        skill_id: str,
        current_narrative_order: int,
        current_mp: int
    ) -> tuple[bool, str]:
        """
        校验角色是否可以在当前剧情节点释放该技能：
        1. 检查技能是否已学会；
        2. 检查法力/真元是否充足；
        3. 检查冷却步长是否就绪。
        返回: (is_legal, reason)
        """
        sql = """
        SELECT
            cs.skill_level,
            cs.last_cast_narrative_order,
            st.name,
            st.cost_mp,
            st.cooldown_narrative_steps
        FROM character_skills cs
        JOIN skills_tree st ON cs.skill_id = st.skill_id AND cs.work_id = st.work_id
        WHERE cs.work_id = ? AND cs.entity_id = ? AND cs.skill_id = ?
        """
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, entity_id, skill_id))
            row = cur.fetchone()
            if not row:
                return False, f"角色尚未掌握技能 [{skill_id}]"

            skill_name = row["name"]
            cost_mp = row["cost_mp"]
            cooldown_steps = row["cooldown_narrative_steps"]
            last_cast = row["last_cast_narrative_order"]

            # 检查蓝量
            if current_mp < cost_mp:
                return False, f"法力/真元匮乏: 需要 {cost_mp} 点，当前仅有 {current_mp} 点"

            # 检查冷却步长
            steps_passed = current_narrative_order - last_cast
            if steps_passed < cooldown_steps:
                remaining = cooldown_steps - steps_passed
                return False, f"大招【{skill_name}】尚在冷却中 (冷却步长 {cooldown_steps} 章，尚余 {remaining} 章)"

            return True, "就绪可释放"

    def record_cast(
        self,
        work_id: str,
        entity_id: str,
        skill_id: str,
        current_narrative_order: int
    ) -> None:
        """记录技能释放并更新冷却起始节点"""
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                UPDATE character_skills
                SET last_cast_narrative_order = ?
                WHERE work_id = ? AND entity_id = ? AND skill_id = ?
                """,
                (current_narrative_order, work_id, entity_id, skill_id)
            )
