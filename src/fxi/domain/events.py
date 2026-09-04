"""
fxi.domain.events - 情节事件模型
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PlotEvent:
    event_id: str
    work_id: str
    scene_uuid: str
    narrative_order: int
    physical_time: str
    summary: str
    timeline_id: str = "main"
    is_canon: bool = True
