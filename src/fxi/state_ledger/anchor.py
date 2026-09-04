"""
fxi.state_ledger.anchor - 基准锚点管理器 (解决中途接入、零历史包袱)
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class AnchorManager:
    """基准锚点管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def create_baseline_anchor(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        baseline_value: float,
        narrative_order: int,
        scene_uuid: str,
        reason: str = "中途接入基准设定"
    ) -> int:
        """
        在指定叙事步长设定基准锚点：
        从此点向前保持 UNMEASURED (零历史包袱)，向后进行确定性结算。
        """
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, entity_id)
            cur.execute(
                """
                INSERT INTO state_events
                (work_id, entity_id, metric_id, delta, new_value, is_anchor, scene_uuid, narrative_order, reason, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, datetime('now'))
                """,
                (
                    work_id,
                    entity_id,
                    metric_id,
                    baseline_value,
                    baseline_value,
                    scene_uuid,
                    narrative_order,
                    reason,
                )
            )
            return cur.lastrowid
