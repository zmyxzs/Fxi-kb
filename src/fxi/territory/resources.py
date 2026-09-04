"""
fxi.territory.resources - 领地/宗门/据点资源日结算与产耗动力学引擎
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import ResourceDeficitError
from fxi.storage.sqlite_client import DatabaseClient


class TerritoryResourceManager:
    """宏观资源存耗与日结算管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def set_resource(
        self,
        work_id: str,
        territory_id: str,
        resource_type: str,
        current_amount: float,
        daily_net_yield: float = 0.0,
        storage_limit: float = 10000.0,
        narrative_order: int = 0
    ) -> None:
        """初始化或更新单项资源"""
        from fxi.storage.sqlite_client import ensure_territory
        with self.db_client.transaction() as cur:
            ensure_territory(cur, work_id, territory_id)
            cur.execute(
                """
                INSERT INTO territory_resources
                (work_id, territory_id, resource_type, current_amount, daily_net_yield, storage_limit, narrative_order, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (work_id, territory_id, resource_type, current_amount, daily_net_yield, storage_limit, narrative_order)
            )

    def tick_daily_settlement(self, work_id: str, territory_id: str, days: int = 1) -> dict[str, float]:
        """按日结算资源净产耗"""
        sql = """
        SELECT id, resource_type, current_amount, daily_net_yield, storage_limit
        FROM territory_resources
        WHERE work_id = ? AND territory_id = ?
        """
        updates = {}
        with self.db_client.transaction() as cur:
            cur.execute(sql, (work_id, territory_id))
            rows = cur.fetchall()
            for r in rows:
                rid = r[0]
                rtype = r[1]
                cur_amt = float(r[2])
                net_yield = float(r[3])
                limit = float(r[4])

                new_amt = max(0.0, min(limit, cur_amt + net_yield * days))
                cur.execute(
                    "UPDATE territory_resources SET current_amount = ?, updated_at = datetime('now') WHERE id = ?",
                    (new_amt, rid)
                )
                updates[rtype] = new_amt

        return updates

    def check_affordability(
        self,
        work_id: str,
        territory_id: str,
        costs: dict[str, float]
    ) -> tuple[bool, list[str]]:
        """检查资源是否足以支持升级/建造/动员"""
        sql = """
        SELECT resource_type, current_amount
        FROM territory_resources
        WHERE work_id = ? AND territory_id = ?
        """
        shortages = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, territory_id))
            stock = {row["resource_type"]: float(row["current_amount"]) for row in cur.fetchall()}

        for rtype, req in costs.items():
            avail = stock.get(rtype, 0.0)
            if avail < req:
                shortages.append(f"{rtype}不足: 需 {req}, 现存 {avail}")

        return len(shortages) == 0, shortages

    def deduct_resources(
        self,
        work_id: str,
        territory_id: str,
        costs: dict[str, float]
    ) -> None:
        """确定性扣除资源，不足则抛出 ResourceDeficitError"""
        is_ok, shortages = self.check_affordability(work_id, territory_id, costs)
        if not is_ok:
            raise ResourceDeficitError(f"据点资源匮乏，操作受阻: {'; '.join(shortages)}")

        with self.db_client.transaction() as cur:
            for rtype, req in costs.items():
                cur.execute(
                    """
                    UPDATE territory_resources
                    SET current_amount = current_amount - ?
                    WHERE work_id = ? AND territory_id = ? AND resource_type = ?
                    """,
                    (req, work_id, territory_id, rtype)
                )
