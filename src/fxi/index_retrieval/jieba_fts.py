"""
fxi.index_retrieval.jieba_fts - jieba 预分词与 SQLite FTS5 全文检索引擎
"""

from typing import Any, Optional
import jieba

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient
from fxi.index_retrieval.project_lexicon import LexiconManager


class ChineseFTS:
    """基于 jieba 预分词与 SQLite FTS5 的中文检索引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.lexicon_mgr = LexiconManager(self.config)
        self.lexicon_mgr.ensure_loaded()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def segment_text(self, text: str) -> str:
        """使用 jieba 分词并将词用空格隔开，供 FTS5 unicode61 分词器索引"""
        words = jieba.cut(text, cut_all=False)
        return " ".join([w.strip() for w in words if w.strip()])

    def index_scene(self, scene_uuid: str, work_id: str, chapter_index: int, content: str) -> None:
        """为场景正文建立/更新全文索引"""
        segmented = self.segment_text(content)
        with self.db_client.transaction() as cur:
            cur.execute(
                "DELETE FROM fts_scenes WHERE scene_uuid = ?",
                (scene_uuid,)
            )
            cur.execute(
                "INSERT INTO fts_scenes (scene_uuid, work_id, chapter_index, segmented_content) VALUES (?, ?, ?, ?)",
                (scene_uuid, work_id, chapter_index, segmented)
            )

    def search(self, query: str, work_id: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
        """
        检索场景，返回命中片段与场景 UUID
        """
        segmented_query = self.segment_text(query)
        if not segmented_query.strip():
            return []

        # 构造 FTS5 MATCH 查询语法 (用空格或者 OR 连接)
        tokens = [f'"{t}"' for t in segmented_query.split() if t]
        match_expr = " OR ".join(tokens)

        sql = """
        SELECT
            scene_uuid,
            work_id,
            chapter_index,
            snippet(fts_scenes, 3, '【', '】', '...', 32) AS highlight_snippet,
            bm25(fts_scenes) AS rank_score
        FROM fts_scenes
        WHERE fts_scenes MATCH ?
        """
        params: list[Any] = [match_expr]
        if work_id:
            sql += " AND work_id = ?"
            params.append(work_id)

        sql += " ORDER BY rank_score ASC LIMIT ?"
        params.append(limit)

        results = []
        with self.db_client.get_connection() as conn:
            try:
                cur = conn.execute(sql, params)
                for row in cur.fetchall():
                    results.append({
                        "scene_uuid": row["scene_uuid"],
                        "work_id": row["work_id"],
                        "chapter_index": row["chapter_index"],
                        "snippet": row["highlight_snippet"],
                        "score": row["rank_score"],
                    })
            except Exception:
                # 容错降级
                return []

        return results
