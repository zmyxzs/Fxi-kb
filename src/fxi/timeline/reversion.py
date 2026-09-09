"""
fxi.timeline.reversion - 死亡回档、时间循环与轮回快照恢复引擎
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import canonical_json, sha256_hex
from fxi.core.exceptions import NotFoundError, ValidationError
from fxi.storage.sqlite_client import DatabaseClient
from fxi.timeline.dag import CausalDAG


def _ensure_reversion_receipts(db_client: DatabaseClient) -> None:
    """Delegate schema ownership to DatabaseClient migrations."""

    db_client.init_db()


@dataclass
class RollbackReport:
    checkpoint_id: str
    target_physical_time: str
    restored_world_state: dict[str, Any]
    retained_entities: list[str]
    new_loop_causal_event_id: str
    actor_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    successor_of: Optional[str] = None


class TimeReversionManager:
    """轮回存档快照与回档恢复管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.dag = CausalDAG(self.config)
        _ensure_reversion_receipts(self.db_client)

    @staticmethod
    def _checkpoint_event_id(checkpoint_id: str) -> str:
        return f"reversion_checkpoint_{checkpoint_id}"

    @staticmethod
    def _rollback_event_id(
        checkpoint_id: str,
        work_id: str,
        current_narrative_order: int,
        idempotency_key: Optional[str],
    ) -> str:
        if idempotency_key:
            digest = sha256_hex(f"{work_id}\0{idempotency_key}")[:24]
            return f"reversion_{checkpoint_id}_idemp_{digest}"
        return f"reversion_{checkpoint_id}_step_{current_narrative_order}"

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        return sha256_hex(payload)

    @staticmethod
    def _checkpoint_summary(row: Any) -> str:
        return canonical_json(
            {
                "type": "reversion_checkpoint",
                "checkpoint_id": row["checkpoint_id"],
                "chapter_id": row["chapter_id"],
                "successor_of": None,
            },
        )

    def _ensure_checkpoint_event(self, cur: Any, row: Any) -> str:
        event_id = self._checkpoint_event_id(row["checkpoint_id"])
        existing = cur.execute(
            "SELECT work_id, timeline_id, summary FROM causal_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if existing["work_id"] != row["work_id"] or existing["timeline_id"] != row["timeline_id"]:
                raise ValidationError(f"回档 checkpoint 事件 ID 已属于其他作品或时间线: {event_id}")
            try:
                existing_summary = json.loads(existing["summary"])
            except (TypeError, json.JSONDecodeError):
                existing_summary = {}
            if existing_summary.get("type") != "reversion_checkpoint":
                raise ValidationError(f"回档 checkpoint 事件 ID 已对应其他因果事件: {event_id}")
            cur.execute(
                """
                UPDATE causal_events
                SET scene_uuid = ?, narrative_order = ?, physical_time = ?, summary = ?, is_canon = 0, status = 'untouched'
                WHERE event_id = ?
                """,
                (
                    f"sc_{event_id}",
                    row["narrative_order"],
                    row["physical_timestamp"],
                    self._checkpoint_summary(row),
                    event_id,
                ),
            )
            return event_id
        cur.execute(
            """
            INSERT INTO causal_events
            (event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time,
             summary, is_canon, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'untouched')
            """,
            (
                event_id,
                row["work_id"],
                row["timeline_id"],
                f"sc_{event_id}",
                row["narrative_order"],
                row["physical_timestamp"],
                self._checkpoint_summary(row),
            ),
        )
        return event_id

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
        """在重要锚点建立轮回快照存档，并投影为可被 successor 引用的因果节点。"""
        from fxi.storage.sqlite_client import ensure_work

        if not checkpoint_id or not work_id or not timeline_id or not chapter_id:
            raise ValidationError("回档 checkpoint 的 ID、work_id、timeline_id 和 chapter_id 不能为空")
        if not isinstance(narrative_order, int) or narrative_order < 0:
            raise ValidationError("checkpoint narrative_order 不能为负数")
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT INTO reversion_checkpoints
                (checkpoint_id, work_id, timeline_id, chapter_id, narrative_order, physical_timestamp,
                 world_state_json, retained_entities_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    work_id = excluded.work_id,
                    timeline_id = excluded.timeline_id,
                    chapter_id = excluded.chapter_id,
                    narrative_order = excluded.narrative_order,
                    physical_timestamp = excluded.physical_timestamp,
                    world_state_json = excluded.world_state_json,
                    retained_entities_json = excluded.retained_entities_json
                """,
                (
                    checkpoint_id,
                    work_id,
                    timeline_id,
                    chapter_id,
                    narrative_order,
                    physical_timestamp,
                    json.dumps(world_state, ensure_ascii=False, sort_keys=True),
                    json.dumps(retained_entities, ensure_ascii=False),
                )
            )
            row = cur.execute(
                "SELECT * FROM reversion_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            self._ensure_checkpoint_event(cur, row)
        return checkpoint_id

    def _report(
        self,
        checkpoint_id: str,
        physical_time: str,
        world_state: dict[str, Any],
        retained_entities: list[str],
        event_id: str,
        actor_id: Optional[str],
        idempotency_key: Optional[str],
        successor_of: str,
    ) -> RollbackReport:
        return RollbackReport(
            checkpoint_id=checkpoint_id,
            target_physical_time=physical_time,
            restored_world_state=world_state,
            retained_entities=retained_entities,
            new_loop_causal_event_id=event_id,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            successor_of=successor_of,
        )

    def rollback_to_checkpoint(
        self,
        checkpoint_id: str,
        current_narrative_order: int,
        trigger_reason: str = "protagonist_death",
        *,
        actor_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> RollbackReport:
        """执行回档并持久化 checkpoint -> successor 的因果投影；重复键只返回同一操作。"""
        if not checkpoint_id:
            raise ValidationError("checkpoint_id 不能为空")
        if not isinstance(current_narrative_order, int) or current_narrative_order < 0:
            raise ValidationError("current_narrative_order 不能为负数")
        idempotency_key = idempotency_key or None

        with self.db_client.transaction() as cur:
            row = cur.execute(
                "SELECT * FROM reversion_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(f"未找到轮回存档点: {checkpoint_id}")
            if current_narrative_order < row["narrative_order"]:
                raise ValidationError("回档触发点不能早于 checkpoint 的叙事顺序")

            work_id = row["work_id"]
            timeline_id = row["timeline_id"]
            physical_time = row["physical_timestamp"]
            world_state = json.loads(row["world_state_json"] or "{}")
            retained_entities = json.loads(row["retained_entities_json"] or "[]")
            checkpoint_event_id = self._ensure_checkpoint_event(cur, row)
            operation_key = (
                f"idempotency:{idempotency_key}"
                if idempotency_key
                else f"natural:{checkpoint_id}:{current_narrative_order}"
            )
            payload = {
                "work_id": work_id,
                "checkpoint_id": checkpoint_id,
                "timeline_id": timeline_id,
                "current_narrative_order": current_narrative_order,
                "trigger_reason": trigger_reason,
                "actor_id": actor_id,
                "world_state": world_state,
                "retained_entities": retained_entities,
            }
            payload_hash = self._payload_hash(payload)
            receipt = cur.execute(
                """
                SELECT payload_hash, causal_event_id
                FROM reversion_receipts
                WHERE work_id = ? AND operation_key = ?
                """,
                (work_id, operation_key),
            ).fetchone()
            if receipt is not None:
                if receipt["payload_hash"] != payload_hash:
                    raise ValidationError("相同回档幂等键对应了不同载荷")
                projected_event = cur.execute(
                    "SELECT 1 FROM causal_events WHERE event_id = ? AND work_id = ?",
                    (receipt["causal_event_id"], work_id),
                ).fetchone()
                if projected_event is None:
                    raise ValidationError("回档收据存在但 successor 事件投影缺失，不能伪称恢复完成")
                return self._report(
                    checkpoint_id,
                    physical_time,
                    world_state,
                    retained_entities,
                    receipt["causal_event_id"],
                    actor_id,
                    idempotency_key,
                    checkpoint_event_id,
                )

            event_id = self._rollback_event_id(
                checkpoint_id, work_id, current_narrative_order, idempotency_key
            )
            summary = json.dumps(
                {
                    "type": "time_reversion",
                    "checkpoint_id": checkpoint_id,
                    "trigger_reason": trigger_reason,
                    "actor_id": actor_id,
                    "target_physical_time": physical_time,
                    "restored_world_state": world_state,
                    "retained_entities": retained_entities,
                    "successor_of": checkpoint_event_id,
                    "payload_hash": payload_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            existing_event = cur.execute(
                "SELECT work_id, timeline_id, narrative_order, summary FROM causal_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event is not None:
                if (
                    existing_event["work_id"] != work_id
                    or existing_event["timeline_id"] != timeline_id
                    or existing_event["narrative_order"] != current_narrative_order
                    or existing_event["summary"] != summary
                ):
                    raise ValidationError("回档事件 ID 已对应不同的回档载荷")
            else:
                cur.execute(
                    """
                    INSERT INTO causal_events
                    (event_id, work_id, timeline_id, scene_uuid, narrative_order, physical_time,
                     summary, is_canon, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'mutated')
                    """,
                    (
                        event_id,
                        work_id,
                        timeline_id,
                        f"sc_{event_id}",
                        current_narrative_order,
                        physical_time,
                        summary,
                    ),
                )
            link = cur.execute(
                """
                SELECT 1 FROM causal_links
                WHERE work_id = ? AND cause_event_id = ? AND effect_event_id = ?
                """,
                (work_id, checkpoint_event_id, event_id),
            ).fetchone()
            if link is None:
                cur.execute(
                    """
                    INSERT INTO causal_links (work_id, cause_event_id, effect_event_id, link_type)
                    VALUES (?, ?, ?, 'reversion_successor')
                    """,
                    (work_id, checkpoint_event_id, event_id),
                )
            try:
                cur.execute(
                    """
                    INSERT INTO reversion_receipts
                    (work_id, checkpoint_id, operation_key, payload_hash, causal_event_id, created_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (work_id, checkpoint_id, operation_key, payload_hash, event_id),
                )
            except sqlite3.IntegrityError as exc:
                cur.execute(
                    """
                    SELECT payload_hash, causal_event_id
                    FROM reversion_receipts
                    WHERE work_id = ? AND operation_key = ?
                    """,
                    (work_id, operation_key),
                )
                raced = cur.fetchone()
                if raced is None or raced["payload_hash"] != payload_hash:
                    raise ValidationError("回档幂等键已被其他载荷占用") from exc
                event_id = raced["causal_event_id"]

            return self._report(
                checkpoint_id,
                physical_time,
                world_state,
                retained_entities,
                event_id,
                actor_id,
                idempotency_key,
                checkpoint_event_id,
            )
