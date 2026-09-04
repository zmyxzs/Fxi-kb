"""
fxi.timeline.ripple_analyzer - 蝴蝶效应涟漪扩散分析器 (同人剧情变动影响评估)
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Set
from fxi.core.config import FxiConfig, load_config
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


class RippleAnalyzer:
    """蝴蝶效应分析与防吃书预检引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.dag = CausalDAG(self.config)

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

        # 2. 查询原著中被颠覆事件的所有下游因果
        descendants = self.dag.get_descendants(canon_work_id, canon_event_id)

        # 3. 分析下游染色
        impact_tree = RippleImpactTree(
            work_id=work_id,
            divergence_canon_event_id=canon_event_id
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

        # 2. 检查这些前置依赖是否有任何一个在同人作品中被标记为分歧/阻断
        sql = """
        SELECT event_id, summary FROM causal_events
        WHERE work_id = ? AND is_canon = 0
        """
        fanfic_divergences = {}
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id,))
            for row in cur.fetchall():
                fanfic_divergences[row["event_id"]] = row["summary"]

        direct_causes = self.dag.get_direct_causes(canon_work_id, intended_canon_event_id)

        # 比对
        broken_causes = []
        for c in direct_causes:
            # 检查是否有对应同人改动颠覆了 c
            for f_id, f_desc in fanfic_divergences.items():
                if c in f_id or c in f_desc:
                    broken_causes.append(f"前置因果 [{c}] 已在同人中被改动: {f_desc}")

        if broken_causes:
            return (
                CausalStatus.INVALIDATED,
                f"原著情节不可直接沿用！存在前置因果断裂: {'; '.join(broken_causes)}",
                [f"建议围绕同人已改变的新事实重写该剧情冲突，严禁照抄原著。"]
            )

        # 检查间接影响 (Ancestors)
        indirect_affected = []
        for anc in ancestors:
            for f_id, f_desc in fanfic_divergences.items():
                if anc in f_id or anc in f_desc:
                    indirect_affected.append(anc)

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
