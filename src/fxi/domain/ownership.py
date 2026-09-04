"""
fxi.domain.ownership - 唯一神兵归属流转事件追踪
"""

from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import OwnershipConflictError
from fxi.storage.sqlite_client import DatabaseClient


class OwnershipTracker:
    """神兵法宝流转事件追踪器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def transfer_ownership(
        self,
        work_id: str,
        item_ref_id: str,
        from_owner_id: Optional[str],
        to_owner_id: Optional[str],
        transfer_type: str,
        scene_uuid: str,
        narrative_order: int,
        reason: str
    ) -> None:
        """
        记录神兵归属流转事件 (looted | gifted | stolen | purchased | destroyed)
        并更新持有状态
        """
        current_owner = self.get_current_owner(work_id, item_ref_id)

        # 若指明了原持有者，则必须与当前实际持有者匹配 (防止凭空转移穿帮)
        if from_owner_id is not None and current_owner != from_owner_id:
            raise OwnershipConflictError(
                f"物品 {item_ref_id} 归属冲突: 声明原持有者为 {from_owner_id}，但当前实际持有者为 {current_owner}"
            )

        with self.db_client.transaction() as cur:
            # 1. 记录不可变历史事件
            cur.execute(
                """
                INSERT INTO item_ownership_events
                (work_id, item_ref_id, from_owner_id, to_owner_id, transfer_type, scene_uuid, narrative_order, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    work_id,
                    item_ref_id,
                    from_owner_id,
                    to_owner_id,
                    transfer_type,
                    scene_uuid,
                    narrative_order,
                    reason,
                )
            )

            # 2. 同步更新 item_instances
            cur.execute(
                "DELETE FROM item_instances WHERE work_id = ? AND item_ref_id = ? AND item_type = 'unique_item'",
                (work_id, item_ref_id)
            )

            if to_owner_id is not None and transfer_type != "destroyed":
                from fxi.storage.sqlite_client import ensure_entity
                ensure_entity(cur, work_id, to_owner_id)
                cur.execute(
                    """
                    INSERT INTO item_instances
                    (work_id, owner_entity_id, item_type, item_ref_id, quantity, current_durability, updated_at)
                    VALUES (?, ?, 'unique_item', ?, 1, 100.0, datetime('now'))
                    """,
                    (work_id, to_owner_id, item_ref_id)
                )

    def get_current_owner(self, work_id: str, item_ref_id: str) -> Optional[str]:
        """获取神兵当前的唯一持有者"""
        sql = """
        SELECT to_owner_id, transfer_type
        FROM item_ownership_events
        WHERE work_id = ? AND item_ref_id = ?
        ORDER BY narrative_order DESC, event_id DESC
        LIMIT 1
        """
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, item_ref_id))
            row = cur.fetchone()
            if not row:
                return None
            if row["transfer_type"] == "destroyed":
                return None
            return row["to_owner_id"]

    def get_ownership_history(self, work_id: str, item_ref_id: str) -> list[dict[str, Any]]:
        """获取神兵的完整归属流转历史"""
        sql = """
        SELECT * FROM item_ownership_events
        WHERE work_id = ? AND item_ref_id = ?
        ORDER BY narrative_order ASC, event_id ASC
        """
        results = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, item_ref_id))
            for row in cur.fetchall():
                results.append(dict(row))
        return results
