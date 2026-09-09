"""
fxi.timeline.pod_filter - 同人分歧点阻断器 (POD Filter)
"""

from typing import Any, Optional


class PODFilter:
    """同人分歧点阻断器"""

    _STATIC_MARKERS = frozenset({
        "static",
        "static_lore",
        "lore",
        "world_rule",
        "world_rules",
        "法则",
        "设定",
        "静态",
        "静态设定",
    })

    @classmethod
    def is_static_lore(cls, event: dict[str, Any]) -> bool:
        """判断事件是否属于 POD 之后仍可引用的静态世界知识。

        静态边界必须由事件显式标记；未标记事件默认按动态剧情处理，避免
        在分歧点之后把原著剧情误注入同人上下文。
        """
        if event.get("is_static_lore") is True:
            return True
        if event.get("is_dynamic") is False:
            return True
        for key in ("event_type", "event_kind", "lore_scope", "boundary"):
            marker = event.get(key)
            if isinstance(marker, str) and marker.strip().lower() in cls._STATIC_MARKERS:
                return True
        return False

    @classmethod
    def is_dynamic_event(cls, event: dict[str, Any]) -> bool:
        """判断事件是否为会被 POD 阻断的动态剧情事件。"""
        return not cls.is_static_lore(event)

    @staticmethod
    def filter_canon_events(
        canon_events: list[dict[str, Any]],
        divergence_chapter_order: Optional[int] = None,
        *,
        divergence_narrative_order: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        同人作品在 divergence_chapter_order 产生分歧；
        自动切断原著在该分歧点之后的一切动态剧情事件，保留分歧点之前的基石事实
        """
        if (
            divergence_chapter_order is not None
            and divergence_narrative_order is not None
            and divergence_chapter_order != divergence_narrative_order
        ):
            raise ValueError("divergence chapter and narrative orders must match")
        divergence_order = (
            divergence_narrative_order
            if divergence_narrative_order is not None
            else divergence_chapter_order
        )

        if divergence_order is None:
            return list(canon_events)

        valid_events = []
        for ev in canon_events:
            ev_order = ev.get("narrative_order", 0)
            if ev_order <= divergence_order:
                valid_events.append(ev)
            elif PODFilter.is_static_lore(ev):
                # 静态法则豁免阻断
                valid_events.append(ev)

        return valid_events

    @classmethod
    def filter_events(
        cls,
        events: list[dict[str, Any]],
        divergence_narrative_order: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """按显式叙事边界过滤事件的通用别名。"""
        return cls.filter_canon_events(
            events,
            divergence_narrative_order=divergence_narrative_order,
        )
