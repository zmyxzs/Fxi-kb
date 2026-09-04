"""
fxi.state_ledger.definitions - 状态指标定义
"""

from pydantic import BaseModel
from fxi.core.types import MetricStatus


class MetricDefinition(BaseModel):
    metric_id: str
    work_id: str
    metric_name: str
    unit: str = "点"
    status_type: MetricStatus = MetricStatus.EXPLICIT
    allows_negative: bool = False
