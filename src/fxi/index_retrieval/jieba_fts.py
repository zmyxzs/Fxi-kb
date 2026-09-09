"""基于 jieba 与 SQLite FTS5 的来源场景检索。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Optional

import jieba

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import ValidationError
from fxi.core.identifiers import validate_segment, validate_work_id
from fxi.index_retrieval.project_lexicon import LexiconManager
from fxi.storage.sqlite_client import DatabaseClient


class ChineseFTS:
    """基于 jieba 预分词与 SQLite FTS5 的中文检索引擎。"""

    _VERSION_COLUMNS = frozenset({"source_id", "source_version"})

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.lexicon_mgr = LexiconManager(self.config)
        self.lexicon_mgr.ensure_loaded()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def segment_text(self, text: str) -> str:
        """使用 jieba 分词并将词用空格隔开，供 FTS5 索引。"""
        words = jieba.cut(text, cut_all=False)
        return " ".join(w.strip() for w in words if w.strip())

    @staticmethod
    def _schema_columns(cursor: Any) -> set[str]:
        rows = cursor.execute("PRAGMA table_info(fts_scenes)").fetchall()
        columns = {str(row[1]) for row in rows}
        if not columns:
            raise RuntimeError("fts_scenes 表不存在，无法建立或查询索引")
        return columns

    @classmethod
    def _require_version_columns(cls, columns: set[str]) -> None:
        missing = sorted(cls._VERSION_COLUMNS - columns)
        if missing:
            raise RuntimeError("fts_scenes 缺少版本绑定列: " + ", ".join(missing))

    @staticmethod
    def _validate_scene_uuid(scene_uuid: Any) -> str:
        try:
            return validate_segment(scene_uuid, "scene_uuid")
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    @classmethod
    def _validate_binding(
        cls,
        work_id: Any,
        source_id: Optional[Any],
        source_version: Optional[Any],
        *,
        require_version: bool = False,
    ) -> tuple[str, Optional[str], Optional[str]]:
        validated_work_id = validate_work_id(work_id)
        if (source_id is None) != (source_version is None):
            raise ValueError("source_id 与 source_version 必须同时提供")
        if require_version and (source_id is None or source_version is None):
            raise ValueError("版本化 FTS 操作必须提供 source_id 与 source_version")
        if source_id is None:
            return validated_work_id, None, None
        return (
            validated_work_id,
            validate_segment(source_id, "source_id"),
            validate_segment(source_version, "source_version"),
        )

    def index_scene(
        self,
        scene_uuid: str,
        work_id: str,
        chapter_index: int,
        content: str,
        *,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        allow_legacy_hint: bool = False,
    ) -> None:
        """建立或更新一个场景索引。

        旧调用没有来源版本时仍保持兼容，并写入 legacy 行；这类行不会进入
        显式版本化检索。新调用必须同时提供 ``source_id`` 和
        ``source_version``，且数据库必须已经完成版本绑定列迁移。
        """
        validated_scene_uuid = self._validate_scene_uuid(scene_uuid)
        validated_work_id, validated_source_id, validated_source_version = self._validate_binding(
            work_id, source_id, source_version
        )
        segmented = self.segment_text(content)

        with self.db_client.transaction() as cur:
            columns = self._schema_columns(cur)
            has_version_columns = self._VERSION_COLUMNS <= columns
            if validated_source_id is not None:
                self._require_version_columns(columns)
                cur.execute(
                    """
                    DELETE FROM fts_scenes
                    WHERE scene_uuid = ? AND work_id = ?
                      AND source_id = ? AND source_version = ?
                    """,
                    (
                        validated_scene_uuid,
                        validated_work_id,
                        validated_source_id,
                        validated_source_version,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO fts_scenes
                        (scene_uuid, work_id, source_id, source_version,
                         chapter_index, segmented_content)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        validated_scene_uuid,
                        validated_work_id,
                        validated_source_id,
                        validated_source_version,
                        chapter_index,
                        segmented,
                    ),
                )
                return

            if has_version_columns:
                cur.execute(
                    """
                    DELETE FROM fts_scenes
                    WHERE scene_uuid = ? AND work_id = ?
                      AND source_id IS NULL AND source_version IS NULL
                    """,
                    (validated_scene_uuid, validated_work_id),
                )
                cur.execute(
                    """
                    INSERT INTO fts_scenes
                        (scene_uuid, work_id, source_id, source_version,
                         chapter_index, segmented_content)
                    VALUES (?, ?, NULL, NULL, ?, ?)
                    """,
                    (validated_scene_uuid, validated_work_id, chapter_index, segmented),
                )
                return

            cur.execute(
                "DELETE FROM fts_scenes WHERE scene_uuid = ? AND work_id = ?",
                (validated_scene_uuid, validated_work_id),
            )
            cur.execute(
                """
                INSERT INTO fts_scenes
                    (scene_uuid, work_id, chapter_index, segmented_content)
                VALUES (?, ?, ?, ?)
                """,
                (validated_scene_uuid, validated_work_id, chapter_index, segmented),
            )

    @staticmethod
    def _scene_row(scene: Any) -> tuple[str, int, str]:
        if isinstance(scene, Mapping):
            scene_uuid = scene.get("scene_uuid")
            chapter_index = scene.get("chapter_index")
            content = scene.get("content")
        else:
            scene_uuid = getattr(scene, "scene_uuid", None)
            chapter_index = getattr(scene, "chapter_index", None)
            content = getattr(scene, "content", None)
        if scene_uuid is None:
            raise ValueError("场景记录缺少 scene_uuid")
        if chapter_index is None:
            raise ValueError("场景记录缺少 chapter_index")
        if not isinstance(content, str):
            raise ValueError("场景记录的 content 必须是字符串")
        return ChineseFTS._validate_scene_uuid(scene_uuid), int(chapter_index), content

    def replace_source_index(
        self,
        work_id: str,
        source_id: str,
        source_version: str,
        scenes: Iterable[Any],
    ) -> None:
        """在一个事务内替换来源版本的全部场景索引。

        写入前会完整校验并预分词；事务中先删除同一 work/source/version 的旧
        行，再写入新集合。任何校验、分词或 SQL 错误都会回滚，避免留下半套
        索引。空集合是有意的全量清空，用于发布不再存在的旧场景。
        """
        validated_work_id, validated_source_id, validated_source_version = self._validate_binding(
            work_id, source_id, source_version, require_version=True
        )
        prepared: list[tuple[str, int, str]] = []
        seen_scene_uuids: set[str] = set()
        for scene in scenes:
            scene_uuid, chapter_index, content = self._scene_row(scene)
            if scene_uuid in seen_scene_uuids:
                raise ValueError(f"场景记录重复: {scene_uuid}")
            seen_scene_uuids.add(scene_uuid)
            prepared.append((scene_uuid, chapter_index, self.segment_text(content)))

        with self.db_client.transaction() as cur:
            columns = self._schema_columns(cur)
            self._require_version_columns(columns)
            cur.execute(
                """
                DELETE FROM fts_scenes
                WHERE work_id = ? AND source_id = ? AND source_version = ?
                """,
                (validated_work_id, validated_source_id, validated_source_version),
            )
            cur.executemany(
                """
                INSERT INTO fts_scenes
                    (scene_uuid, work_id, source_id, source_version,
                     chapter_index, segmented_content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        scene_uuid,
                        validated_work_id,
                        validated_source_id,
                        validated_source_version,
                        chapter_index,
                        segmented,
                    )
                    for scene_uuid, chapter_index, segmented in prepared
                ],
            )

    def to_phrase(self, text: str) -> str:
        """将一段文本转为 FTS5 短语格式。"""
        seg = self.segment_text(text).strip()
        return f'"{seg.replace(chr(34), chr(34) * 2)}"' if seg else ""

    def search(
        self,
        query: str,
        work_id: Optional[str] = None,
        limit: int = 10,
        required_terms: Optional[list[str]] = None,
        order_by: str = "rank",
        *,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        allow_legacy_hint: bool = False,
    ) -> list[dict[str, Any]]:
        """检索场景，并按显式来源版本限制结果范围。

        版本化检索必须同时提供 work/source/version。未指定来源版本时，若表
        已支持版本绑定，只返回 legacy 行，避免把不同来源版本混入事实结果。
        """
        if order_by not in {"rank", "chronological"}:
            raise ValueError("order_by must be 'rank' or 'chronological'")
        if limit <= 0:
            return []

        validated_work_id: Optional[str] = None
        if work_id is not None:
            validated_work_id, _, _ = self._validate_binding(work_id, source_id, source_version)
        elif source_id is not None or source_version is not None:
            raise ValueError("版本化检索必须提供 work_id")
        elif (source_id is None) != (source_version is None):
            raise ValueError("source_id 与 source_version 必须同时提供")

        validated_source_id: Optional[str] = None
        validated_source_version: Optional[str] = None
        if (
            validated_work_id is not None
            and source_id is None
            and source_version is None
            and not allow_legacy_hint
        ):
            from fxi.api.registry import (
                RegistryError,
                SourceScopeRequiredError,
                WorkRegistry,
            )

            try:
                binding = WorkRegistry(self.db_client).resolve(validated_work_id)
            except SourceScopeRequiredError as exc:
                raise ValueError(f"{exc.code}: source_id is required") from exc
            except RegistryError as exc:
                code = getattr(exc, "code", "SOURCE_SCOPE_REQUIRED")
                raise ValueError(f"{code}: source scope is required") from exc
            if not binding.source_version:
                raise ValueError("SOURCE_SCOPE_REQUIRED: active source has no version")
            validated_source_id = validate_segment(binding.source_id, "source_id")
            validated_source_version = validate_segment(binding.source_version, "source_version")
        if source_id is not None:
            _, validated_source_id, validated_source_version = self._validate_binding(
                work_id, source_id, source_version, require_version=True
            )

        terms = [t.strip() for t in (required_terms or query.split()) if t.strip()]
        if not terms:
            return []

        phrases: list[str] = []
        for term in terms:
            phrase = self.to_phrase(term)
            if phrase and phrase not in phrases:
                phrases.append(phrase)
        if not phrases:
            return []

        results: list[dict[str, Any]] = []
        seen_uuids: set[str] = set()

        def _execute_match(match_expr: str, cur_limit: int) -> list[dict[str, Any]]:
            sort_sql = "chapter_index ASC" if order_by == "chronological" else "rank_score ASC"
            params: list[Any] = [match_expr]
            with self.db_client.get_connection() as conn:
                columns = self._schema_columns(conn)
                has_version_columns = self._VERSION_COLUMNS <= columns
                if validated_source_id is not None:
                    self._require_version_columns(columns)
                source_select = (
                    "source_id, source_version"
                    if has_version_columns
                    else "NULL AS source_id, NULL AS source_version"
                )
                sql = f"""
                SELECT
                    scene_uuid,
                    work_id,
                    {source_select},
                    chapter_index,
                    snippet(fts_scenes, 3, '【', '】', '...', 32) AS highlight_snippet,
                    bm25(fts_scenes) AS rank_score
                FROM fts_scenes
                WHERE fts_scenes MATCH ?
                """
                if validated_work_id is not None:
                    sql += " AND work_id = ?"
                    params.append(validated_work_id)
                if validated_source_id is not None:
                    sql += " AND source_id = ? AND source_version = ?"
                    params.extend([validated_source_id, validated_source_version])
                elif has_version_columns:
                    # 未指定版本时只允许遗留行，不能跨版本泄漏事实。
                    sql += " AND source_id IS NULL AND source_version IS NULL"
                sql += f" ORDER BY {sort_sql} LIMIT ?"
                params.append(cur_limit)
                matched: list[dict[str, Any]] = []
                for row in conn.execute(sql, params).fetchall():
                    matched.append(
                        {
                            "scene_uuid": row["scene_uuid"],
                            "work_id": row["work_id"],
                            "source_id": row["source_id"],
                            "source_version": row["source_version"],
                            "chapter_index": row["chapter_index"],
                            "snippet": row["highlight_snippet"],
                            "score": row["rank_score"],
                        }
                    )
                return matched

        if len(phrases) > 1:
            and_expr = " AND ".join(phrases)
            for item in _execute_match(and_expr, limit):
                if item["scene_uuid"] not in seen_uuids:
                    seen_uuids.add(item["scene_uuid"])
                    results.append(item)

        if len(results) < limit:
            or_expr = " OR ".join(phrases)
            for item in _execute_match(or_expr, limit):
                if item["scene_uuid"] not in seen_uuids:
                    seen_uuids.add(item["scene_uuid"])
                    results.append(item)
                    if len(results) >= limit:
                        break

        return results
