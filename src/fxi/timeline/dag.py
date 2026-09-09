"""
fxi.timeline.dag - 时空因果有向无环图 (Causal DAG)
"""

from collections import deque
from typing import Optional, Set
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import CausalConflictError, NotFoundError, ValidationError
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
        status: str = "untouched",
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
    ) -> None:
        """注册因果图节点"""
        if not event_id or not work_id or not timeline_id:
            raise ValidationError("event_id、work_id 和 timeline_id 不能为空")
        if narrative_order < 0:
            raise ValidationError("narrative_order 不能为负数")
        if (source_id is None) != (source_version is None):
            raise ValidationError("source_id 与 source_version 必须同时提供")
        if source_id is not None and (not source_id.strip() or not source_version.strip()):
            raise ValidationError("source_id 与 source_version 不能为空")

        with self.db_client.transaction() as cur:
            work = cur.execute("SELECT work_id FROM works WHERE work_id = ?", (work_id,)).fetchone()
            if work is None:
                raise NotFoundError(f"作品未注册: {work_id}")
            cur.execute(
                "SELECT work_id, timeline_id FROM causal_events WHERE event_id = ?",
                (event_id,),
            )
            existing = cur.fetchone()
            if existing and existing["work_id"] != work_id:
                raise ValidationError(
                    f"事件 {event_id} 已归属于作品 {existing['work_id']}，不能改挂到 {work_id}"
                )
            if existing and existing["timeline_id"] != timeline_id:
                raise ValidationError(
                    f"事件 {event_id} 已归属于时间线 {existing['timeline_id']}，不能改挂到 {timeline_id}"
                )

            if existing:
                # 保留旧 event_id 以及已有边；旧实现的 INSERT OR REPLACE 会先删边再重建。
                if source_id is None:
                    cur.execute(
                        """
                        UPDATE causal_events
                        SET scene_uuid = ?, narrative_order = ?, physical_time = ?,
                            summary = ?, is_canon = ?, status = ?
                        WHERE event_id = ?
                        """,
                        (
                            scene_uuid,
                            narrative_order,
                            physical_time,
                            summary,
                            1 if is_canon else 0,
                            status,
                            event_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE causal_events
                        SET source_id = ?, source_version = ?, scene_uuid = ?,
                            narrative_order = ?, physical_time = ?, summary = ?,
                            is_canon = ?, status = ?
                        WHERE event_id = ?
                        """,
                        (
                            source_id.strip(),
                            source_version.strip(),
                            scene_uuid,
                            narrative_order,
                            physical_time,
                            summary,
                            1 if is_canon else 0,
                            status,
                            event_id,
                        ),
                    )
            else:
                cur.execute(
                    """
                    INSERT INTO causal_events
                    (event_id, work_id, timeline_id, source_id, source_version,
                     scene_uuid, narrative_order, physical_time, summary, is_canon, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        work_id,
                        timeline_id,
                        source_id.strip() if source_id is not None else None,
                        source_version.strip() if source_version is not None else None,
                        scene_uuid,
                        narrative_order,
                        physical_time,
                        summary,
                        1 if is_canon else 0,
                        status,
                    ),
                )

    def get_event(self, work_id: str, event_id: str) -> Optional[dict]:
        """获取单个因果事件节点详情"""
        sql = "SELECT * FROM causal_events WHERE work_id = ? AND event_id = ?"
        with self.db_client.get_connection() as conn:
            row = conn.execute(sql, (work_id, event_id)).fetchone()
            return dict(row) if row else None

    def add_causal_link(
        self,
        work_id: str,
        cause_event_id: str,
        effect_event_id: str,
        link_type: str = "direct_cause"
    ) -> None:
        """注册有向因果依赖边: cause -> effect"""
        if cause_event_id == effect_event_id:
            raise CausalConflictError("因果事件不能指向自身")
        if not work_id or not cause_event_id or not effect_event_id:
            raise ValidationError("work_id、cause_event_id 和 effect_event_id 不能为空")

        with self.db_client.transaction() as cur:
            cur.execute(
                """
                SELECT event_id, work_id, timeline_id, narrative_order
                FROM causal_events
                WHERE event_id IN (?, ?)
                """,
                (cause_event_id, effect_event_id),
            )
            events = {row["event_id"]: row for row in cur.fetchall()}
            missing = [event_id for event_id in (cause_event_id, effect_event_id) if event_id not in events]
            if missing:
                raise NotFoundError(f"因果边引用了未注册事件: {', '.join(missing)}")

            cause = events[cause_event_id]
            effect = events[effect_event_id]
            if cause["work_id"] != work_id or effect["work_id"] != work_id:
                raise ValidationError("因果边两端必须属于同一作品和请求 work_id")
            if cause["timeline_id"] != effect["timeline_id"]:
                raise ValidationError("因果边两端必须属于同一时间线")
            if cause["narrative_order"] > effect["narrative_order"]:
                raise CausalConflictError(
                    f"因果边违反叙事顺序: {cause_event_id}({cause['narrative_order']})"
                    f" -> {effect_event_id}({effect['narrative_order']})"
                )

            cur.execute(
                """
                SELECT 1 FROM causal_links
                WHERE work_id = ? AND cause_event_id = ? AND effect_event_id = ?
                LIMIT 1
                """,
                (work_id, cause_event_id, effect_event_id),
            )
            if cur.fetchone():
                raise ValidationError(f"重复因果边: {cause_event_id} -> {effect_event_id}")

            if self._has_path(cur, work_id, effect_event_id, cause_event_id):
                raise CausalConflictError(
                    f"新增因果边 {cause_event_id} -> {effect_event_id} 会形成环"
                )

            cur.execute(
                """
                INSERT INTO causal_links
                (work_id, cause_event_id, effect_event_id, link_type)
                VALUES (?, ?, ?, ?)
                """,
                (work_id, cause_event_id, effect_event_id, link_type)
            )

    @staticmethod
    def _has_path(cur, work_id: str, start_event_id: str, target_event_id: str) -> bool:
        """在当前事务中判断 start -> target 是否已有路径，避免用第二连接读不到未提交边。"""
        queue = deque([start_event_id])
        visited = {start_event_id}
        while queue:
            current = queue.popleft()
            cur.execute(
                """
                SELECT effect_event_id FROM causal_links
                WHERE work_id = ? AND cause_event_id = ?
                """,
                (work_id, current),
            )
            for row in cur.fetchall():
                effect = row["effect_event_id"]
                if effect == target_event_id:
                    return True
                if effect not in visited:
                    visited.add(effect)
                    queue.append(effect)
        return False

    def get_direct_causes(
        self,
        work_id: str,
        event_id: str,
        timeline_id: Optional[str] = None,
    ) -> list[str]:
        """获取直接前置因果事件 ID"""
        sql = """
            SELECT l.cause_event_id
            FROM causal_links AS l
            JOIN causal_events AS c
              ON c.event_id = l.cause_event_id AND c.work_id = l.work_id
            JOIN causal_events AS e
              ON e.event_id = l.effect_event_id AND e.work_id = l.work_id
            WHERE l.work_id = ? AND l.effect_event_id = ?
        """
        params: list[object] = [work_id, event_id]
        if timeline_id is not None:
            sql += " AND c.timeline_id = ? AND e.timeline_id = ?"
            params.extend([timeline_id, timeline_id])
        sql += " ORDER BY l.id ASC"
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, params)
            return [row["cause_event_id"] for row in cur.fetchall()]

    def get_direct_effects(
        self,
        work_id: str,
        event_id: str,
        timeline_id: Optional[str] = None,
    ) -> list[str]:
        """获取直接下游受影响事件 ID"""
        sql = """
            SELECT l.effect_event_id
            FROM causal_links AS l
            JOIN causal_events AS c
              ON c.event_id = l.cause_event_id AND c.work_id = l.work_id
            JOIN causal_events AS e
              ON e.event_id = l.effect_event_id AND e.work_id = l.work_id
            WHERE l.work_id = ? AND l.cause_event_id = ?
        """
        params: list[object] = [work_id, event_id]
        if timeline_id is not None:
            sql += " AND c.timeline_id = ? AND e.timeline_id = ?"
            params.extend([timeline_id, timeline_id])
        sql += " ORDER BY l.id ASC"
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, params)
            return [row["effect_event_id"] for row in cur.fetchall()]

    def get_ancestors(
        self,
        work_id: str,
        event_id: str,
        timeline_id: Optional[str] = None,
    ) -> Set[str]:
        """反向追溯一个事件发生所依赖的所有前置因果集合 (Transitive Ancestors)"""
        ancestors: Set[str] = set()
        queue = deque([event_id])
        visited: Set[str] = {event_id}

        while queue:
            curr = queue.popleft()
            direct = self.get_direct_causes(work_id, curr, timeline_id=timeline_id)
            for cause in direct:
                if cause not in visited:
                    visited.add(cause)
                    ancestors.add(cause)
                    queue.append(cause)

        return ancestors

    def get_descendants(
        self,
        work_id: str,
        event_id: str,
        timeline_id: Optional[str] = None,
    ) -> Set[str]:
        """正向遍历一个事件向下游扩散的所有连锁影响集合 (Transitive Descendants)"""
        descendants: Set[str] = set()
        queue = deque([event_id])
        visited: Set[str] = {event_id}

        while queue:
            curr = queue.popleft()
            direct = self.get_direct_effects(work_id, curr, timeline_id=timeline_id)
            for effect in direct:
                if effect not in visited:
                    visited.add(effect)
                    descendants.add(effect)
                    queue.append(effect)

        return descendants
