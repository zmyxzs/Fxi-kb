"""
fxi.timeline.reversion - 死亡回档、时间循环与轮回快照恢复引擎
"""

import json
from dataclasses import dataclass
from typing import Any, Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import NotFoundError
from fxi.storage.sqlite_client import DatabaseClient
from fxi.timeline.dag import CausalDAG


@dataclass
class RollbackReport:
    checkpoint_id: str
    target_physical_time: str
    restored_world_state: dict[str, Any]
    retained_entities: list[str]
    new_loop_causal_event_id: str


class TimeReversionManager:
    """轮回存档快照与回档恢复管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.dag = CausalDAG(self.config)

    def create_checkpoint(
        self,
        checkpoint_id: str,
        work_id: str,
        timeline_id: str,
        chapter_id: str,
        narrative_order: int,
        physical_timestamp: str,
        world_state: dict[str, Any],
        retained_entities: list[str]
    ) -> str:
        """在重要锚点建立轮回快照存档"""
        from fxi.storage.sqlite_client import ensure_work
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT OR REPLACE INTO reversion_checkpoints
                (checkpoint_id, work_id, timeline_id, chapter_id, narrative_order, physical_timestamp, world_state_json, retained_entities_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    checkpoint_id,
                    work_id,
                    timeline_id,
                    chapter_id,
                    narrative_order,
                    physical_timestamp,
                    json.dumps(world_state, ensure_ascii=False),
                    json.dumps(retained_entities, ensure_ascii=False),
                )
            )
        return checkpoint_id

    def rollback_to_checkpoint(
        self,
        checkpoint_id: str,
        current_narrative_order: int,
        trigger_reason: str = "protagonist_death"
    ) -> RollbackReport:
        """
        执行时间回溯 (死亡读档 / 轮回启动)：
        1. 读取快照中的 world_state_json；
        2. 恢复客观物理世界（普通 NPC 状态、金币、环境起死回生）；
        3. 保留特权实体 (如主角) 跨轮回记忆与认知；
        4. 在因果图中单调递增建立新事件: (回档触发) -> (新一轮轮回起点)，因果 DAG 永不成环！
        """
        sql = "SELECT * FROM reversion_checkpoints WHERE checkpoint_id = ?"
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (checkpoint_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"未找到轮回存档点: {checkpoint_id}")

            work_id = row["work_id"]
            timeline_id = row["timeline_id"]
            physical_time = row["physical_timestamp"]
            world_state = json.loads(row["world_state_json"] or "{}")
            retained_entities = json.loads(row["retained_entities_json"] or "[]")

        # 建立新一轮轮回的因果节点 (narrative_order 递增)
        new_loop_event_id = f"reversion_{checkpoint_id}_step_{current_narrative_order}"
        self.dag.register_event(
            event_id=new_loop_event_id,
            work_id=work_id,
            scene_uuid=f"sc_{new_loop_event_id}",
            narrative_order=current_narrative_order,
            physical_time=physical_time,
            summary=f"触发时间回档 [{trigger_reason}]，重置客观世界至 {physical_time}，保留特权实体记忆: {retained_entities}",
            timeline_id=timeline_id,
            is_canon=False,
            status="mutated"
        )

        return RollbackReport(
            checkpoint_id=checkpoint_id,
            target_physical_time=physical_time,
            restored_world_state=world_state,
            retained_entities=retained_entities,
            new_loop_causal_event_id=new_loop_event_id,
        )
