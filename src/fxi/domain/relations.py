"""
fxi.domain.relations - 实体关系图谱管理器
"""

from pathlib import Path
from typing import Any, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import StorageError, ValidationError
from fxi.core.identifiers import validate_work_id
from fxi.storage.sqlite_client import DatabaseClient


class RelationManager:
    """实体间关系 (师徒、宿敌、盟友、所属) 管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    @staticmethod
    def _validate_work_id(work_id: str) -> str:
        return validate_work_id(work_id)

    def link(
        self,
        work_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        valid_from_order: int = 0,
        valid_to_order: Optional[int] = None
    ) -> int:
        """建立关系边"""
        work_id = self._validate_work_id(work_id)
        with self.db_client.transaction() as cur:
            cur.execute(
                """
                INSERT INTO entity_relations
                (work_id, source_id, target_id, relation_type, valid_from_order, valid_to_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (work_id, source_id, target_id, relation_type, valid_from_order, valid_to_order)
            )
            return cur.lastrowid

    def get_relations(self, work_id: str, entity_id: str, narrative_order: int = 0) -> list[dict[str, Any]]:
        """查询实体在指定时间段的有效关系"""
        work_id = self._validate_work_id(work_id)
        sql = """
        SELECT * FROM entity_relations
        WHERE work_id = ? AND (source_id = ? OR target_id = ?)
          AND valid_from_order <= ?
          AND (valid_to_order IS NULL OR valid_to_order >= ?)
        """
        results = []
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, entity_id, entity_id, narrative_order, narrative_order))
            for row in cur.fetchall():
                results.append(dict(row))
        return results

    def _get_rel_file(self, work_id: str):
        work_id = self._validate_work_id(work_id)
        target_dir = self.config.projects_dir / work_id / "entities"
        return target_dir / "relationships.yaml"

    def save_relationships(self, work_id: str, relationships: list[dict[str, Any]]) -> None:
        """持久化结构化关系网络至 relationships.yaml 并同步到 SQLite"""
        if not isinstance(relationships, list) or any(not isinstance(r, dict) for r in relationships):
            raise ValidationError("relationships 必须是对象列表")
        target_file = self._get_rel_file(work_id)
        data = {"relationships": relationships}
        serialized = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_file.write_text(serialized, encoding="utf-8")
            if target_file.read_text(encoding="utf-8") != serialized:
                raise OSError("关系档案读回内容与写入内容不一致")
        except OSError as exc:
            raise StorageError(f"无法保存作品 [{work_id}] 的关系档案") from exc
        self.batch_sync_to_sqlite(work_id, relationships)

    def batch_sync_to_sqlite(self, work_id: str, relationships: list[dict[str, Any]]) -> int:
        """将 relationships 结构同步至 entity_relations 表"""
        work_id = self._validate_work_id(work_id)
        from fxi.domain.canonical import CanonicalRegistry
        reg = CanonicalRegistry(self.config)
        synced = 0
        with self.db_client.transaction() as cur:
            cur.execute("DELETE FROM entity_relations WHERE work_id = ?", (work_id,))
            for r in relationships:
                pair = r.get("pair") or []
                if len(pair) < 2:
                    continue
                source_name, target_name = pair[0], pair[1]
                src_id = reg.get_canonical_id(work_id, source_name, "character")
                tgt_id = reg.get_canonical_id(work_id, target_name, "character")
                rel_type = str(r.get("dynamic") or "relation")[:50]

                cur.execute(
                    """
                    INSERT INTO entity_relations (work_id, source_id, target_id, relation_type, valid_from_order, valid_to_order)
                    VALUES (?, ?, ?, ?, 0, NULL)
                    """,
                    (work_id, src_id, tgt_id, rel_type)
                )
                synced += 1
        return synced

    def load_relationships(self, work_id: str) -> list[dict[str, Any]]:
        """从 relationships.yaml 读取人际关系与张力"""
        target_file = self._get_rel_file(work_id)
        if not target_file.is_file():
            return []
        try:
            data = yaml.safe_load(target_file.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise StorageError(f"无法读取作品 [{work_id}] 的关系档案") from exc
        if not isinstance(data, dict) or not isinstance(data.get("relationships", []), list):
            raise ValidationError(f"作品 [{work_id}] 的关系档案结构无效")
        relationships = data.get("relationships", [])
        if any(not isinstance(relation, dict) for relation in relationships):
            raise ValidationError(f"作品 [{work_id}] 的关系档案必须是对象列表")
        return relationships

    def get_relationships_status(self, work_id: str) -> dict[str, Any]:
        """返回关系档案的可用状态，兼容旧的 list 查询接口。"""
        target_file = self._get_rel_file(work_id)
        if not target_file.is_file():
            return {
                "work_id": work_id,
                "status": "UNAVAILABLE",
                "error_code": "WORK_RELATIONSHIPS_MISSING",
                "message": "未找到该作品自己的 relationships.yaml；不会继承其他作品。",
            }
        relationships = self.load_relationships(work_id)
        return {"work_id": work_id, "status": "AVAILABLE", "relationships": relationships}

    def get_tensions(self, work_id: str, character_names: list[str]) -> list[dict[str, Any]]:
        """查询在场人物之间的人际张力、共同秘密与互动态度"""
        self._validate_work_id(work_id)
        all_rels = self.load_relationships(work_id)
        matching = []
        cnames_set = set(character_names)

        # 优先全量匹配 (当前在场角色包含关系对中的双方)
        for r in all_rels:
            pair = set(r.get("pair", []))
            if pair and pair.issubset(cnames_set):
                matching.append(r)

        # 仅当完全没有双方均在场的强关系时，才匹配单人高关联
        if not matching:
            for r in all_rels:
                pair = set(r.get("pair", []))
                if len(pair.intersection(cnames_set)) >= 1 and len(character_names) <= 2:
                    matching.append(r)

        return matching
