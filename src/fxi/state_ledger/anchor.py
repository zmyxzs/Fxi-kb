"""
fxi.state_ledger.anchor - 基准锚点管理器 (解决中途接入、零历史包袱)
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.storage.sqlite_client import DatabaseClient


class AnchorManager:
    """基准锚点管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.calculator = LedgerCalculator(self.config)

    def create_baseline_anchor(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        baseline_value: float,
        narrative_order: int,
        scene_uuid: str,
        reason: str = "中途接入基准设定",
        commit_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        knowledge_version: Optional[str] = None,
    ) -> int:
        """
        在指定叙事步长设定基准锚点：
        从此点向前保持 UNMEASURED (零历史包袱)，向后进行确定性结算。
        通过 LedgerCalculator 写入 state_events 并绑定 state_event_receipts 幂等收据。
        """
        return self.calculator.record_event(
            work_id=work_id,
            entity_id=entity_id,
            metric_id=metric_id,
            delta=baseline_value,
            scene_uuid=scene_uuid,
            narrative_order=narrative_order,
            new_value=baseline_value,
            reason=reason,
            is_anchor=True,
            commit_id=commit_id,
            idempotency_key=idempotency_key,
            knowledge_version=knowledge_version,
        )
