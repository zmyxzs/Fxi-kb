"""
fxi.state_ledger.events - 不可变状态变动事件
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class StateEvent:
    event_id: Optional[int]
    work_id: str
    entity_id: str
    metric_id: str
    delta: float
    new_value: Optional[float]
    is_anchor: bool
    scene_uuid: str
    narrative_order: int
    reason: str
    rule_version: str = "v1"
