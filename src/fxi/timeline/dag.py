"""
fxi.timeline.dag - 时空因果有向无环图 (Causal DAG)
"""

from collections import deque
from typing import Optional, Set
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class CausalDAG:
    """基于 SQLite 的因果拓扑图管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def register_event(
        self,
        event_id: str,
        work_id: str,
        scene_uuid: str,
        narrative_order: int,
        physical_time: str,
        summary: str,
        timeline_id: str = "main",
        is_canon: bool = True,
        status: str = "untouched"
    ) -> None:
        """注册因果图节点"""
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT OR REPLACE INTO causal_events
                (event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time, summary, is_canon, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    work_id,
                    timeline_id,
                    scene_uuid,
                    narrative_order,
                    physical_time,
                    summary,
                    1 if is_canon else 0,
                    status,
                )
            )

    def add_causal_link(
        self,
        work_id: str,
        cause_event_id: str,
        effect_event_id: str,
        link_type: str = "direct_cause"
    ) -> None:
        """注册有向因果依赖边: cause -> effect"""
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT INTO causal_links
                (work_id, cause_event_id, effect_event_id, link_type)
                VALUES (?, ?, ?, ?)
                """,
                (work_id, cause_event_id, effect_event_id, link_type)
            )

    def get_direct_causes(self, work_id: str, event_id: str) -> list[str]:
        """获取直接前置因果事件 ID"""
        sql = "SELECT cause_event_id FROM causal_links WHERE work_id = ? AND effect_event_id = ?"
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, event_id))
            return [row["cause_event_id"] for row in cur.fetchall()]

    def get_direct_effects(self, work_id: str, event_id: str) -> list[str]:
        """获取直接下游受影响事件 ID"""
        sql = "SELECT effect_event_id FROM causal_links WHERE work_id = ? AND cause_event_id = ?"
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, event_id))
            return [row["effect_event_id"] for row in cur.fetchall()]

    def get_ancestors(self, work_id: str, event_id: str) -> Set[str]:
        """反向追溯一个事件发生所依赖的所有前置因果集合 (Transitive Ancestors)"""
        ancestors: Set[str] = set()
        queue = deque([event_id])
        visited: Set[str] = {event_id}

        while queue:
            curr = queue.popleft()
            direct = self.get_direct_causes(work_id, curr)
            for cause in direct:
                if cause not in visited:
                    visited.add(cause)
                    ancestors.add(cause)
                    queue.append(cause)

        return ancestors

    def get_descendants(self, work_id: str, event_id: str) -> Set[str]:
        """正向遍历一个事件向下游扩散的所有连锁影响集合 (Transitive Descendants)"""
        descendants: Set[str] = set()
        queue = deque([event_id])
        visited: Set[str] = {event_id}

        while queue:
            curr = queue.popleft()
            direct = self.get_direct_effects(work_id, curr)
            for effect in direct:
                if effect not in visited:
                    visited.add(effect)
                    descendants.add(effect)
                    queue.append(effect)

        return descendants
