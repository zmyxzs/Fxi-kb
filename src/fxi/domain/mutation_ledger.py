"""
fxi.domain.mutation_ledger - 同人时间线因果变动账本 (Mutation Ledger)

负责管理同人二次创作对原著设定/时序的合法魔改、蝴蝶效应与因果干预记录。
核心原则：“允许魔改，但魔改必须出师有名（因果立账）”。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.domain.canonical import CanonicalRegistry
from fxi.storage.sqlite_client import DatabaseClient


@dataclass
class TimelineMutation:
    """
    同人剧情因果变动单元 (Timeline Mutation)

    Attributes:
        mutation_id: 变动唯一标识 (如 "mut_012_early_god_domain")
        work_id: 归属作品标识
        trigger_chapter: 触发该变动的同人章节序号
        cause_event: 引发变动的具体因果事件
        entity_id: 受影响的实体标识
        mutation_type: 变动类型 (ability_grant | ability_modify | ability_suppress | state_override | relation_change)
        target_name: 变动的目标名称
        original_canon_chapter: 原著中该状态/能力原本解锁的章节 (可选)
        payload: 变动的详细参数 (如修改后的消耗、威力、状态值等)
        status: 变动状态 (active | superseded | cancelled)
        created_at: 创建时间戳 (ISO-8601)
    """
    mutation_id: str
    work_id: str
    trigger_chapter: int
    cause_event: str
    entity_id: str
    mutation_type: str
    target_name: str
    original_canon_chapter: Optional[int] = None
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimelineMutation:
        return cls(
            mutation_id=str(data.get("mutation_id", "")),
            work_id=str(data.get("work_id", "")),
            trigger_chapter=int(data.get("trigger_chapter", 1)),
            cause_event=str(data.get("cause_event", "")),
            entity_id=str(data.get("entity_id", "")),
            mutation_type=str(data.get("mutation_type", "ability_grant")),
            target_name=str(data.get("target_name", "")),
            original_canon_chapter=int(data["original_canon_chapter"]) if data.get("original_canon_chapter") is not None else None,
            payload=dict(data.get("payload") or {}),
            status=str(data.get("status", "active")),
            created_at=str(data.get("created_at", "")),
        )


class MutationLedger:
    """同人变动账本管理器，双写持久化于 YAML 文件与 SQLite"""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        db_client: Optional[DatabaseClient] = None,
    ):
        self.config = config or load_config()
        self.db_client = db_client or DatabaseClient(self.config.sqlite_path)
        self.canonical_registry = CanonicalRegistry(self.config)

    def get_ledger_path(self, work_id: str) -> Path:
        """获取指定作品的变动账本 YAML 路径"""
        timeline_dir = self.config.projects_dir / work_id / "timeline"
        timeline_dir.mkdir(parents=True, exist_ok=True)
        return timeline_dir / "mutations.yaml"

    def record_mutation(
        self,
        work_id: str,
        trigger_chapter: int,
        cause_event: str,
        entity_name_or_id: str,
        mutation_type: str,
        target_name: str,
        mutation_id: Optional[str] = None,
        original_canon_chapter: Optional[int] = None,
        payload: Optional[dict[str, Any]] = None,
        status: str = "active",
    ) -> TimelineMutation:
        """
        正式立项一条同人因果变动 (立账)
        """
        canonical_id = self.canonical_registry.get_canonical_id(
            work_id, entity_name_or_id, category="character"
        )
        entity_id = canonical_id or entity_name_or_id

        if not mutation_id:
            mutation_id = f"mut_{trigger_chapter:03d}_{entity_id}_{mutation_type}_{target_name}"

        mutation = TimelineMutation(
            mutation_id=mutation_id,
            work_id=work_id,
            trigger_chapter=int(trigger_chapter),
            cause_event=cause_event.strip(),
            entity_id=entity_id,
            mutation_type=mutation_type.strip(),
            target_name=target_name.strip(),
            original_canon_chapter=int(original_canon_chapter) if original_canon_chapter is not None else None,
            payload=dict(payload or {}),
            status=status,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # 1. 写入 SQLite
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                INSERT INTO timeline_mutations (
                    mutation_id, work_id, trigger_chapter, cause_event,
                    entity_id, mutation_type, target_name, original_canon_chapter,
                    payload_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_id, mutation_id) DO UPDATE SET
                    trigger_chapter = excluded.trigger_chapter,
                    cause_event = excluded.cause_event,
                    entity_id = excluded.entity_id,
                    mutation_type = excluded.mutation_type,
                    target_name = excluded.target_name,
                    original_canon_chapter = excluded.original_canon_chapter,
                    payload_json = excluded.payload_json,
                    status = excluded.status,
                    created_at = excluded.created_at
                """,
                (
                    mutation.mutation_id,
                    mutation.work_id,
                    mutation.trigger_chapter,
                    mutation.cause_event,
                    mutation.entity_id,
                    mutation.mutation_type,
                    mutation.target_name,
                    mutation.original_canon_chapter,
                    json.dumps(mutation.payload, ensure_ascii=False),
                    mutation.status,
                    mutation.created_at,
                ),
            )

        # 2. 同步写出 YAML 文件
        self.sync_to_file(work_id)
        return mutation

    def list_mutations(
        self,
        work_id: str,
        chapter: Optional[int] = None,
        entity_name_or_id: Optional[str] = None,
        status: str = "active",
    ) -> list[TimelineMutation]:
        """
        查询生效的变动记录。
        若指定 chapter，则仅返回 trigger_chapter <= chapter 的变动。
        """
        entity_id = None
        if entity_name_or_id:
            canonical_id = self.canonical_registry.get_canonical_id(
                work_id, entity_name_or_id, category="character"
            )
            entity_id = canonical_id or entity_name_or_id

        query = "SELECT * FROM timeline_mutations WHERE work_id = ?"
        params: list[Any] = [work_id]

        if status:
            query += " AND status = ?"
            params.append(status)

        if chapter is not None:
            query += " AND trigger_chapter <= ?"
            params.append(int(chapter))

        if entity_id:
            query += " AND (entity_id = ? OR entity_id = ?)"
            params.extend([entity_id, entity_name_or_id])

        query += " ORDER BY trigger_chapter ASC, created_at ASC"

        with self.db_client.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        results = []
        for r in rows:
            results.append(
                TimelineMutation(
                    mutation_id=r["mutation_id"],
                    work_id=r["work_id"],
                    trigger_chapter=r["trigger_chapter"],
                    cause_event=r["cause_event"],
                    entity_id=r["entity_id"],
                    mutation_type=r["mutation_type"],
                    target_name=r["target_name"],
                    original_canon_chapter=r["original_canon_chapter"],
                    payload=json.loads(r["payload_json"] or "{}"),
                    status=r["status"],
                    created_at=r["created_at"],
                )
            )
        return results

    def get_mutation(self, work_id: str, mutation_id: str) -> Optional[TimelineMutation]:
        """按 ID 查询单个变动记录"""
        with self.db_client.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM timeline_mutations WHERE work_id = ? AND mutation_id = ?",
                (work_id, mutation_id),
            ).fetchone()
            if not row:
                return None
            return TimelineMutation(
                mutation_id=row["mutation_id"],
                work_id=row["work_id"],
                trigger_chapter=row["trigger_chapter"],
                cause_event=row["cause_event"],
                entity_id=row["entity_id"],
                mutation_type=row["mutation_type"],
                target_name=row["target_name"],
                original_canon_chapter=row["original_canon_chapter"],
                payload=json.loads(row["payload_json"] or "{}"),
                status=row["status"],
                created_at=row["created_at"],
            )

    def cancel_mutation(self, work_id: str, mutation_id: str) -> bool:
        """撤销或取消某项变动"""
        with self.db_client.transaction() as cur:
            res = cur.execute(
                "UPDATE timeline_mutations SET status = 'cancelled' WHERE work_id = ? AND mutation_id = ?",
                (work_id, mutation_id),
            )
            affected = res.rowcount > 0

        if affected:
            self.sync_to_file(work_id)
        return affected

    def sync_to_file(self, work_id: str) -> int:
        """将 SQLite 中的变动导出持久化为 YAML"""
        mutations = self.list_mutations(work_id, status="")
        data = [m.to_dict() for m in mutations]
        yaml_content = yaml.dump(
            data, allow_unicode=True, sort_keys=False, default_flow_style=False
        )

        target_path = self.get_ledger_path(work_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        temp_file = target_path.with_suffix(".tmp")
        temp_file.write_text(yaml_content, encoding="utf-8")
        if target_path.exists():
            target_path.unlink()
        temp_file.rename(target_path)
        return len(mutations)

    def sync_from_file(self, work_id: str) -> int:
        """从 YAML 文件读取并同步恢复至 SQLite"""
        file_path = self.get_ledger_path(work_id)
        if not file_path.is_file():
            return 0

        content = file_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or []
        if not isinstance(data, list):
            return 0

        count = 0
        with self.db_client.transaction() as cur:
            for item in data:
                m = TimelineMutation.from_dict(item)
                cur.execute(
                    """
                    INSERT INTO timeline_mutations (
                        mutation_id, work_id, trigger_chapter, cause_event,
                        entity_id, mutation_type, target_name, original_canon_chapter,
                        payload_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(work_id, mutation_id) DO UPDATE SET
                        trigger_chapter = excluded.trigger_chapter,
                        cause_event = excluded.cause_event,
                        entity_id = excluded.entity_id,
                        mutation_type = excluded.mutation_type,
                        target_name = excluded.target_name,
                        original_canon_chapter = excluded.original_canon_chapter,
                        payload_json = excluded.payload_json,
                        status = excluded.status,
                        created_at = excluded.created_at
                    """,
                    (
                        m.mutation_id,
                        m.work_id,
                        m.trigger_chapter,
                        m.cause_event,
                        m.entity_id,
                        m.mutation_type,
                        m.target_name,
                        m.original_canon_chapter,
                        json.dumps(m.payload, ensure_ascii=False),
                        m.status,
                        m.created_at,
                    ),
                )
                count += 1
        return count
