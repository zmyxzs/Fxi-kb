"""
fxi.claims.lifecycle - 四级软删除生命周期管控引擎
"""

from typing import Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import ValidationError
from fxi.core.types import LifecycleAction
from fxi.storage.sqlite_client import DatabaseClient


# 只允许已知表和主键列，避免把业务参数拼成 SQL 标识符。
_LIFECYCLE_ALLOWLIST = {
    "claim_versions": "claim_id",
    "causal_events": "event_id",
    "foreshadowing": "seed_id",
    "v2_style_candidates": "candidate_id",
    "v2_proposals": "proposal_id",
    "v2_reviews": "report_id",
}


class LifecycleManager:
    """四级生命周期 (exclude / disable / supersede / purge) 控制器。"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def apply_action(
        self,
        table_name: str,
        id_column: str,
        target_id: str,
        action: LifecycleAction,
        *,
        work_id: Optional[str] = None,
    ) -> bool:
        """执行生命周期状态流转；不存在目标返回 False，SQL/约束失败直接可见。"""
        expected_id_column = _LIFECYCLE_ALLOWLIST.get(table_name)
        if expected_id_column is None or id_column != expected_id_column:
            raise ValidationError("生命周期目标表或主键列不在 allowlist 中")
        if not target_id:
            raise ValidationError("生命周期 target_id 不能为空")
        if work_id is not None and not work_id:
            raise ValidationError("work_id 不能为空字符串")
        try:
            selected_action = action if isinstance(action, LifecycleAction) else LifecycleAction(action)
        except ValueError as exc:
            raise ValidationError(f"未知生命周期 action: {action}") from exc

        where = f"{id_column} = ?"
        params: list[str] = [target_id]
        if work_id is not None:
            where += " AND work_id = ?"
            params.append(work_id)

        with self.db_client.transaction() as cur:
            if selected_action == LifecycleAction.PURGE:
                cur.execute(f"DELETE FROM {table_name} WHERE {where}", params)
            else:
                status = {
                    LifecycleAction.DISABLE: "disabled",
                    LifecycleAction.SUPERSEDE: "superseded",
                    LifecycleAction.EXCLUDE: "excluded",
                }.get(selected_action)
                if status is None:
                    raise ValidationError(f"生命周期 action 不支持状态投影: {selected_action.value}")
                cur.execute(f"UPDATE {table_name} SET status = ? WHERE {where}", [status, *params])
            return cur.rowcount > 0
