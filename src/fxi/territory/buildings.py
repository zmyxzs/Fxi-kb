"""
fxi.territory.buildings - 据点建筑拓扑与设施管理
"""

from typing import Any, Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class TerritoryBuildingManager:
    """建筑与基础设施管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def register_building(
        self,
        work_id: str,
        territory_id: str,
        building_id: str,
        proto_id: str,
        name: str,
        level: int = 1,
        status: str = "completed",
        narrative_order: int = 0
    ) -> None:
        """注册或更新建筑"""
        from fxi.storage.sqlite_client import ensure_territory
        with self.db_client.transaction() as cur:
            ensure_territory(cur, work_id, territory_id)
            cur.execute(
                """
                INSERT INTO territory_buildings
                (building_id, work_id, territory_id, building_proto_id, name, level, durability, status, assigned_workers, narrative_order)
                VALUES (?, ?, ?, ?, ?, ?, 100.0, ?, 0, ?)
                ON CONFLICT(work_id, territory_id, building_id) DO UPDATE SET
                    building_proto_id = excluded.building_proto_id,
                    name = excluded.name,
                    level = excluded.level,
                    durability = excluded.durability,
                    status = excluded.status,
                    assigned_workers = excluded.assigned_workers,
                    narrative_order = excluded.narrative_order
                """,
                (building_id, work_id, territory_id, proto_id, name, level, status, narrative_order)
            )

    def list_buildings(self, work_id: str, territory_id: str) -> list[dict[str, Any]]:
        """列出据点所有设施"""
        sql = "SELECT * FROM territory_buildings WHERE work_id = ? AND territory_id = ?"
        results = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, territory_id))
            for row in cur.fetchall():
                results.append(dict(row))
        return results
