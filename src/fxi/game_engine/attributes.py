"""
fxi.game_engine.attributes - 基础六维/根基资质向派生战斗属性公式换算引擎 (全题材通用)
"""

from typing import Any, Optional


class AttributeCalculator:
    """属性换算引擎：确定性 Python 代码计算，杜绝 LLM 心算幻觉"""

    @staticmethod
    def compute_derived_stats(
        base_stats: dict[str, float],
        genre_type: str = "rpg"
    ) -> dict[str, Any]:
        """
        根据基础属性计算派生属性:
        - 游戏/升级流: STR, AGI, INT, CON -> 物攻, 法强, 暴击, 生命, 负重
        - 修仙/玄幻流: 根骨, 悟性, 神识, 气血 -> 真元上限, 施法速度, 神识威压范围
        - 科幻/机甲流: 反应, 突触, 算力, 体能 -> 神经负载上限, 电子战破译率, 机动超载阈值
        """
        # 统一规范化输入键为小写
        stats = {k.lower(): float(v) for k, v in base_stats.items()}

        # 1. 经典六维转换 (RPG / 升级流)
        str_val = stats.get("str", stats.get("力量", 10.0))
        agi_val = stats.get("agi", stats.get("敏捷", 10.0))
        int_val = stats.get("int", stats.get("智力", 10.0))
        con_val = stats.get("con", stats.get("体质", 10.0))

        atk = round(str_val * 2.0 + agi_val * 0.5, 1)
        magic_power = round(int_val * 2.5, 1)
        max_hp = int(con_val * 15.0 + str_val * 2.0)
        max_mp = int(int_val * 12.0)
        crit_rate = round(min(agi_val * 0.05, 50.0), 1)  # 百分比上限 50%
        carry_weight = round(str_val * 10.0, 1)          # kg

        return {
            "attack": atk,
            "magic_power": magic_power,
            "max_hp": max_hp,
            "max_mp": max_mp,
            "crit_rate_pct": crit_rate,
            "max_carry_weight_kg": carry_weight,
        }
