"""
fxi.core.types - 全局枚举与核心类型
"""

from __future__ import annotations
from enum import Enum
from typing import Any, Union


class SceneType(str, Enum):
    COMBAT = "combat"                     # 战斗厮杀 (聚焦技能冷却、血量、招式禁令)
    DIALOGUE = "dialogue"                 # 对话博弈 (聚焦角色秘密、性格阶段、视点防穿帮)
    CAMPUS_DIALOGUE = "campus_dialogue"   # 校园/人际对白
    FAMILY_WARMTH = "family_warmth"       # 家庭温馨日常
    SLICE_OF_LIFE = "slice_of_life"       # 市井烟火日常
    INVESTIGATION = "investigation"       # 探案与微观物证调查
    TERRITORY = "territory"               # 领地/基业治理 (聚焦宏观资源状态、民心动向、建筑进度)
    CULTIVATION = "cultivation"           # 闭关修炼/突破 (聚焦境界突破、功法心魔、丹药消耗)
    EXPLORATION = "exploration"           # 秘境探索 (聚焦地图机制、物品储物袋、环境禁忌)

    @classmethod
    def normalize(cls, val: str | "SceneType") -> "SceneType":
        if isinstance(val, cls):
            return val
        s = str(val).strip().lower()
        for member in cls:
            if member.value == s:
                return member
        # 宽容别名映射
        if "dialogue" in s or "chat" in s or "talk" in s or "warmth" in s:
            return cls.DIALOGUE
        if "combat" in s or "fight" in s or "battle" in s:
            return cls.COMBAT
        if "investigat" in s or "clue" in s:
            return cls.INVESTIGATION
        if "life" in s or "daily" in s:
            return cls.SLICE_OF_LIFE
        return cls.DIALOGUE


class MetricStatus(str, Enum):
    EXPLICIT = "EXPLICIT"           # 精确确知数值 (如 3500 金币)
    UNMEASURED = "UNMEASURED"       # 尚未测量/模糊状态 (如 约数千金币)
    NOT_APPLICABLE = "NOT_APPLICABLE" # 不适用该实体 (如 骷髅士兵无精力值)


class CausalStatus(str, Enum):
    UNTOUCHED = "untouched"         # 原著基石: 未受同人变动波及，可 100% 沿用
    MUTATED = "mutated"             # 受到波及: 核心事件发生，但参与人/时间/地点发生异化
    INVALIDATED = "invalidated"     # 彻底失效: 前置因果已被同人修改，本事件不可再发生


class LifecycleAction(str, Enum):
    EXCLUDE = "exclude"             # 软排除 (当前场景不生效)
    DISABLE = "disable"             # 禁用
    SUPERSEDE = "supersede"         # 替代 (新设覆盖旧设)
    PURGE = "purge"                 # 硬物理删除 (极度谨慎)


class ForeshadowingStatus(str, Enum):
    PLANTED = "planted"             # 伏笔已埋下
    HINTED = "hinted"               # 伏笔产生轻量呼应
    RESOLVED = "resolved"           # 伏笔已回收
    ABANDONED = "abandoned"         # 废弃不收


class TaskType(str, Enum):
    FAST_EXTRACTION = "fast_extraction"
    STRUCTURED_REPAIR = "structured_repair"
    DEEP_REASONING_OOC = "deep_reasoning_ooc"
    RIPPLE_ANALYSIS = "ripple_analysis"
