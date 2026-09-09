"""
fxi.model_gateway.cache - 基于 SHA-256 的本地大模型调用响应缓存
"""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any, Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex


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
    def _canonicalize(value: Any) -> Any:
        """Convert request metadata into a stable JSON-compatible value."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, type):
            return f"{value.__module__}.{value.__qualname__}"
        if isinstance(value, Mapping):
            return {
                str(key): LLMCache._canonicalize(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [LLMCache._canonicalize(item) for item in value]
        if isinstance(value, (set, frozenset)):
            values = [LLMCache._canonicalize(item) for item in value]
            return sorted(
                values,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return LLMCache._canonicalize(model_dump(mode="json"))
        raise TypeError(f"缓存键参数不可序列化: {type(value).__name__}")

    @staticmethod
    def compute_key(
        prompt: str,
        model: str,
        task: str,
        *,
        provider: str = "",
        temperature: Optional[float] = None,
        schema: str = "",
        max_tokens: int = 0,
        prompt_version: str = "gateway-v2",
        **request_parameters: Any,
    ) -> str:
        if not isinstance(prompt, str):
            raise TypeError("缓存键 prompt 必须是字符串")
        payload = {
            "task": task,
            "model": model,
            "provider": provider,
            "temperature": temperature,
            "schema": LLMCache._canonicalize(schema),
            "max_tokens": max_tokens,
            "prompt_version": prompt_version,
            # The provider receives the exact prompt; whitespace can therefore affect output.
            "prompt": prompt,
            # Future provider/request knobs must be supplied here instead of being silently ignored.
            "request_parameters": LLMCache._canonicalize(request_parameters),
        }
        return sha256_hex(payload)

    def get(self, cache_key: str) -> Optional[str]:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                "SELECT response_text FROM response_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def set(self, cache_key: str, prompt: str, model: str, response_text: str) -> None:
        p_hash = sha256_hex(prompt)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO response_cache (cache_key, prompt_hash, model, response_text, created_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(cache_key) DO UPDATE SET
                    prompt_hash = excluded.prompt_hash,
                    model = excluded.model,
                    response_text = excluded.response_text,
                    created_at = excluded.created_at
                """,
                (cache_key, p_hash, model, response_text)
            )

    def clear(self) -> int:
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute("DELETE FROM response_cache")
            return cur.rowcount
