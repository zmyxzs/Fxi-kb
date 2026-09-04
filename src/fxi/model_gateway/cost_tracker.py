"""
fxi.model_gateway.cost_tracker - Token 消耗与费用审计记账本
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class CostTracker:
    """模型调用成本审计器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def record_usage(
        self,
        task: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_cny: float = 0.0
    ) -> None:
        """记录一次模型调用用量"""
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                INSERT INTO api_usage_logs (task, model, prompt_tokens, completion_tokens, cost_cny, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (task, model, prompt_tokens, completion_tokens, cost_cny)
            )

    def get_summary(self) -> dict[str, float]:
        """获取总 Token 与开销统计"""
        sql = """
        SELECT
            SUM(prompt_tokens) AS total_prompt_tokens,
            SUM(completion_tokens) AS total_completion_tokens,
            SUM(cost_cny) AS total_cost_cny,
            COUNT(log_id) AS total_calls
        FROM api_usage_logs
        """
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql)
            row = cur.fetchone()
            return {
                "total_prompt_tokens": row["total_prompt_tokens"] or 0,
                "total_completion_tokens": row["total_completion_tokens"] or 0,
                "total_cost_cny": round(row["total_cost_cny"] or 0.0, 4),
                "total_calls": row["total_calls"] or 0,
            }
