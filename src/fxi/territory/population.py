"""
fxi.territory.population - 据点人口与民心动力学
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class TerritoryPopulationManager:
    """人口与民心动力学控制器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def init_territory(
        self,
        work_id: str,
        territory_id: str,
        lord_entity_id: str,
        tier: str = "village",
        population: int = 100,
        soldiers: int = 10,
        loyalty: float = 80.0,
        security: float = 85.0
    ) -> None:
        """初始化据点治理底盘"""
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, lord_entity_id)
            cur.execute(
                """
                INSERT OR REPLACE INTO territory_ledgers
                (territory_id, work_id, lord_entity_id, tier_level, population_total, population_soldiers, loyalty_score, security_score, tax_rate, narrative_order, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.15, 0, datetime('now'))
                """,
                (territory_id, work_id, lord_entity_id, tier, population, soldiers, loyalty, security)
            )

    def update_dynamics(
        self,
        work_id: str,
        territory_id: str,
        pop_delta: int = 0,
        loyalty_delta: float = 0.0,
        security_delta: float = 0.0
    ) -> None:
        """更新人口、民心与治安增减"""
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                UPDATE territory_ledgers
                SET population_total = MAX(0, population_total + ?),
                    loyalty_score = MAX(0.0, MIN(100.0, loyalty_score + ?)),
                    security_score = MAX(0.0, MIN(100.0, security_score + ?)),
                    updated_at = datetime('now')
                WHERE work_id = ? AND territory_id = ?
                """,
                (pop_delta, loyalty_delta, security_delta, work_id, territory_id)
            )
