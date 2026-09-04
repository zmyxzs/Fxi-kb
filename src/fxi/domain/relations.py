"""
fxi.domain.relations - 实体关系图谱管理器
"""

from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class RelationManager:
    """实体间关系 (师徒、宿敌、盟友、所属) 管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def link(
        self,
        work_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        valid_from_order: int = 0,
        valid_to_order: Optional[int] = None
    ) -> int:
        """建立关系边"""
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                INSERT INTO entity_relations
                (work_id, source_id, target_id, relation_type, valid_from_order, valid_to_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (work_id, source_id, target_id, relation_type, valid_from_order, valid_to_order)
            )
            return cur.lastrowid

    def get_relations(self, work_id: str, entity_id: str, narrative_order: int = 0) -> list[dict[str, Any]]:
        """查询实体在指定时间段的有效关系"""
        sql = """
        SELECT * FROM entity_relations
        WHERE work_id = ? AND (source_id = ? OR target_id = ?)
          AND valid_from_order <= ?
          AND (valid_to_order IS NULL OR valid_to_order >= ?)
        """
        results = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, entity_id, entity_id, narrative_order, narrative_order))
            for row in cur.fetchall():
                results.append(dict(row))
        return results
