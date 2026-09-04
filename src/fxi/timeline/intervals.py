"""
fxi.timeline.intervals - 时空有效区间与帧问题校验 (Frame Problem Guard)
"""

from typing import Optional


class IntervalValidator:
    """时间区间有效性校验器"""

    @staticmethod
    def is_valid_at(valid_from: int, valid_to: Optional[int], current_order: int) -> bool:
        """判定某条事实/相态在指定叙事步长下是否依然处于有效时段"""
        if current_order < valid_from:
            return False
        if valid_to is not None and current_order > valid_to:
            return False
        return True
