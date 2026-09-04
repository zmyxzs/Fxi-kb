"""
fxi.game_engine.projection - 参数三层阶梯投影器 (防撑爆 Prompt 核心机制)
"""

from typing import Any, Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.types import SceneType
from fxi.storage.sqlite_client import DatabaseClient


class TieredParameterProjector:
    """参数阶梯化投影器：将 200+ 属性压缩至 300~500 Tokens 的场景焦点"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def project_for_prompt(
        self,
        work_id: str,
        character_id: str,
        scene_type: SceneType,
        current_order: int,
        hp_ratio: float = 1.0,
        mp_ratio: float = 1.0
    ) -> dict[str, Any]:
        """
        根据场景类型投影精炼面板：
        - COMBAT: 仅暴露 4~6 个快捷技能 + 阶段化生命/法力状态 (MP: 30% 紧缺)
        - DIALOGUE: 屏蔽所有技能数值，仅保留身份称号与气场
        - TERRITORY: 暴露治理特权与统率力
        """
        result: dict[str, Any] = {
            "character_id": character_id,
            "scene_mode": scene_type.value,
        }

        if scene_type == SceneType.COMBAT:
            # 阶段化生命与法力
            hp_stage = "充盈" if hp_ratio > 0.7 else ("负伤" if hp_ratio > 0.3 else "垂危濒死")
            mp_stage = "充盈" if mp_ratio > 0.7 else ("紧缺" if mp_ratio > 0.2 else "枯竭空蓝")

            result["status_stages"] = {
                "health": f"{int(hp_ratio * 100)}% ({hp_stage})",
                "energy": f"{int(mp_ratio * 100)}% ({mp_stage})",
            }

            # 仅查询装备栏活跃技能 (Active Deck)
            sql = """
            SELECT st.name, st.tier, st.skill_type, st.cost_mp, st.cooldown_narrative_steps,
                   ( ? - cs.last_cast_narrative_order ) AS steps_since_cast
            FROM character_skills cs
            JOIN skills_tree st ON cs.skill_id = st.skill_id AND cs.work_id = st.work_id
            WHERE cs.work_id = ? AND cs.entity_id = ? AND cs.is_equipped = 1
            LIMIT 6
            """
            active_skills = []
            with self.db_client.get_connection() as conn:
                cur = conn.execute(sql, (current_order, work_id, character_id))
                for row in cur.fetchall():
                    cd = row["cooldown_narrative_steps"]
                    since = row["steps_since_cast"]
                    is_ready = since >= cd
                    active_skills.append({
                        "name": row["name"],
                        "type": row["skill_type"],
                        "cost": f"{row['cost_mp']}点",
                        "status": "就绪" if is_ready else f"冷却中(余{cd - since}章)",
                    })
            result["active_deck"] = active_skills

        elif scene_type == SceneType.DIALOGUE:
            result["focus"] = "文戏交锋: 隐藏全部技能与攻击力面板，聚焦身份性格与视点秘密"

        elif scene_type == SceneType.TERRITORY:
            result["focus"] = "领地治政: 聚焦领主统帅力与政令调度"

        return result
