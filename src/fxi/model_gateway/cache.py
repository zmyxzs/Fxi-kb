"""
fxi.model_gateway.cache - 基于 SHA-256 的本地大模型调用响应缓存
"""

import hashlib
import sqlite3
from pathlib import Path
from typing import Optional
from fxi.core.config import FxiConfig, load_config


class LLMCache:
    """基于 SQLite 的哈希响应缓存 (0ms, 0费用)"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_path = self.config.cache_db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS response_cache (
                cache_key TEXT PRIMARY KEY NOT NULL,
                prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                response_text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """)

    @staticmethod
    def compute_key(prompt: str, model: str, task: str) -> str:
        raw = f"{task}:{model}:{prompt.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, cache_key: str) -> Optional[str]:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "SELECT response_text FROM response_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def set(self, cache_key: str, prompt: str, model: str, response_text: str) -> None:
        p_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO response_cache (cache_key, prompt_hash, model, response_text, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (cache_key, p_hash, model, response_text)
            )

    def clear(self) -> int:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute("DELETE FROM response_cache")
            return cur.rowcount
