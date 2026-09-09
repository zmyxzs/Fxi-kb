"""
fxi.state_ledger.calculator - 动态数值账本与快照结算引擎
"""

import math
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import canonical_json, sha256_hex
from fxi.core.exceptions import ValidationError
from fxi.core.types import MetricStatus
from fxi.state_ledger.definitions import MetricDefinition
from fxi.storage.sqlite_client import DatabaseClient


def ensure_state_event_receipts(db_client: DatabaseClient) -> None:
    """Delegate schema ownership to DatabaseClient migrations."""

    db_client.init_db()


def _state_event_payload_hash(payload: dict[str, Any]) -> str:
    return sha256_hex(payload)


def _state_event_slot(
    entity_id: str,
    metric_id: str,
    scene_uuid: str,
    narrative_order: int,
    is_anchor: bool,
) -> str:
    return canonical_json(
        {
            "entity_id": entity_id,
            "metric_id": metric_id,
            "scene_uuid": scene_uuid,
            "narrative_order": narrative_order,
            "is_anchor": bool(is_anchor),
        },
    )


def record_state_event(
    db_client: DatabaseClient,
    work_id: str,
    entity_id: str,
    metric_id: str,
    delta: float,
    scene_uuid: str,
    narrative_order: int,
    reason: str,
    *,
    is_anchor: bool = False,
    new_value: Optional[float] = None,
    commit_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    knowledge_version: Optional[str] = None,
    rule_version: str = "v1",
) -> int:
    """原子写入状态事件，并用 commit/idempotency 收据阻止重复消费。"""
    from fxi.storage.sqlite_client import ensure_entity

    if not work_id or not entity_id or not metric_id or not scene_uuid:
        raise ValidationError("状态事件的 work_id、entity_id、metric_id 和 scene_uuid 不能为空")
    if not isinstance(narrative_order, int) or narrative_order < 0:
        raise ValidationError("状态事件 narrative_order 不能为负数")
    if not rule_version or not isinstance(rule_version, str):
        raise ValidationError("状态事件 rule_version 不能为空")
    try:
        delta = float(delta)
        if not math.isfinite(delta):
            raise ValueError
        if new_value is not None:
            new_value = float(new_value)
            if not math.isfinite(new_value):
                raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValidationError("状态事件数值必须是有限数字") from exc

    commit_id = commit_id or None
    idempotency_key = idempotency_key or None
    stored_version = knowledge_version if knowledge_version is not None else commit_id
    event_slot = _state_event_slot(entity_id, metric_id, scene_uuid, narrative_order, is_anchor)
    payload_hash = _state_event_payload_hash(
        {
            "work_id": work_id,
            "entity_id": entity_id,
            "metric_id": metric_id,
            "delta": float(delta),
            "new_value": new_value,
            "is_anchor": bool(is_anchor),
            "scene_uuid": scene_uuid,
            "narrative_order": narrative_order,
            "reason": reason,
            "commit_id": commit_id,
            "knowledge_version": stored_version,
            "rule_version": rule_version,
        }
    )

    with db_client.transaction() as cur:
        ensure_entity(cur, work_id, entity_id)
        metric_definition = _metric_definition(cur, work_id, metric_id)
        matches = []
        if idempotency_key:
            cur.execute(
                """
                SELECT state_event_id, payload_hash
                FROM state_event_receipts
                WHERE work_id = ? AND idempotency_key = ?
                """,
                (work_id, idempotency_key),
            )
            row = cur.fetchone()
            if row:
                matches.append(row)
        if commit_id:
            cur.execute(
                """
                SELECT state_event_id, payload_hash
                FROM state_event_receipts
                WHERE work_id = ? AND commit_id = ? AND event_slot = ?
                """,
                (work_id, commit_id, event_slot),
            )
            row = cur.fetchone()
            if row and all(item["state_event_id"] != row["state_event_id"] for item in matches):
                matches.append(row)

        if matches:
            if any(row["payload_hash"] != payload_hash for row in matches):
                raise ValidationError("相同 commit/idempotency 标识对应了不同状态事件载荷")
            event_ids = {row["state_event_id"] for row in matches}
            if len(event_ids) != 1:
                raise ValidationError("commit_id 与 idempotency_key 指向了不同状态事件")
            return next(iter(event_ids))

        cur.execute(
            """
            INSERT INTO state_events
            (work_id, entity_id, metric_id, delta, new_value, is_anchor, scene_uuid,
             narrative_order, reason, rule_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                work_id,
                entity_id,
                metric_id,
                delta,
                new_value,
                1 if is_anchor else 0,
                scene_uuid,
                narrative_order,
                reason,
                rule_version,
            ),
        )
        state_event_id = int(cur.lastrowid)
        if (
            metric_definition is not None
            and metric_definition["status_type"] == MetricStatus.EXPLICIT
            and not metric_definition["allows_negative"]
        ):
            candidate, _ = _compute_balance(
                cur,
                work_id,
                entity_id,
                metric_id,
                narrative_order,
                rule_version=rule_version,
            )
            if candidate.computed_value is not None and candidate.computed_value < 0:
                raise ValidationError(f"状态指标 {metric_id} 不允许结余为负数")
        try:
            cur.execute(
                """
                INSERT INTO state_event_receipts
                (work_id, entity_id, metric_id, event_slot, commit_id, idempotency_key,
                 knowledge_version, payload_hash, state_event_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    work_id,
                    entity_id,
                    metric_id,
                    event_slot,
                    commit_id,
                    idempotency_key,
                    stored_version,
                    payload_hash,
                    state_event_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("状态事件幂等键已被并发请求占用") from exc
        return state_event_id


@dataclass
class BalanceSnapshot:
    work_id: str
    entity_id: str
    metric_id: str
    computed_value: Optional[float]
    status: MetricStatus
    narrative_order: int


def _metric_status(value: Any) -> MetricStatus:
    try:
        return value if isinstance(value, MetricStatus) else MetricStatus(str(value))
    except ValueError as exc:
        raise ValidationError(f"未知状态指标 status_type: {value}") from exc


def _metric_definition(cur: Any, work_id: str, metric_id: str) -> Optional[dict[str, Any]]:
    row = cur.execute(
        """
        SELECT status_type, allows_negative, rule_version
        FROM state_metrics
        WHERE work_id = ? AND metric_id = ?
        """,
        (work_id, metric_id),
    ).fetchone()
    if row is None:
        return None
    return {
        "status_type": _metric_status(row["status_type"]),
        "allows_negative": bool(row["allows_negative"]),
        "rule_version": str(row["rule_version"] or "v1"),
    }


def _snapshot_status_value(snapshot: BalanceSnapshot) -> Optional[float]:
    return snapshot.computed_value if snapshot.status == MetricStatus.EXPLICIT else None


def _compute_balance(
    cur: Any,
    work_id: str,
    entity_id: str,
    metric_id: str,
    narrative_order: int,
    *,
    knowledge_version: Optional[str] = None,
    rule_version: Optional[str] = None,
) -> tuple[BalanceSnapshot, Optional[int]]:
    """在给定连接或事务游标上按叙事顺序计算，不把自增 event_id 当作顺序。"""
    definition = _metric_definition(cur, work_id, metric_id)
    if rule_version is None and definition is not None:
        rule_version = definition["rule_version"]
    status_type = definition["status_type"] if definition else MetricStatus.EXPLICIT
    if status_type == MetricStatus.NOT_APPLICABLE:
        return BalanceSnapshot(work_id, entity_id, metric_id, None, status_type, narrative_order), None
    if status_type == MetricStatus.UNMEASURED:
        return BalanceSnapshot(work_id, entity_id, metric_id, None, status_type, narrative_order), None

    def event_filters(alias: str) -> tuple[str, list[Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if knowledge_version is not None:
            filters.append(
                f"EXISTS (SELECT 1 FROM state_event_receipts AS r "
                f"WHERE r.state_event_id = {alias}.event_id AND r.knowledge_version = ?)"
            )
            params.append(knowledge_version)
        if rule_version is not None:
            filters.append(f"{alias}.rule_version = ?")
            params.append(rule_version)
        return (" AND " + " AND ".join(filters)) if filters else "", params

    anchor_filter, anchor_params = event_filters("e")
    anchor_row = cur.execute(
        """
        SELECT e.event_id, e.new_value, e.narrative_order
        FROM state_events AS e
        WHERE e.work_id = ? AND e.entity_id = ? AND e.metric_id = ?
          AND e.is_anchor = 1 AND e.narrative_order <= ?
        """ + anchor_filter + """
        ORDER BY e.narrative_order DESC, e.event_id DESC
        LIMIT 1
        """,
        (work_id, entity_id, metric_id, narrative_order, *anchor_params),
    ).fetchone()

    earliest_row = cur.execute(
        """
        SELECT MIN(e.narrative_order) AS earliest_order
        FROM state_events AS e
        WHERE e.work_id = ? AND e.entity_id = ? AND e.metric_id = ?
          AND e.is_anchor = 1
        """ + anchor_filter,
        (work_id, entity_id, metric_id, *anchor_params),
    ).fetchone()
    earliest_order = earliest_row["earliest_order"] if earliest_row else None
    if earliest_order is not None and narrative_order < earliest_order:
        return BalanceSnapshot(work_id, entity_id, metric_id, None, MetricStatus.UNMEASURED, narrative_order), None

    event_filter, event_params = event_filters("e")
    event_where = """
        SELECT e.event_id, e.delta
        FROM state_events AS e
        WHERE e.work_id = ? AND e.entity_id = ? AND e.metric_id = ?
          AND e.narrative_order <= ? AND e.is_anchor = 0
    """
    params: list[Any] = [work_id, entity_id, metric_id, narrative_order]
    if anchor_row is not None:
        event_where += """
          AND (
              e.narrative_order > ?
              OR (e.narrative_order = ? AND e.event_id > ?)
          )
        """
        params.extend(
            [anchor_row["narrative_order"], anchor_row["narrative_order"], anchor_row["event_id"]]
        )
    events = cur.execute(
        event_where + event_filter + " ORDER BY e.narrative_order ASC, e.event_id ASC",
        (*params, *event_params),
    ).fetchall()

    if anchor_row is None and not events:
        return BalanceSnapshot(work_id, entity_id, metric_id, None, MetricStatus.UNMEASURED, narrative_order), None

    total = float(anchor_row["new_value"] or 0.0) if anchor_row is not None else 0.0
    based_on_event_id = anchor_row["event_id"] if anchor_row is not None else None
    for event in events:
        total += float(event["delta"])
        based_on_event_id = event["event_id"]
    total = round(total, 2)
    if definition and not definition["allows_negative"] and total < 0:
        raise ValidationError(f"状态指标 {metric_id} 不允许结余为负数")
    return (
        BalanceSnapshot(work_id, entity_id, metric_id, total, MetricStatus.EXPLICIT, narrative_order),
        based_on_event_id,
    )


def _persist_snapshot(cur: Any, snapshot: BalanceSnapshot, based_on_event_id: Optional[int]) -> None:
    cur.execute(
        """
        INSERT INTO state_snapshots
        (work_id, entity_id, metric_id, narrative_order, computed_value, status, based_on_event_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(work_id, entity_id, metric_id, narrative_order) DO UPDATE SET
            computed_value = excluded.computed_value,
            status = excluded.status,
            based_on_event_id = excluded.based_on_event_id,
            updated_at = excluded.updated_at
        """,
        (
            snapshot.work_id,
            snapshot.entity_id,
            snapshot.metric_id,
            snapshot.narrative_order,
            _snapshot_status_value(snapshot),
            snapshot.status.value,
            based_on_event_id,
        ),
    )


def _existing_replay_rule_version(
    cur: Any,
    work_id: str,
    entity_id: str,
    metric_id: str,
    scene_uuid: str,
    narrative_order: int,
    is_anchor: bool,
    commit_id: Optional[str],
    idempotency_key: Optional[str],
) -> Optional[str]:
    """读取同一幂等事件的持久化规则版本；冲突时交给写入层报错。"""
    rows = []
    if idempotency_key:
        rows.extend(
            cur.execute(
                """
                SELECT r.state_event_id, e.rule_version
                FROM state_event_receipts AS r
                JOIN state_events AS e ON e.event_id = r.state_event_id
                WHERE r.work_id = ? AND r.idempotency_key = ?
                """,
                (work_id, idempotency_key),
            ).fetchall()
        )
    if commit_id:
        event_slot = _state_event_slot(entity_id, metric_id, scene_uuid, narrative_order, is_anchor)
        rows.extend(
            cur.execute(
                """
                SELECT r.state_event_id, e.rule_version
                FROM state_event_receipts AS r
                JOIN state_events AS e ON e.event_id = r.state_event_id
                WHERE r.work_id = ? AND r.commit_id = ? AND r.event_slot = ?
                """,
                (work_id, commit_id, event_slot),
            ).fetchall()
        )
    event_ids = {row["state_event_id"] for row in rows}
    if len(event_ids) != 1:
        return None
    rule_versions = {str(row["rule_version"] or "v1") for row in rows}
    if len(rule_versions) != 1:
        return None
    return next(iter(rule_versions))


class LedgerCalculator:
    """动态账本加减计算器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self._metric_rule_versions: dict[tuple[str, str], str] = {}
        ensure_state_event_receipts(self.db_client)

    def register_metric(self, definition: MetricDefinition) -> None:
        """登记或更新指标定义；定义表是 allows_negative/status_type 的权威来源。"""
        from fxi.storage.sqlite_client import ensure_work

        if not definition.work_id or not definition.metric_id or not definition.metric_name:
            raise ValidationError("指标定义的 work_id、metric_id 和 metric_name 不能为空")
        if not definition.rule_version:
            raise ValidationError("指标定义 rule_version 不能为空")
        status_type = _metric_status(definition.status_type)
        with self.db_client.transaction() as cur:
            ensure_work(cur, definition.work_id)
            cur.execute(
                """
                INSERT INTO state_metrics
                (work_id, metric_id, metric_name, unit, status_type, allows_negative, rule_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_id, metric_id) DO UPDATE SET
                    metric_name = excluded.metric_name,
                    unit = excluded.unit,
                    status_type = excluded.status_type,
                    allows_negative = excluded.allows_negative,
                    rule_version = excluded.rule_version
                """,
                (
                    definition.work_id,
                    definition.metric_id,
                    definition.metric_name,
                    definition.unit,
                    status_type.value,
                    1 if definition.allows_negative else 0,
                    definition.rule_version,
                ),
            )
        self._metric_rule_versions[(definition.work_id, definition.metric_id)] = definition.rule_version

    def _resolve_rule_version(
        self,
        work_id: str,
        metric_id: str,
        requested_rule_version: Optional[str],
        *,
        entity_id: Optional[str] = None,
        scene_uuid: Optional[str] = None,
        narrative_order: Optional[int] = None,
        is_anchor: bool = False,
        commit_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        if requested_rule_version is not None:
            return requested_rule_version

        replay_rule_version: Optional[str] = None
        with self.db_client.transaction() as cur:
            if (
                (commit_id or idempotency_key)
                and entity_id is not None
                and scene_uuid is not None
                and narrative_order is not None
            ):
                replay_rule_version = _existing_replay_rule_version(
                    cur,
                    work_id,
                    entity_id,
                    metric_id,
                    scene_uuid,
                    narrative_order,
                    is_anchor,
                    commit_id,
                    idempotency_key,
                )
            if replay_rule_version is not None:
                return replay_rule_version
            if replay_rule_version is None:
                row = cur.execute(
                    "SELECT rule_version FROM state_metrics WHERE work_id = ? AND metric_id = ?",
                    (work_id, metric_id),
                ).fetchone()
                if row is not None:
                    return str(row["rule_version"] or "v1")

        return self._metric_rule_versions.get((work_id, metric_id), "v1")

    def record_event(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        delta: float,
        scene_uuid: str,
        narrative_order: int,
        reason: str,
        commit_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        knowledge_version: Optional[str] = None,
        is_anchor: bool = False,
        new_value: Optional[float] = None,
        rule_version: Optional[str] = None,
    ) -> int:
        """记录一笔数值变动事件 (正增负减)"""
        selected_rule_version = self._resolve_rule_version(
            work_id,
            metric_id,
            rule_version,
            entity_id=entity_id,
            scene_uuid=scene_uuid,
            narrative_order=narrative_order,
            is_anchor=is_anchor,
            commit_id=commit_id,
            idempotency_key=idempotency_key,
        )
        return record_state_event(
            self.db_client,
            work_id,
            entity_id,
            metric_id,
            delta,
            scene_uuid,
            narrative_order,
            reason,
            is_anchor=is_anchor,
            new_value=new_value,
            commit_id=commit_id,
            idempotency_key=idempotency_key,
            knowledge_version=knowledge_version,
            rule_version=selected_rule_version,
        )

    def calculate_balance(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        narrative_order: int,
        knowledge_version: Optional[str] = None,
        version: Optional[str] = None,
        rule_version: Optional[str] = None,
    ) -> BalanceSnapshot:
        """
        计算角色在指定叙事节点的准确结余：
        1. 寻找 <= narrative_order 的最近基准锚点 (Anchor)；
        2. 若存在锚点，以锚点值为起点累加后续变动；
        3. 若不存在锚点：
           - 若有变动事件，从 0 开始累加，状态为 EXPLICIT；
           - 若全无事件，返回状态 UNMEASURED；
        4. 若查询节点在最早锚点之前，返回 UNMEASURED (零历史包袱！)。
        """
        if not isinstance(narrative_order, int) or narrative_order < 0:
            raise ValidationError("查询 narrative_order 不能为负数")
        if knowledge_version is not None and version is not None and knowledge_version != version:
            raise ValidationError("knowledge_version 与 version 不能同时指定为不同值")
        selected_knowledge_version = knowledge_version if knowledge_version is not None else version
        selected_rule_version = self._resolve_rule_version(work_id, metric_id, rule_version)
        with self.db_client.get_connection() as conn:
            snapshot, based_on_event_id = _compute_balance(
                conn,
                work_id,
                entity_id,
                metric_id,
                narrative_order,
                knowledge_version=selected_knowledge_version,
                rule_version=selected_rule_version,
            )
            _persist_snapshot(conn, snapshot, based_on_event_id)
        return snapshot

    def recompute_balance(
        self,
        work_id: str,
        entity_id: str,
        metric_id: str,
        narrative_order: int,
        knowledge_version: Optional[str] = None,
        version: Optional[str] = None,
        rule_version: Optional[str] = None,
    ) -> BalanceSnapshot:
        """按指定知识版本重新从事件账本计算，不依赖旧的缓存快照。"""
        return self.calculate_balance(
            work_id,
            entity_id,
            metric_id,
            narrative_order,
            knowledge_version=knowledge_version,
            version=version,
            rule_version=rule_version,
        )
