"""
fxi.domain.items - 量产原型与唯一神兵二分管理
"""

import json
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class ItemManager:
    """物品与背包资产管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def create_prototype(
        self,
        work_id: str,
        prototype_id: str,
        name: str,
        category: str = "storage",
        specs: Optional[dict[str, Any]] = None
    ) -> None:
        """注册一个通用的量产物品原型 (如 '制式储物袋', '生生造化丹')"""
        specs = specs or {}
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT INTO item_prototypes
                (prototype_id, work_id, name, category, specs_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(work_id, prototype_id) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    specs_json = excluded.specs_json
                """,
                (prototype_id, work_id, name, category, json.dumps(specs, ensure_ascii=False))
            )

    def spawn_instance(
        self,
        work_id: str,
        owner_entity_id: str,
        item_ref_id: str,
        item_type: str = "prototype",
        quantity: int = 1,
        durability: Optional[float] = 100.0,
    ) -> int:
        """为特定角色生成物品实例存入其背包"""
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, owner_entity_id)
            cur.execute(
                """
                INSERT INTO item_instances
                (work_id, owner_entity_id, item_type, item_ref_id, quantity, current_durability, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (work_id, owner_entity_id, item_type, item_ref_id, quantity, durability)
            )
            return cur.lastrowid

    def get_character_inventory(self, work_id: str, owner_entity_id: str) -> list[dict[str, Any]]:
        """获取角色的背包清单"""
        sql = """
        SELECT
            inst.instance_id,
            inst.item_type,
            inst.item_ref_id,
            inst.quantity,
            inst.current_durability,
            COALESCE(proto.name, ent.name, inst.item_ref_id) AS item_name,
            COALESCE(proto.category, ent.category, 'unknown') AS category
        FROM item_instances inst
        LEFT JOIN item_prototypes proto
            ON inst.item_type = 'prototype' AND inst.item_ref_id = proto.prototype_id AND inst.work_id = proto.work_id
        LEFT JOIN entities ent
            ON inst.item_type = 'unique_item' AND inst.item_ref_id = ent.entity_id AND inst.work_id = ent.work_id
        WHERE inst.work_id = ? AND inst.owner_entity_id = ? AND inst.quantity > 0
        """
        results = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, owner_entity_id))
            for row in cur.fetchall():
                results.append({
                    "instance_id": row["instance_id"],
                    "item_name": row["item_name"],
                    "category": row["category"],
                    "item_type": row["item_type"],
                    "item_ref_id": row["item_ref_id"],
                    "quantity": row["quantity"],
                    "durability": row["current_durability"],
                })
        return results

    def consume_instance(self, instance_id: int, quantity: int = 1) -> bool:
        """消耗背包中的物品"""
        with self.db_client.transaction() as cur:
            cur.execute("SELECT quantity FROM item_instances WHERE instance_id = ?", (instance_id,))
            row = cur.fetchone()
            if not row or row["quantity"] < quantity:
                return False

            new_qty = row["quantity"] - quantity
            if new_qty <= 0:
                cur.execute("DELETE FROM item_instances WHERE instance_id = ?", (instance_id,))
            else:
                cur.execute("UPDATE item_instances SET quantity = ? WHERE instance_id = ?", (new_qty, instance_id))
            return True
