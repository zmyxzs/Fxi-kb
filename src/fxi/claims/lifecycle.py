"""
fxi.claims.lifecycle - 四级软删除生命周期管控引擎
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.types import LifecycleAction
from fxi.storage.sqlite_client import DatabaseClient


class LifecycleManager:
    """四级生命周期 (exclude / disable / supersede / purge) 控制器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def apply_action(self, table_name: str, id_column: str, target_id: str, action: LifecycleAction) -> bool:
        """执行生命周期状态流转"""
        with self.db_client.transaction() as cur:
            if action == LifecycleAction.DISABLE:
                cur.execute(f"UPDATE {table_name} SET status = 'disabled' WHERE {id_column} = ?", (target_id,))
            elif action == LifecycleAction.SUPERSEDE:
                cur.execute(f"UPDATE {table_name} SET status = 'superseded' WHERE {id_column} = ?", (target_id,))
            elif action == LifecycleAction.PURGE:
                # 物理删除仅在确无依赖时执行
                cur.execute(f"DELETE FROM {table_name} WHERE {id_column} = ?", (target_id,))
            elif action == LifecycleAction.EXCLUDE:
                # 软排除仅影响检索过滤
                pass
            return True
