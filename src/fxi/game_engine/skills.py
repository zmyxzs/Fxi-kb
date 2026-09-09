"""
fxi.game_engine.skills - 作品级技能学习、装备、消耗与冷却状态机
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import SkillCastIllegalError
from fxi.domain.entities import validate_work_id
from fxi.storage.sqlite_client import DatabaseClient


class SkillTreeEngine:
    """技能与功法秘术状态机引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    @staticmethod
    def _validate_order_and_mp(current_narrative_order: int, current_mp: Optional[int] = None) -> None:
        if isinstance(current_narrative_order, bool) or not isinstance(current_narrative_order, int) or current_narrative_order < 0:
            raise ValueError("current_narrative_order 必须是非负整数")
        if current_mp is not None and (
            isinstance(current_mp, bool) or not isinstance(current_mp, int) or current_mp < 0
        ):
            raise ValueError("current_mp 必须是非负整数")

    @staticmethod
    def _cast_check(
        row: Mapping[str, Any],
        current_narrative_order: int,
        current_mp: Optional[int],
    ) -> tuple[bool, str]:
        if not bool(row["is_equipped"]):
            return False, f"技能【{row['name']}】尚未装备，不能施放"
        cost_mp = int(row["cost_mp"])
        cooldown_steps = int(row["cooldown_narrative_steps"])
        last_cast = int(row["last_cast_narrative_order"] or 0)
        if cost_mp < 0 or cooldown_steps < 0:
            return False, f"技能【{row['name']}】的消耗或冷却配置非法"
        if current_mp is not None and current_mp < cost_mp:
            return False, f"法力/真元匮乏: 需要 {cost_mp} 点，当前仅有 {current_mp} 点"
        if current_narrative_order < last_cast:
            return False, "当前剧情节点早于上一次施放记录，拒绝回写冷却状态"
        steps_passed = current_narrative_order - last_cast
        if steps_passed < cooldown_steps:
            remaining = cooldown_steps - steps_passed
            return False, f"技能【{row['name']}】尚在冷却中 (冷却步长 {cooldown_steps}，尚余 {remaining})"
        return True, "就绪可释放"

    @staticmethod
    def _skill_query() -> str:
        return """
        SELECT
            cs.skill_level,
            cs.last_cast_narrative_order,
            cs.is_equipped,
            st.name,
            st.skill_type,
            st.cost_mp,
            st.cooldown_narrative_steps
        FROM character_skills cs
        JOIN skills_tree st ON cs.skill_id = st.skill_id AND cs.work_id = st.work_id
        WHERE cs.work_id = ? AND cs.entity_id = ? AND cs.skill_id = ?
        """

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
        """在指定作品的技能库中注册技能模板"""
        work_id = validate_work_id(work_id)
        if cost_mp < 0 or cooldown_steps < 0:
            raise ValueError("技能消耗和冷却必须是非负整数")
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT INTO skills_tree
                (skill_id, work_id, name, tier, skill_type, cost_mp, cooldown_narrative_steps, prerequisite_skill_id, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_id, skill_id) DO UPDATE SET
                    name = excluded.name,
                    tier = excluded.tier,
                    skill_type = excluded.skill_type,
                    cost_mp = excluded.cost_mp,
                    cooldown_narrative_steps = excluded.cooldown_narrative_steps,
                    prerequisite_skill_id = excluded.prerequisite_skill_id,
                    description = excluded.description
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
        """角色习得指定作品中的技能，并设置是否进入快捷装备栏"""
        work_id = validate_work_id(work_id)
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            skill = cur.execute(
                "SELECT skill_id FROM skills_tree WHERE work_id = ? AND skill_id = ?",
                (work_id, skill_id),
            ).fetchone()
            if not skill:
                raise SkillCastIllegalError(f"作品 [{work_id}] 未注册技能 [{skill_id}]")
            ensure_entity(cur, work_id, entity_id)
            cur.execute(
                """
                INSERT INTO character_skills
                (work_id, entity_id, skill_id, skill_level, is_equipped, last_cast_narrative_order)
                VALUES (?, ?, ?, 1, ?, 0)
                ON CONFLICT(work_id, entity_id, skill_id) DO UPDATE SET
                    is_equipped = excluded.is_equipped
                """,
                (work_id, entity_id, skill_id, 1 if is_equipped else 0)
            )

    def equip_skill(self, work_id: str, entity_id: str, skill_id: str, is_equipped: bool = True) -> None:
        """装配或卸下快捷栏技能 (Active Deck)"""
        work_id = validate_work_id(work_id)
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                UPDATE character_skills SET is_equipped = ?
                WHERE work_id = ? AND entity_id = ? AND skill_id = ?
                """,
                (1 if is_equipped else 0, work_id, entity_id, skill_id)
            )
            if cur.rowcount != 1:
                raise SkillCastIllegalError(f"角色 [{entity_id}] 尚未掌握作品 [{work_id}] 的技能 [{skill_id}]")

    def validate_cast(
        self,
        work_id: str,
        entity_id: str,
        skill_id: str,
        current_narrative_order: int,
        current_mp: int
    ) -> tuple[bool, str]:
        """
        校验角色是否可以在当前剧情节点释放该技能：已学习、已装备、法力充足、冷却就绪。
        返回: (is_legal, reason)
        """
        work_id = validate_work_id(work_id)
        self._validate_order_and_mp(current_narrative_order, current_mp)
        with self.db_client.get_connection() as conn:
            row = conn.execute(self._skill_query(), (work_id, entity_id, skill_id)).fetchone()
        if not row:
            return False, f"角色尚未掌握技能 [{skill_id}]"
        return self._cast_check(row, current_narrative_order, current_mp)

    def record_cast(
        self,
        work_id: str,
        entity_id: str,
        skill_id: str,
        current_narrative_order: int,
        current_mp: Optional[int] = None,
    ) -> Optional[int]:
        """记录技能释放并更新冷却；传入 current_mp 时返回扣减后的剩余 MP。"""
        work_id = validate_work_id(work_id)
        self._validate_order_and_mp(current_narrative_order, current_mp)
        with self.db_client.transaction() as cur:
            row = cur.execute(self._skill_query(), (work_id, entity_id, skill_id)).fetchone()
            if not row:
                raise SkillCastIllegalError(f"角色尚未掌握技能 [{skill_id}]")
            legal, reason = self._cast_check(row, current_narrative_order, current_mp)
            if not legal:
                raise SkillCastIllegalError(reason)
            updated = cur.execute(
                """
                UPDATE character_skills
                SET last_cast_narrative_order = ?
                WHERE work_id = ? AND entity_id = ? AND skill_id = ?
                  AND last_cast_narrative_order = ?
                """,
                (
                    current_narrative_order,
                    work_id,
                    entity_id,
                    skill_id,
                    int(row["last_cast_narrative_order"] or 0),
                )
            )
            if updated.rowcount != 1:
                raise SkillCastIllegalError("技能状态在施放期间发生变化，请重新校验")
        if current_mp is None:
            return None
        return current_mp - int(row["cost_mp"])

    def cast_skill(
        self,
        work_id: str,
        entity_id: str,
        skill_id: str,
        current_narrative_order: int,
        current_mp: int,
    ) -> dict[str, Any]:
        """原子完成技能门禁、MP 扣减结果计算与冷却记录。"""
        remaining_mp = self.record_cast(
            work_id=work_id,
            entity_id=entity_id,
            skill_id=skill_id,
            current_narrative_order=current_narrative_order,
            current_mp=current_mp,
        )
        with self.db_client.get_connection() as conn:
            row = conn.execute(
                "SELECT cost_mp FROM skills_tree WHERE work_id = ? AND skill_id = ?",
                (validate_work_id(work_id), skill_id),
            ).fetchone()
        if row is None or remaining_mp is None:
            raise SkillCastIllegalError("技能释放记录不完整")
        return {
            "work_id": work_id,
            "entity_id": entity_id,
            "skill_id": skill_id,
            "cost_mp": int(row["cost_mp"]),
            "remaining_mp": remaining_mp,
            "narrative_order": current_narrative_order,
            "recorded": True,
        }
