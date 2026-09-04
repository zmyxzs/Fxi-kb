"""
fxi.state_ledger.calculator - 动态数值账本与快照结算引擎
"""

from dataclasses import dataclass
from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.types import MetricStatus
from fxi.storage.sqlite_client import DatabaseClient


@dataclass
class BalanceSnapshot:
    work_id: str
    entity_id: str
    metric_id: str
    computed_value: float
    status: MetricStatus
    narrative_order: int


class LedgerCalculator:
    """动态账本加减计算器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def record_event(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        delta: float,
        scene_uuid: str,
        narrative_order: int,
        reason: str
    ) -> int:
        """记录一笔数值变动事件 (正增负减)"""
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, entity_id)
            cur.execute(
                """
                INSERT INTO state_events
                (work_id, entity_id, metric_id, delta, is_anchor, scene_uuid, narrative_order, reason, created_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, datetime('now'))
                """,
                (work_id, entity_id, metric_id, delta, scene_uuid, narrative_order, reason)
            )
            return cur.lastrowid

    def calculate_balance(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        narrative_order: int
    ) -> BalanceSnapshot:
        """
        计算角色在指定叙事节点的准确结余：
        1. 寻找 <= narrative_order 的最近基准锚点 (Anchor)；
        2. 若存在锚点，以锚点值为起点累加后续变动；
        3. 若不存在锚点：
           - 若有变动事件，从 0 开始累加，状态为 EXPLICIT；
           - 若全无事件，返回状态 UNMEASURED；
        4. 若查询节点在最早锚点之前，返回 UNMEASURED (零历史包袱！)。
        """
        with self.db_client.get_connection() as conn:
            # 查找最早的锚点，判定是否处于未测量历史区间
            earliest_anchor_cur = conn.execute(
                """
                SELECT MIN(narrative_order) AS earliest_order
                FROM state_events
                WHERE work_id = ? AND entity_id = ? AND metric_id = ? AND is_anchor = 1
                """,
                (work_id, entity_id, metric_id)
            )
            earliest_row = earliest_anchor_cur.fetchone()
            earliest_order = earliest_row["earliest_order"] if earliest_row else None

            if earliest_order is not None and narrative_order < earliest_order:
                return BalanceSnapshot(
                    work_id=work_id,
                    entity_id=entity_id,
                    metric_id=metric_id,
                    computed_value=0.0,
                    status=MetricStatus.UNMEASURED,
                    narrative_order=narrative_order
                )

            # 寻找 <= 当前步长的最近锚点
            anchor_cur = conn.execute(
                """
                SELECT event_id, new_value, narrative_order
                FROM state_events
                WHERE work_id = ? AND entity_id = ? AND metric_id = ?
                  AND is_anchor = 1 AND narrative_order <= ?
                ORDER BY narrative_order DESC, event_id DESC
                LIMIT 1
                """,
                (work_id, entity_id, metric_id, narrative_order)
            )
            anchor_row = anchor_cur.fetchone()

            base_val = 0.0
            start_event_id = 0
            if anchor_row:
                base_val = float(anchor_row["new_value"] or 0.0)
                start_event_id = anchor_row["event_id"]

            # 累加后续所有普通变动事件
            events_cur = conn.execute(
                """
                SELECT delta
                FROM state_events
                WHERE work_id = ? AND entity_id = ? AND metric_id = ?
                  AND event_id > ? AND narrative_order <= ? AND is_anchor = 0
                ORDER BY narrative_order ASC, event_id ASC
                """,
                (work_id, entity_id, metric_id, start_event_id, narrative_order)
            )
            events = events_cur.fetchall()

            if not anchor_row and not events:
                return BalanceSnapshot(
                    work_id=work_id,
                    entity_id=entity_id,
                    metric_id=metric_id,
                    computed_value=0.0,
                    status=MetricStatus.UNMEASURED,
                    narrative_order=narrative_order
                )

            total = base_val
            for ev in events:
                total += float(ev["delta"])

            return BalanceSnapshot(
                work_id=work_id,
                entity_id=entity_id,
                metric_id=metric_id,
                computed_value=round(total, 2),
                status=MetricStatus.EXPLICIT,
                narrative_order=narrative_order
            )
