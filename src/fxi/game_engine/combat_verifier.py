"""
fxi.game_engine.combat_verifier - 战斗与法术释放合法性验证器
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import SkillCastIllegalError
from fxi.game_engine.skills import SkillTreeEngine


class CombatVerifier:
    """战斗行为合规审查引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.engine = SkillTreeEngine(self.config)

    def verify_action(
        self,
        work_id: str,
        character_id: str,
        skill_id: str,
        current_narrative_order: int,
        current_mp: int
    ) -> None:
        """
        验证技能释放是否合法，若非法直接抛出 SkillCastIllegalError
        """
        is_legal, reason = self.engine.validate_cast(
            work_id=work_id,
            entity_id=character_id,
            skill_id=skill_id,
            current_narrative_order=current_narrative_order,
            current_mp=current_mp
        )
        if not is_legal:
            raise SkillCastIllegalError(f"战斗违规: 角色 [{character_id}] 释放 [{skill_id}] 失败: {reason}")
