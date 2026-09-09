"""
fxi.claims.retcon - 合法吃书引擎 (追溯性设定修改)
"""

from typing import Optional

from fxi.claims.models import RetconDeclaration
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import ValidationError
from fxi.storage.sqlite_client import DatabaseClient


class RetconManager:
    """追溯性设定修正 (吃书) 管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    @staticmethod
    def _assert_claims_in_work(cur, work_id: str, *claim_ids: str) -> None:
        for claim_id in claim_ids:
            if not claim_id:
                continue
            rows = cur.execute(
                "SELECT DISTINCT work_id FROM claim_versions WHERE claim_id = ?",
                (claim_id,),
            ).fetchall()
            if any(row["work_id"] is not None and row["work_id"] != work_id for row in rows):
                raise ValidationError(f"主张 {claim_id} 属于其他作品，不能跨 work retcon")

    def declare_retcon(self, declaration: RetconDeclaration) -> None:
        """登记合法吃书声明；声明和被修正主张始终限定在同一 work。"""
        from fxi.storage.sqlite_client import ensure_work

        if not declaration.work_id or not declaration.retcon_id:
            raise ValidationError("retcon_id 和 work_id 不能为空")
        if not isinstance(declaration.effective_narrative_order, int) or declaration.effective_narrative_order < 0:
            raise ValidationError("effective_narrative_order 不能为负数")
        with self.db_client.transaction() as cur:
            ensure_work(cur, declaration.work_id)
            self._assert_claims_in_work(
                cur,
                declaration.work_id,
                declaration.superseded_claim_id,
                declaration.new_claim_id,
            )
            existing = cur.execute(
                "SELECT * FROM retcon_declarations WHERE retcon_id = ?",
                (declaration.retcon_id,),
            ).fetchone()
            if existing is not None:
                same = (
                    existing["work_id"] == declaration.work_id
                    and existing["superseded_claim_id"] == declaration.superseded_claim_id
                    and existing["new_claim_id"] == declaration.new_claim_id
                    and existing["effective_narrative_order"] == declaration.effective_narrative_order
                    and existing["author_note"] == declaration.author_note
                )
                if same:
                    return
                raise ValidationError("retcon_id 已对应不同的声明载荷")
            cur.execute(
                """
                INSERT INTO retcon_declarations
                (
                    retcon_id, work_id, superseded_claim_id, new_claim_id,
                    effective_narrative_order, author_note, created_at
                )
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

    def is_claim_superseded(self, work_id: str, claim_id: str, narrative_order: int) -> bool:
        """检查某条主张在当前叙事节点是否已被吃书修正。"""
        if not work_id or not claim_id:
            raise ValidationError("work_id 和 claim_id 不能为空")
        if not isinstance(narrative_order, int) or narrative_order < 0:
            raise ValidationError("narrative_order 不能为负数")
        sql = """
        SELECT 1 FROM retcon_declarations
        WHERE work_id = ? AND superseded_claim_id = ? AND effective_narrative_order <= ?
        LIMIT 1
        """
        with self.db_client.get_connection() as conn:
            return conn.execute(sql, (work_id, claim_id, narrative_order)).fetchone() is not None
