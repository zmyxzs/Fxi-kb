"""
fxi.timeline.continuity - 章节连续性台账管理器 (承前启后单真理源)
"""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import CorruptedDataError
from fxi.storage.sqlite_client import DatabaseClient, ensure_work


class ContinuityManager:
    """章节物理场景、尾声切片与悬念未决钩子 (Continuity Ledger) 管理器，基于 SQLite 提供单行 O(1) 访问"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def _get_legacy_ledger_path(self, work_id: str) -> Path:
        return self.config.projects_dir / work_id / "timeline" / "continuity_ledger.yaml"

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        conn = self.db_client.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    def _migrate_legacy_yaml_if_needed(self, work_id: str) -> None:
        legacy_path = self._get_legacy_ledger_path(work_id)
        if not legacy_path.is_file():
            return
        try:
            data = yaml.safe_load(legacy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise CorruptedDataError(f"读取遗留连续性台账失败 {legacy_path}: {exc}") from exc

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise CorruptedDataError(f"遗留连续性台账不是对象: {legacy_path}")
        chapters = data.get("chapters", {})
        if not isinstance(chapters, dict):
            raise CorruptedDataError(f"遗留连续性台账 chapters 不是对象: {legacy_path}")
        if not chapters:
            return

        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            for ch_idx, item in chapters.items():
                if not isinstance(item, dict):
                    raise CorruptedDataError(
                        f"遗留连续性台账章节 {ch_idx!r} 不是对象: {legacy_path}"
                    )
                try:
                    chapter_index = int(item.get("chapter_index", ch_idx))
                    active_characters = json.dumps(
                        item.get("active_characters", []), ensure_ascii=False
                    )
                    unresolved_hooks = json.dumps(
                        item.get("unresolved_hooks", []), ensure_ascii=False
                    )
                except (TypeError, ValueError) as exc:
                    raise CorruptedDataError(
                        f"遗留连续性台账章节 {ch_idx!r} 数据非法: {exc}"
                    ) from exc
                cur.execute(
                    """
                    INSERT OR IGNORE INTO chapter_continuity
                    (work_id, chapter_index, title, ending_location, active_characters_json,
                     ending_situation, unresolved_hooks_json, tail_snippet, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        work_id,
                        chapter_index,
                        item.get("title", ""),
                        item.get("ending_location", ""),
                        active_characters,
                        item.get("ending_situation", ""),
                        unresolved_hooks,
                        item.get("tail_snippet", ""),
                    ),
                )

    def record_chapter(
        self,
        work_id: str,
        chapter_index: int,
        title: str,
        tail_snippet: str,
        ending_location: str,
        active_characters: list[str],
        ending_situation: str,
        unresolved_hooks: list[str],
    ) -> None:
        """单章原子写入 SQLite，支持毫秒级定位，零全量重写损耗"""
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT INTO chapter_continuity
                (work_id, chapter_index, title, ending_location, active_characters_json,
                 ending_situation, unresolved_hooks_json, tail_snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(work_id, chapter_index) DO UPDATE SET
                    title = excluded.title,
                    ending_location = excluded.ending_location,
                    active_characters_json = excluded.active_characters_json,
                    ending_situation = excluded.ending_situation,
                    unresolved_hooks_json = excluded.unresolved_hooks_json,
                    tail_snippet = excluded.tail_snippet,
                    created_at = excluded.created_at
                """,
                (
                    work_id,
                    int(chapter_index),
                    title,
                    ending_location,
                    json.dumps(active_characters or [], ensure_ascii=False),
                    ending_situation,
                    json.dumps(unresolved_hooks or [], ensure_ascii=False),
                    tail_snippet,
                ),
            )

    def get_continuity(self, work_id: str, chapter_index: int) -> Optional[dict[str, Any]]:
        """单章 O(1) 毫秒级读取，按索引直接获取前情事实"""
        sql = """
            SELECT chapter_index, title, tail_snippet, ending_location,
                   active_characters_json, ending_situation, unresolved_hooks_json
            FROM chapter_continuity
            WHERE work_id = ? AND chapter_index = ?
        """
        with self._connection() as conn:
            row = conn.execute(sql, (work_id, int(chapter_index))).fetchone()
            if row:
                return {
                    "chapter_index": row["chapter_index"],
                    "title": row["title"],
                    "tail_snippet": row["tail_snippet"],
                    "ending_location": row["ending_location"],
                    "active_characters": json.loads(row["active_characters_json"] or "[]"),
                    "ending_situation": row["ending_situation"],
                    "unresolved_hooks": json.loads(row["unresolved_hooks_json"] or "[]"),
                }

        # 若 SQLite 暂无，尝试从遗留 YAML 兜底并自动迁移
        self._migrate_legacy_yaml_if_needed(work_id)
        with self._connection() as conn:
            row = conn.execute(sql, (work_id, int(chapter_index))).fetchone()
            if row:
                return {
                    "chapter_index": row["chapter_index"],
                    "title": row["title"],
                    "tail_snippet": row["tail_snippet"],
                    "ending_location": row["ending_location"],
                    "active_characters": json.loads(row["active_characters_json"] or "[]"),
                    "ending_situation": row["ending_situation"],
                    "unresolved_hooks": json.loads(row["unresolved_hooks_json"] or "[]"),
                }
        return None

    def get_latest_continuity(self, work_id: str) -> Optional[dict[str, Any]]:
        """获取最近一章的连续性快照"""
        sql = """
            SELECT chapter_index, title, tail_snippet, ending_location,
                   active_characters_json, ending_situation, unresolved_hooks_json
            FROM chapter_continuity
            WHERE work_id = ?
            ORDER BY chapter_index DESC
            LIMIT 1
        """
        with self._connection() as conn:
            row = conn.execute(sql, (work_id,)).fetchone()
            if row:
                return {
                    "chapter_index": row["chapter_index"],
                    "title": row["title"],
                    "tail_snippet": row["tail_snippet"],
                    "ending_location": row["ending_location"],
                    "active_characters": json.loads(row["active_characters_json"] or "[]"),
                    "ending_situation": row["ending_situation"],
                    "unresolved_hooks": json.loads(row["unresolved_hooks_json"] or "[]"),
                }
        self._migrate_legacy_yaml_if_needed(work_id)
        with self._connection() as conn:
            row = conn.execute(sql, (work_id,)).fetchone()
            if row:
                return {
                    "chapter_index": row["chapter_index"],
                    "title": row["title"],
                    "tail_snippet": row["tail_snippet"],
                    "ending_location": row["ending_location"],
                    "active_characters": json.loads(row["active_characters_json"] or "[]"),
                    "ending_situation": row["ending_situation"],
                    "unresolved_hooks": json.loads(row["unresolved_hooks_json"] or "[]"),
                }
        return None

    def load_ledger(self, work_id: str) -> dict[str, Any]:
        """向后兼容接口：获取指定作品的全量章节字典映射"""
        sql = """
            SELECT chapter_index, title, tail_snippet, ending_location,
                   active_characters_json, ending_situation, unresolved_hooks_json
            FROM chapter_continuity
            WHERE work_id = ?
            ORDER BY chapter_index ASC
        """
        with self._connection() as conn:
            rows = conn.execute(sql, (work_id,)).fetchall()
            if not rows:
                self._migrate_legacy_yaml_if_needed(work_id)
                rows = conn.execute(sql, (work_id,)).fetchall()

            chapters: dict[int, dict[str, Any]] = {}
            for row in rows:
                chapters[row["chapter_index"]] = {
                    "chapter_index": row["chapter_index"],
                    "title": row["title"],
                    "tail_snippet": row["tail_snippet"],
                    "ending_location": row["ending_location"],
                    "active_characters": json.loads(row["active_characters_json"] or "[]"),
                    "ending_situation": row["ending_situation"],
                    "unresolved_hooks": json.loads(row["unresolved_hooks_json"] or "[]"),
                }
            return {"work_id": work_id, "chapters": chapters}

    def save_ledger(self, work_id: str, data: dict[str, Any]) -> None:
        """向后兼容接口：批量保存数据到 SQLite"""
        chapters = data.get("chapters", {})
        if not isinstance(chapters, dict):
            return
        with self.db_client.transaction() as cur:
            ensure_work(cur, work_id)
            for ch_idx, item in chapters.items():
                if not isinstance(item, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO chapter_continuity
                    (work_id, chapter_index, title, ending_location, active_characters_json,
                     ending_situation, unresolved_hooks_json, tail_snippet, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(work_id, chapter_index) DO UPDATE SET
                        title = excluded.title,
                        ending_location = excluded.ending_location,
                        active_characters_json = excluded.active_characters_json,
                        ending_situation = excluded.ending_situation,
                        unresolved_hooks_json = excluded.unresolved_hooks_json,
                        tail_snippet = excluded.tail_snippet,
                        created_at = excluded.created_at
                    """,
                    (
                        work_id,
                        int(item.get("chapter_index", ch_idx)),
                        item.get("title", ""),
                        item.get("ending_location", ""),
                        json.dumps(item.get("active_characters", []), ensure_ascii=False),
                        item.get("ending_situation", ""),
                        json.dumps(item.get("unresolved_hooks", []), ensure_ascii=False),
                        item.get("tail_snippet", ""),
                    ),
                )
