"""
fxi.timeline.ripple_analyzer - 蝴蝶效应涟漪扩散分析器 (同人剧情变动影响评估)
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Set
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import NotFoundError, ValidationError
from fxi.core.types import CausalStatus
from fxi.storage.sqlite_client import DatabaseClient
from fxi.timeline.dag import CausalDAG


@dataclass
class RippleImpactTree:
    work_id: str
    divergence_canon_event_id: str
    invalidated_canon_events: list[dict[str, Any]] = field(default_factory=list)
    mutated_canon_events: list[dict[str, Any]] = field(default_factory=list)
    affected_characters: list[str] = field(default_factory=list)
    suggested_alternatives: list[str] = field(default_factory=list)
    mapped_fanfic_event_id: Optional[str] = None


class RippleAnalyzer:
    """蝴蝶效应分析与防吃书预检引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.dag = CausalDAG(self.config)
        self._ensure_mapping_schema()

    def _ensure_mapping_schema(self) -> None:
        """Delegate mapping-table creation and migration to DatabaseClient."""

        self.db_client.init_db()

    def mark_divergence(
        self,
        work_id: str,
        canon_work_id: str,
        canon_event_id: str,
        fanfic_event_id: str,
        fanfic_summary: str,
        narrative_order: int
    ) -> RippleImpactTree:
        """
        在同人作品中标记分歧点，并自动计算下游蝴蝶效应染色
        """
        if work_id == canon_work_id:
            raise ValidationError("同人作品和原著作品必须通过显式作品范围区分")

        with self.db_client.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT event_id, work_id, is_canon
                FROM causal_events
                WHERE work_id = ? AND event_id = ?
                """,
                (canon_work_id, canon_event_id),
            )
            canon_event = cur.fetchone()
            if not canon_event:
                raise NotFoundError(f"未找到原著事件: {canon_work_id}/{canon_event_id}")
            if not canon_event["is_canon"]:
                raise ValidationError(f"分歧源事件必须是原著事件: {canon_event_id}")

        # Validate the explicit mapping before creating the fanfic node so a
        # conflict cannot leave an orphaned event behind.
        with self.db_client.get_connection() as conn:
            existing = conn.execute(
                "SELECT fanfic_event_id FROM timeline_event_mappings WHERE work_id = ? AND canon_work_id = ? AND canon_event_id = ?",
                (work_id, canon_work_id, canon_event_id),
            ).fetchone()
            if existing and existing["fanfic_event_id"] != fanfic_event_id:
                raise ValidationError(f"原著事件 {canon_event_id} 已映射到另一个同人事件 {existing['fanfic_event_id']}")
            reverse = conn.execute(
                "SELECT canon_event_id FROM timeline_event_mappings WHERE work_id = ? AND fanfic_event_id = ?",
                (work_id, fanfic_event_id),
            ).fetchone()
            if reverse and reverse["canon_event_id"] != canon_event_id:
                raise ValidationError(f"同人事件 {fanfic_event_id} 已映射到另一个原著事件")
        # 1. 注册同人事件节点
        self.dag.register_event(
            event_id=fanfic_event_id,
            work_id=work_id,
            scene_uuid=f"sc_{work_id}_{fanfic_event_id}",
            narrative_order=narrative_order,
            physical_time="",
            summary=fanfic_summary,
            is_canon=False,
            status="mutated"
        )

        # 2. 持久化显式事件映射；同一映射可安全重放，不同目标拒绝覆盖。
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                SELECT fanfic_event_id
                FROM timeline_event_mappings
                WHERE work_id = ? AND canon_work_id = ? AND canon_event_id = ?
                """,
                (work_id, canon_work_id, canon_event_id),
            )
            existing = cur.fetchone()
            if existing and existing["fanfic_event_id"] != fanfic_event_id:
                raise ValidationError(
                    f"原著事件 {canon_event_id} 已映射到另一个同人事件 {existing['fanfic_event_id']}"
                )
            cur.execute(
                """
                SELECT canon_event_id
                FROM timeline_event_mappings
                WHERE work_id = ? AND fanfic_event_id = ?
                """,
                (work_id, fanfic_event_id),
            )
            fanfic_mapping = cur.fetchone()
            if fanfic_mapping and fanfic_mapping["canon_event_id"] != canon_event_id:
                raise ValidationError(
                    f"同人事件 {fanfic_event_id} 已映射到另一个原著事件"
                )
            if not existing:
                cur.execute(
                    """
                    INSERT INTO timeline_event_mappings
                    (work_id, canon_work_id, canon_event_id, fanfic_event_id, created_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    """,
                    (work_id, canon_work_id, canon_event_id, fanfic_event_id),
                )

        # 3. 查询原著中被颠覆事件的所有下游因果
        descendants = self.dag.get_descendants(canon_work_id, canon_event_id)

        # 3. 分析下游染色
        impact_tree = RippleImpactTree(
            work_id=work_id,
            divergence_canon_event_id=canon_event_id,
            mapped_fanfic_event_id=fanfic_event_id,
        )

        with self.db_client.get_connection() as conn:
            for down_id in descendants:
                cur = conn.execute(
                    "SELECT event_id, summary, narrative_order FROM causal_events WHERE work_id = ? AND event_id = ?",
                    (canon_work_id, down_id)
                )
                row = cur.fetchone()
                if not row:
                    continue

                event_summary = row["summary"]
                # 若直接因果依赖于原著死者/旧物品，判定为 INVALIDATED
                direct_causes = self.dag.get_direct_causes(canon_work_id, down_id)
                if canon_event_id in direct_causes:
                    impact_tree.invalidated_canon_events.append({
                        "event_id": down_id,
                        "summary": event_summary,
                        "reason": f"直接前置因果依赖于已被篡改的事件 [{canon_event_id}]"
                    })
                    impact_tree.suggested_alternatives.append(
                        f"原著情节【{event_summary}】已不可直接发生，建议重构为因同人变动引发的全新对立冲突。"
                    )
                else:
                    impact_tree.mutated_canon_events.append({
                        "event_id": down_id,
                        "summary": event_summary,
                        "reason": f"间接受波及，核心诱因与参与者关系已异化"
                    })

        return impact_tree

    def check_canon_compatibility(
        self,
        work_id: str,
        canon_work_id: str,
        intended_canon_event_id: str
    ) -> tuple[CausalStatus, str, list[str]]:
        """
        【防吃书预检】：检查同人即将写的原著剧情是否已被前面的蝴蝶效应阻断
        返回: (CausalStatus, 诊断说明, 变异重构建议)
        """
        # 1. 查找 intended_canon_event_id 的所有前置依赖 (Ancestors)
        ancestors = self.dag.get_ancestors(canon_work_id, intended_canon_event_id)

        # 2. 只读取持久化的显式 canon_event_id 映射，不从 ID/摘要做字符串猜测。
        sql = """
        SELECT canon_event_id, fanfic_event_id
        FROM timeline_event_mappings
        WHERE work_id = ? AND canon_work_id = ?
        """
        mapped_events: dict[str, str] = {}
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, canon_work_id))
            for row in cur.fetchall():
                mapped_events[row["canon_event_id"]] = row["fanfic_event_id"]

        direct_causes = self.dag.get_direct_causes(canon_work_id, intended_canon_event_id)

        # 直接映射到目标事件本身也表示该原著事件已被分歧替代。
        if intended_canon_event_id in mapped_events:
            fanfic_event_id = mapped_events[intended_canon_event_id]
            return (
                CausalStatus.INVALIDATED,
                f"原著事件 [{intended_canon_event_id}] 已由同人事件 [{fanfic_event_id}] 替代",
                ["围绕同人事件的后继事实重写该剧情，不得直接复用原著事件。"],
            )

        broken_causes = []
        for c in direct_causes:
            fanfic_event_id = mapped_events.get(c)
            if fanfic_event_id:
                broken_causes.append(
                    f"前置因果 [{c}] 已由同人事件 [{fanfic_event_id}] 改动"
                )

        if broken_causes:
            return (
                CausalStatus.INVALIDATED,
                f"原著情节不可直接沿用！存在前置因果断裂: {'; '.join(broken_causes)}",
                [f"建议围绕同人已改变的新事实重写该剧情冲突，严禁照抄原著。"]
            )

        # 检查间接影响 (Ancestors)
        indirect_affected = [anc for anc in ancestors if anc in mapped_events]

        if indirect_affected:
            return (
                CausalStatus.MUTATED,
                f"该剧情受到上游蝴蝶效应波及 (间接前置已变化: {', '.join(indirect_affected)})",
                ["事件仍可发生，但参与人态度、台词动机或战斗胜负需产生合理异化。"]
            )

        return (
            CausalStatus.UNTOUCHED,
            "原著因果链完好，未受同人历史变动波及，可放心沿用原著基石。",
            []
        )
