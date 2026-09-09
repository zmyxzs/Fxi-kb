"""
fxi.domain.ownership - 唯一神兵归属流转事件追踪
"""

from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import NotFoundError, OwnershipConflictError, ValidationError
from fxi.storage.sqlite_client import DatabaseClient


_TRANSFER_TYPES = {"looted", "gifted", "stolen", "purchased", "destroyed"}
_ACQUISITION_TYPES = {"looted", "purchased"}


class OwnershipTracker:
    """物品流转事件追踪器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    @staticmethod
    def _validate_as_of(as_of: Optional[int]) -> None:
        if as_of is not None and (not isinstance(as_of, int) or as_of < 0):
            raise ValidationError("as_of narrative_order 不能为负数")

    @staticmethod
    def _current_owner_at(cur: Any, work_id: str, item_ref_id: str, as_of: Optional[int] = None) -> Optional[str]:
        where = "work_id = ? AND item_ref_id = ?"
        params: list[Any] = [work_id, item_ref_id]
        if as_of is not None:
            where += " AND narrative_order <= ?"
            params.append(as_of)
        row = cur.execute(
            f"""
            SELECT to_owner_id, transfer_type
            FROM item_ownership_events
            WHERE {where}
            ORDER BY narrative_order DESC, event_id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None or row["transfer_type"] == "destroyed":
            return None
        return row["to_owner_id"]

    @staticmethod
    def _item_exists(cur: Any, work_id: str, item_ref_id: str) -> bool:
        row = cur.execute(
            """
            SELECT 1 FROM entities
            WHERE work_id = ? AND entity_id = ? AND category = 'item'
            LIMIT 1
            """,
            (work_id, item_ref_id),
        ).fetchone()
        return row is not None

    def _validate_item(
        self,
        cur: Any,
        work_id: str,
        item_ref_id: str,
        from_owner_id: Optional[str],
        transfer_type: str,
    ) -> None:
        if self._item_exists(cur, work_id, item_ref_id):
            return
        # 首次 looted/purchased 事件可把新发现的孤品登记为 item；其他路径必须拒绝未知引用。
        if from_owner_id is None and transfer_type in _ACQUISITION_TYPES:
            from fxi.storage.sqlite_client import ensure_entity

            ensure_entity(cur, work_id, item_ref_id, name=item_ref_id, category="item")
            return
        raise NotFoundError(f"作品 {work_id} 中不存在唯一物品: {item_ref_id}")

    @staticmethod
    def _rebuild_unique_projection(cur: Any, work_id: str, item_ref_id: str) -> None:
        row = cur.execute(
            """
            SELECT to_owner_id, transfer_type
            FROM item_ownership_events
            WHERE work_id = ? AND item_ref_id = ?
            ORDER BY narrative_order DESC, event_id DESC
            LIMIT 1
            """,
            (work_id, item_ref_id),
        ).fetchone()
        cur.execute(
            "DELETE FROM item_instances WHERE work_id = ? AND item_ref_id = ? AND item_type = 'unique_item'",
            (work_id, item_ref_id),
        )
        if row is not None and row["to_owner_id"] is not None and row["transfer_type"] != "destroyed":
            from fxi.storage.sqlite_client import ensure_entity

            ensure_entity(cur, work_id, row["to_owner_id"])
            cur.execute(
                """
                INSERT INTO item_instances
                (work_id, owner_entity_id, item_type, item_ref_id, quantity, current_durability, updated_at)
                VALUES (?, ?, 'unique_item', ?, 1, 100.0, datetime('now'))
                """,
                (work_id, row["to_owner_id"], item_ref_id),
            )

    def _record_event(
        self,
        cur: Any,
        work_id: str,
        item_ref_id: str,
        from_owner_id: Optional[str],
        to_owner_id: Optional[str],
        transfer_type: str,
        scene_uuid: str,
        narrative_order: int,
        reason: str,
        quantity: int = 1,
    ) -> None:
        if not work_id or not item_ref_id or not scene_uuid:
            raise ValidationError("ownership 事件的 work_id、item_ref_id 和 scene_uuid 不能为空")
        if transfer_type not in _TRANSFER_TYPES:
            raise ValidationError(f"未知 ownership transfer_type: {transfer_type}")
        if not isinstance(narrative_order, int) or narrative_order < 0:
            raise ValidationError("ownership narrative_order 不能为负数")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValidationError("ownership quantity 必须为正整数")
        if quantity != 1:
            raise ValidationError("unique_item ownership 的 quantity 必须为 1")
        if transfer_type == "destroyed" and to_owner_id is not None:
            raise ValidationError("destroyed ownership 事件不能有 to_owner_id")
        if transfer_type != "destroyed" and to_owner_id is None:
            raise ValidationError("非 destroyed ownership 事件必须有 to_owner_id")

        self._validate_item(cur, work_id, item_ref_id, from_owner_id, transfer_type)
        current_owner = self._current_owner_at(cur, work_id, item_ref_id, narrative_order - 1)
        if from_owner_id != current_owner:
            raise OwnershipConflictError(
                f"物品 {item_ref_id} 归属冲突: 声明原持有者为 {from_owner_id}，"
                f"但叙事顺序 {narrative_order} 前实际持有者为 {current_owner}"
            )
        cur.execute(
            """
            INSERT INTO item_ownership_events
            (
                work_id, item_ref_id, from_owner_id, to_owner_id, transfer_type,
                scene_uuid, narrative_order, reason, created_at
            )
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
            ),
        )
        self._rebuild_unique_projection(cur, work_id, item_ref_id)

    def transfer_ownership(
        self,
        work_id: str,
        item_ref_id: str,
        from_owner_id: Optional[str],
        to_owner_id: Optional[str],
        transfer_type: str,
        scene_uuid: str,
        narrative_order: int,
        reason: str,
        quantity: int = 1,
    ) -> None:
        """记录唯一物品流转；归属校验以该事件叙事顺序之前的状态为准。"""
        with self.db_client.transaction() as cur:
            self._record_event(
                cur,
                work_id,
                item_ref_id,
                from_owner_id,
                to_owner_id,
                transfer_type,
                scene_uuid,
                narrative_order,
                reason,
                quantity,
            )

    def get_current_owner(
        self,
        work_id: str,
        item_ref_id: str,
        as_of: Optional[int] = None,
        *,
        as_of_narrative_order: Optional[int] = None,
    ) -> Optional[str]:
        """获取当前或 as-of 叙事节点的唯一持有者。"""
        if as_of is not None and as_of_narrative_order is not None and as_of != as_of_narrative_order:
            raise ValidationError("as_of 与 as_of_narrative_order 不能同时指定为不同值")
        selected_as_of = as_of if as_of is not None else as_of_narrative_order
        self._validate_as_of(selected_as_of)
        with self.db_client.get_connection() as conn:
            return self._current_owner_at(conn, work_id, item_ref_id, selected_as_of)

    def get_ownership_history(
        self,
        work_id: str,
        item_ref_id: str,
        as_of: Optional[int] = None,
        *,
        as_of_narrative_order: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """获取完整或 as-of 截止点（含该节点）的归属流转历史。"""
        if as_of is not None and as_of_narrative_order is not None and as_of != as_of_narrative_order:
            raise ValidationError("as_of 与 as_of_narrative_order 不能同时指定为不同值")
        selected_as_of = as_of if as_of is not None else as_of_narrative_order
        self._validate_as_of(selected_as_of)
        where = "work_id = ? AND item_ref_id = ?"
        params: list[Any] = [work_id, item_ref_id]
        if selected_as_of is not None:
            where += " AND narrative_order <= ?"
            params.append(selected_as_of)
        with self.db_client.get_connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM item_ownership_events WHERE {where} ORDER BY narrative_order ASC, event_id ASC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def batch_record_ownership(self, events: list[dict[str, Any]]) -> int:
        """批量记录物品归属与流转事件；单个非法事件使整个批次回滚。"""
        if not events:
            return 0
        saved = 0
        with self.db_client.transaction() as cur:
            for event in events:
                self._record_event(
                    cur,
                    event["work_id"],
                    event["item_ref_id"],
                    event.get("from_owner_id"),
                    event.get("to_owner_id"),
                    event.get("transfer_type", "looted"),
                    event.get("scene_uuid", f"sc_{event['item_ref_id']}"),
                    int(event.get("narrative_order", 1)),
                    event.get("reason", ""),
                    event.get("quantity", 1),
                )
                saved += 1
        return saved
