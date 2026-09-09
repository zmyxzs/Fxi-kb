"""
fxi.territory.population - 据点人口与民心动力学
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import NotFoundError
from fxi.domain.entities import (
    get_worldview_genre,
    load_work_config,
    select_work_section,
    validate_work_id,
)
from fxi.storage.sqlite_client import DatabaseClient


class TerritoryPopulationManager:
    """人口与民心动力学控制器"""

    _DEFAULTS = {
        "tier": "village",
        "population": 100,
        "soldiers": 10,
        "loyalty": 80.0,
        "security": 85.0,
        "tax_rate": 0.15,
    }

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def init_territory(
        self,
        work_id: str,
        territory_id: str,
        lord_entity_id: str,
        tier: Optional[str] = None,
        population: Optional[int] = None,
        soldiers: Optional[int] = None,
        loyalty: Optional[float] = None,
        security: Optional[float] = None,
        tax_rate: Optional[float] = None,
    ) -> None:
        """初始化据点治理底盘"""
        work_id = validate_work_id(work_id)
        settings, error_code = load_work_config(self.config, work_id)
        configured = dict(self._DEFAULTS)
        if settings is not None:
            genre = get_worldview_genre(settings)
            raw_defaults = select_work_section(settings, "territory_defaults", genre)
            if raw_defaults is None:
                raw_defaults = select_work_section(settings, "population_defaults", genre)
            if raw_defaults is not None and not isinstance(raw_defaults, dict):
                raise ValueError("作品的 territory_defaults 必须是对象")
            if isinstance(raw_defaults, dict):
                aliases = {
                    "population_total": "population",
                    "population_soldiers": "soldiers",
                    "loyalty_score": "loyalty",
                    "security_score": "security",
                }
                for key, value in raw_defaults.items():
                    target = aliases.get(key, key)
                    if target in configured:
                        configured[target] = value
        elif error_code != "WORK_CONFIG_MISSING":
            raise ValueError(f"作品 [{work_id}] 的据点配置不可用: {error_code}")

        values = {
            "tier": configured["tier"] if tier is None else tier,
            "population": configured["population"] if population is None else population,
            "soldiers": configured["soldiers"] if soldiers is None else soldiers,
            "loyalty": configured["loyalty"] if loyalty is None else loyalty,
            "security": configured["security"] if security is None else security,
            "tax_rate": configured["tax_rate"] if tax_rate is None else tax_rate,
        }
        if not isinstance(values["tier"], str) or not values["tier"].strip():
            raise ValueError("据点 tier 必须是非空字符串")
        if any(isinstance(values[key], bool) for key in ("population", "soldiers")):
            raise ValueError("人口与驻军必须是整数")
        try:
            values["population"] = int(values["population"])
            values["soldiers"] = int(values["soldiers"])
            values["loyalty"] = float(values["loyalty"])
            values["security"] = float(values["security"])
            values["tax_rate"] = float(values["tax_rate"])
        except (TypeError, ValueError) as exc:
            raise ValueError("据点初始化参数必须是数值") from exc
        if values["population"] < 0 or values["soldiers"] < 0 or values["soldiers"] > values["population"]:
            raise ValueError("驻军人数必须在总人口范围内")
        if not 0.0 <= values["loyalty"] <= 100.0 or not 0.0 <= values["security"] <= 100.0:
            raise ValueError("民心与治安必须在 0 到 100 之间")
        if not 0.0 <= values["tax_rate"] <= 1.0:
            raise ValueError("税率必须在 0 到 1 之间")
        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, lord_entity_id)
            cur.execute(
                """
                INSERT INTO territory_ledgers
                (territory_id, work_id, lord_entity_id, tier_level, population_total, population_soldiers, loyalty_score, security_score, tax_rate, narrative_order, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))
                ON CONFLICT(work_id, territory_id) DO UPDATE SET
                    lord_entity_id = excluded.lord_entity_id,
                    tier_level = excluded.tier_level,
                    population_total = excluded.population_total,
                    population_soldiers = excluded.population_soldiers,
                    loyalty_score = excluded.loyalty_score,
                    security_score = excluded.security_score,
                    tax_rate = excluded.tax_rate,
                    narrative_order = excluded.narrative_order,
                    updated_at = excluded.updated_at
                """,
                (
                    territory_id,
                    work_id,
                    lord_entity_id,
                    values["tier"].strip(),
                    values["population"],
                    values["soldiers"],
                    values["loyalty"],
                    values["security"],
                    values["tax_rate"],
                )
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
        work_id = validate_work_id(work_id)
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
            if cur.rowcount != 1:
                raise NotFoundError(f"作品 [{work_id}] 未找到据点 [{territory_id}]")
