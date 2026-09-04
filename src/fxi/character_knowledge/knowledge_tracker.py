"""
fxi.character_knowledge.knowledge_tracker - 角色认知状态与已知事实追踪器
"""

from typing import Optional, Set
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class KnowledgeTracker:
    """角色已知情报集合追踪器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.db_client.transaction() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS character_known_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                learned_narrative_order INTEGER NOT NULL,
                scene_uuid TEXT,
                UNIQUE(work_id, character_id, claim_id)
            );
            """)

    def record_learned_claim(
        self,
        work_id: str,
        character_id: str,
        claim_id: str,
        narrative_order: int,
        scene_uuid: Optional[str] = None
    ) -> None:
        """记录角色获知某条机密/事实"""
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO character_known_claims
                (work_id, character_id, claim_id, learned_narrative_order, scene_uuid)
                VALUES (?, ?, ?, ?, ?)
                """,
                (work_id, character_id, claim_id, narrative_order, scene_uuid)
            )

    def get_known_claims(self, work_id: str, character_id: str, current_order: int) -> Set[str]:
        """获取角色在当前叙事节点已知的所有主张 ID 集合"""
        sql = """
        SELECT claim_id FROM character_known_claims
        WHERE work_id = ? AND character_id = ? AND learned_narrative_order <= ?
        """
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, character_id, current_order))
            return {row["claim_id"] for row in cur.fetchall()}
