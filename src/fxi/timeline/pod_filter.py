"""
fxi.timeline.pod_filter - 同人分歧点阻断器 (POD Filter)
"""

from typing import Any, Optional


class PODFilter:
    """同人分歧点阻断器"""

    @staticmethod
    def filter_canon_events(
        canon_events: list[dict[str, Any]],
        divergence_chapter_order: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """
        同人作品在 divergence_chapter_order 产生分歧；
        自动切断原著在该分歧点之后的一切动态剧情事件，保留分歧点之前的基石事实
        """
        if divergence_chapter_order is None:
            return canon_events

        valid_events = []
        for ev in canon_events:
            ev_order = ev.get("narrative_order", 0)
            if ev_order <= divergence_chapter_order:
                valid_events.append(ev)
            elif ev.get("is_static_lore", False):
                # 静态法则豁免阻断
                valid_events.append(ev)

        return valid_events
