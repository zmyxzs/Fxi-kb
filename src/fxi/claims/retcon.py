"""
fxi.claims.retcon - 合法吃书引擎 (追溯性设定修改)
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient
from fxi.claims.models import RetconDeclaration


class RetconManager:
    """追溯性设定修正 (吃书) 管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def declare_retcon(self, declaration: RetconDeclaration) -> None:
        """登记合法吃书声明，标记旧主张被替代"""
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, declaration.work_id)
            # 1. 登记吃书记录
            cur.execute(
                """
                INSERT OR REPLACE INTO retcon_declarations
                (retcon_id, work_id, superseded_claim_id, new_claim_id, effective_narrative_order, author_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    declaration.retcon_id,
                    declaration.work_id,
                    declaration.superseded_claim_id,
                    declaration.new_claim_id,
                    declaration.effective_narrative_order,
                    declaration.author_note,
                )
            )

            # 2. 将旧主张状态更新为 superseded
            cur.execute(
                "UPDATE claim_versions SET status = 'superseded' WHERE claim_id = ?",
                (declaration.superseded_claim_id,)
            )

    def is_claim_superseded(self, work_id: str, claim_id: str, narrative_order: int) -> bool:
        """检查某条主张在当前叙事节点是否已被吃书修正"""
        sql = """
        SELECT 1 FROM retcon_declarations
        WHERE work_id = ? AND superseded_claim_id = ? AND effective_narrative_order <= ?
        LIMIT 1
        """
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, claim_id, narrative_order))
            return cur.fetchone() is not None
