"""
fxi.domain.entities - 故事世界实体管理器 (人物/法宝/地理/宗门)
"""

import json
from pathlib import Path
from typing import Any, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import NotFoundError
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.text_io import write_markdown_frontmatter


class EntityManager:
    """实体模型管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def get_entity(self, work_id: str, entity_id: str) -> dict[str, Any]:
        """获取单个实体设定"""
        with self.db_client.get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM entities WHERE work_id = ? AND entity_id = ?",
                (work_id, entity_id)
            )
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"未找到实体: {work_id}/{entity_id}")

            return {
                "entity_id": row["entity_id"],
                "work_id": row["work_id"],
                "category": row["category"],
                "is_unique": bool(row["is_unique"]),
                "name": row["name"],
                "aliases": json.loads(row["aliases_json"] or "[]"),
                "attributes": yaml.safe_load(row["attributes_yaml"] or "{}"),
                "file_path": row["file_path"],
                "updated_at": row["updated_at"],
            }

    def upsert_entity(
        self,
        work_id: str,
        entity_id: str,
        name: str,
        category: str = "character",
        is_unique: bool = True,
        aliases: Optional[list[str]] = None,
        attributes: Optional[dict[str, Any]] = None,
        description: str = "",
    ) -> None:
        """持久化落盘实体至 Markdown 文件并同步到 SQLite"""
        aliases = aliases or []
        attributes = attributes or {}

        # 确定物理落盘路径 projects/<work-id>/entities/<category>/<entity_id>.md
        rel_dir = f"entities/{category}s"
        target_file = self.config.projects_dir / work_id / rel_dir / f"{entity_id}.md"

        metadata = {
            "entity_id": entity_id,
            "category": category,
            "is_unique": is_unique,
            "name": name,
            "aliases": aliases,
            "attributes": attributes,
        }

        # 1. 写入纯文本 Markdown 单真理源
        write_markdown_frontmatter(target_file, metadata, description)

        # 2. 同步写入 SQLite
        rel_path = str(target_file.relative_to(self.config.workspace_root))
        with self.db_client.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO authors (owner_id, slug, display_name, created_at) VALUES ('default_author', 'default_author', 'Default Author', datetime('now'))"
            )
            cur.execute(
                "INSERT OR IGNORE INTO works (work_id, owner_id, slug, title, genre_ids_json, created_at, updated_at) VALUES (?, 'default_author', ?, ?, '[]', datetime('now'), datetime('now'))",
                (work_id, work_id, work_id)
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO entities
                (entity_id, work_id, category, is_unique, name, aliases_json, attributes_yaml, file_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    entity_id,
                    work_id,
                    category,
                    1 if is_unique else 0,
                    name,
                    json.dumps(aliases, ensure_ascii=False),
                    yaml.dump(attributes, allow_unicode=True),
                    rel_path,
                )
            )

    def list_entities(self, work_id: str, category: Optional[str] = None) -> list[dict[str, Any]]:
        """列出指定作品下的实体"""
        sql = "SELECT * FROM entities WHERE work_id = ?"
        params: list[Any] = [work_id]
        if category:
            sql += " AND category = ?"
            params.append(category)

        results = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, params)
            for row in cur.fetchall():
                results.append({
                    "entity_id": row["entity_id"],
                    "work_id": row["work_id"],
                    "category": row["category"],
                    "is_unique": bool(row["is_unique"]),
                    "name": row["name"],
                    "aliases": json.loads(row["aliases_json"] or "[]"),
                })
        return results
