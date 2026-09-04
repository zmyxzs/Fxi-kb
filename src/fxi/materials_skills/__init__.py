"""
fxi.materials_skills - 写作技法、负向行文教条与成果接收模块
"""

from .anti_patterns import AntiPatternRepository
from .distillation_receiver import DistillationReceiver
from .skill_store import SkillStore

__all__ = [
    "SkillStore",
    "AntiPatternRepository",
    "DistillationReceiver",
]
