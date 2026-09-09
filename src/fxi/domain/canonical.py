"""
fxi.domain.canonical - 权威实体注册表与实体归一化去重引擎
"""

import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.text_io import read_markdown_frontmatter, write_markdown_frontmatter

class CanonicalRegistry:
    """实体命名归一化、权威 ID 映射与去重合并管理器"""

    _CATEGORY_ALIASES = {
        "char": "character",
        "chars": "character",
        "characters": "character",
        "items": "item",
        "loc": "location",
        "locations": "location",
        "fac": "faction",
        "factions": "faction",
    }
    _KNOWN_ID_CATEGORIES = {"character", "item", "location", "faction"}

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    @staticmethod
    def _text_key(value: Any) -> str:
        return str(value or "").strip().casefold()

    @classmethod
    def _category_key(cls, category: Any) -> str:
        value = str(category or "character").strip().casefold()
        if not value or not re.fullmatch(r"[\w.-]{1,64}", value, re.UNICODE):
            raise ValueError("实体类型必须是安全的非空标识符")
        return cls._CATEGORY_ALIASES.get(value, value)

    @classmethod
    def _category_from_id(cls, entity_id: Any) -> Optional[str]:
        if not isinstance(entity_id, str):
            return None
        prefix, separator, _ = entity_id.partition("_")
        if not separator:
            return None
        prefix = prefix.casefold()
        if prefix in cls._CATEGORY_ALIASES:
            return cls._CATEGORY_ALIASES[prefix]
        return prefix if prefix in cls._KNOWN_ID_CATEGORIES else None

    @classmethod
    def _validate_entity_id(cls, entity_id: Any, category: str, work_id: str) -> str:
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的规范 ID 必须是非空字符串")
        value = entity_id.strip()
        invalid_chars = ("/", "\\", "\n", "\r", "<", ">", ":", '"', "|", "?", "*")
        if value in {".", ".."} or any(char in value for char in invalid_chars):
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的规范 ID 含非法路径或控制字符")
        inferred_category = cls._category_from_id(value)
        if inferred_category and inferred_category != category:
            raise RuntimeError(
                f"INCOMPLETE: 作品 [{work_id}] 的规范 ID [{value}] 与类型 [{category}] 冲突"
            )
        return value

    def _work_root(self, work_id: str) -> Path:
        if not isinstance(work_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", work_id):
            raise ValueError("work_id 必须是安全的非空作品标识符")
        return self.config.projects_dir / work_id

    def _load_work_canonical_ids(self, work_id: str) -> dict[tuple[str, str], str]:
        """读取指定作品的规范映射；配置始终限定在该作品目录内。"""
        path = self._work_root(work_id) / "entities" / "canonical.yaml"
        if not path.is_file():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 canonical.yaml 无法读取") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 canonical.yaml 必须是对象")
        declared_scope = data.get("scope", data.get("work_id"))
        if declared_scope not in (None, "", work_id):
            raise RuntimeError(
                f"INCOMPLETE: 作品 [{work_id}] 的 canonical.yaml scope [{declared_scope}] 不匹配"
            )

        mapping: dict[tuple[str, str], str] = {}
        id_categories: dict[str, str] = {}

        def add_mapping(name: Any, entity_id: Any, category: Any = None) -> None:
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的规范名称必须是非空字符串")
            category_key = self._category_key(
                category or self._category_from_id(entity_id) or "character"
            )
            entity_id_value = self._validate_entity_id(entity_id, category_key, work_id)
            previous_category = id_categories.get(entity_id_value)
            if previous_category and previous_category != category_key:
                raise RuntimeError(
                    f"INCOMPLETE: 作品 [{work_id}] 的规范 ID [{entity_id_value}] 跨类型复用"
                )
            id_categories[entity_id_value] = category_key
            key = (category_key, self._text_key(name))
            previous = mapping.get(key)
            if previous and previous != entity_id_value:
                raise RuntimeError(
                    f"INCOMPLETE: 作品 [{work_id}] 的规范名称 [{name}] 存在冲突映射"
                )
            mapping[key] = entity_id_value

        def add_mapping_group(raw_group: Any, category: Any = None) -> None:
            if not isinstance(raw_group, dict):
                raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的规范映射必须是对象")
            for name, entity_id in raw_group.items():
                add_mapping(name, entity_id, category)

        raw_mapping = data.get("canonical_ids", {})
        if raw_mapping is None:
            raw_mapping = {}
        if not isinstance(raw_mapping, dict):
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 canonical_ids 必须是对象")
        if any(isinstance(value, dict) for value in raw_mapping.values()):
            if not all(isinstance(value, dict) for value in raw_mapping.values()):
                raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 canonical_ids 类型分组不完整")
            for category, category_mapping in raw_mapping.items():
                add_mapping_group(category_mapping, category)
        else:
            # 兼容旧的 name -> id 格式；显式类型格式优先用于新配置。
            add_mapping_group(raw_mapping)

        raw_aliases = data.get("aliases", {})
        if raw_aliases is None:
            raw_aliases = {}
        if not isinstance(raw_aliases, dict):
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 aliases 必须是对象")
        if any(isinstance(value, dict) for value in raw_aliases.values()):
            if not all(isinstance(value, dict) for value in raw_aliases.values()):
                raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 aliases 类型分组不完整")
            for category, category_aliases in raw_aliases.items():
                add_mapping_group(category_aliases, category)
        else:
            add_mapping_group(raw_aliases)

        raw_entities = data.get("entities", [])
        if raw_entities is None:
            raw_entities = []
        if isinstance(raw_entities, list):
            entity_entries = [(None, entry) for entry in raw_entities]
        elif isinstance(raw_entities, dict):
            entity_entries = list(raw_entities.items())
        else:
            raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 entities 必须是列表或对象")
        for configured_id, entry in entity_entries:
            if not isinstance(entry, dict):
                raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 entities 含无效条目")
            entity_id = entry.get("id", configured_id)
            category = entry.get("type", entry.get("category", "character"))
            entity_id_value = self._validate_entity_id(
                entity_id, self._category_key(category), work_id
            )
            add_mapping(entry.get("name"), entity_id_value, category)
            aliases = entry.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
                raise RuntimeError(f"INCOMPLETE: 作品 [{work_id}] 的 entities.aliases 必须是字符串列表")
            for alias in aliases:
                add_mapping(alias, entity_id_value, category)
        return mapping
    def get_canonical_id(
        self,
        work_id: str,
        name: str,
        category: str = "character",
        default_id: Optional[str] = None
    ) -> str:
        """获取指定作品内的规范 ID；未知实体生成带作品和类型作用域的稳定 ID。"""
        if not isinstance(name, str):
            raise ValueError("实体名称必须是字符串")
        raw_name = name.strip()
        if not raw_name:
            raise ValueError("实体名称不能为空")
        self._work_root(work_id)
        category_key = self._category_key(category)

        # 先检查指定作品数据库中已有的名字或别名，绝不跨作品复用 ID。
        norm_key = self._text_key(raw_name)
        with self.db_client.get_connection() as conn:
            cur = conn.execute(
                "SELECT entity_id, name, category, aliases_json FROM entities WHERE work_id = ?",
                (work_id,)
            )
            for row in cur.fetchall():
                if self._category_key(row["category"]) != category_key:
                    continue
                if self._text_key(row["name"]) == norm_key:
                    return row["entity_id"]
                try:
                    aliases = json.loads(row["aliases_json"] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"INCOMPLETE: 作品 [{work_id}] 的实体 [{row['entity_id']}] 别名数据损坏"
                    ) from exc
                if not isinstance(aliases, list):
                    raise RuntimeError(
                        f"INCOMPLETE: 作品 [{work_id}] 的实体 [{row['entity_id']}] 别名数据必须是列表"
                    )
                for al in aliases:
                    if self._text_key(al) == norm_key:
                        return row["entity_id"]

        mapped_id = self._load_work_canonical_ids(work_id).get((category_key, norm_key))
        if mapped_id:
            return mapped_id

        if default_id is not None:
            if not isinstance(default_id, str) or not default_id.strip():
                raise ValueError("default_id 必须是非空字符串")
            explicit_id = default_id.strip()
            inferred_category = self._category_from_id(explicit_id)
            if inferred_category and inferred_category != category_key:
                raise ValueError(
                    f"default_id [{explicit_id}] 与实体类型 [{category_key}] 冲突"
                )
            return explicit_id

        seed = f"{work_id.casefold()}\x00{category_key}\x00{norm_key}"
        digest = sha256_hex(seed)[:16]
        return f"{category_key}_{digest}"


    def merge_entities(self, work_id: str, primary_id: str, duplicate_ids: list[str]) -> None:
        """
        将多个重复实体的档案、属性、别名、时序相态完整合并到 primary_id，
        并物理删除重复文件与 SQLite 记录。
        """
        category_dirs = ["characters", "items", "locations", "factions"]
        entities_root = self._work_root(work_id) / "entities"
        def find_file(eid: str) -> Optional[Path]:
            for cdir in category_dirs:
                cand = entities_root / cdir / f"{eid}.md"
                if cand.is_file():
                    return cand
            return None

        primary_file = find_file(primary_id)
        if not primary_file:
            raise FileNotFoundError(
                f"作品 [{work_id}] 的权威实体文件不存在: {primary_id}"
            )

        primary_meta, primary_body = read_markdown_frontmatter(primary_file)
        merged_aliases = set(primary_meta.get("aliases") or [])
        merged_attributes = dict(primary_meta.get("attributes") or {})
        merged_voice = dict(primary_meta.get("voice_profile") or {})
        if "voice_profile" in merged_attributes and isinstance(merged_attributes["voice_profile"], dict):
            for k, v in merged_attributes["voice_profile"].items():
                if v and not merged_voice.get(k):
                    merged_voice[k] = v

        for dup_id in duplicate_ids:
            if dup_id == primary_id:
                continue
            dup_file = find_file(dup_id)
            if dup_file:
                try:
                    dup_meta, dup_body = read_markdown_frontmatter(dup_file)
                    # 合并别名
                    dup_name = dup_meta.get("name")
                    if dup_name and dup_name != primary_meta.get("name"):
                        merged_aliases.add(dup_name)
                    for al in dup_meta.get("aliases") or []:
                        if al and al != primary_meta.get("name"):
                            merged_aliases.add(al)

                    # 合并属性
                    for k, v in (dup_meta.get("attributes") or {}).items():
                        if k not in merged_attributes or not merged_attributes[k]:
                            merged_attributes[k] = v
                        elif isinstance(v, dict) and isinstance(merged_attributes[k], dict):
                            for sub_k, sub_v in v.items():
                                if sub_v and not merged_attributes[k].get(sub_k):
                                    merged_attributes[k][sub_k] = sub_v

                    # 合并声线
                    dup_vp = dup_meta.get("voice_profile") or {}
                    for vk, vv in dup_vp.items():
                        if vv:
                            if vk in ("catchphrases", "gestures", "taboos"):
                                existing_list = merged_voice.setdefault(vk, [])
                                for item in vv:
                                    if item not in existing_list:
                                        existing_list.append(item)
                            elif not merged_voice.get(vk):
                                merged_voice[vk] = vv

                    # 追加描述
                    if dup_body.strip() and dup_body.strip() not in primary_body:
                        primary_body = (primary_body.strip() + "\n\n" + dup_body.strip()).strip()

                    # 删除重复文件
                    dup_file.unlink(missing_ok=True)
                except Exception as err:
                    raise RuntimeError(
                        f"无法读取或合并作品 [{work_id}] 的重复实体文件 {dup_file.name}"
                    ) from err

            # 数据库迁移：更新 entity_phases、causal_events、state_events 等外键指向
            with self.db_client.transaction() as cur:
                cur.execute(
                    "UPDATE entity_phases SET entity_id = ? WHERE work_id = ? AND entity_id = ?",
                    (primary_id, work_id, dup_id)
                )
                cur.execute(
                    "UPDATE state_events SET entity_id = ? WHERE work_id = ? AND entity_id = ?",
                    (primary_id, work_id, dup_id)
                )
                cur.execute(
                    "UPDATE character_skills SET entity_id = ? WHERE work_id = ? AND entity_id = ?",
                    (primary_id, work_id, dup_id)
                )
                cur.execute(
                    "UPDATE item_instances SET owner_entity_id = ? WHERE work_id = ? AND owner_entity_id = ?",
                    (primary_id, work_id, dup_id)
                )
                cur.execute(
                    "UPDATE entity_relations SET source_id = ? WHERE work_id = ? AND source_id = ?",
                    (primary_id, work_id, dup_id)
                )
                cur.execute(
                    "UPDATE entity_relations SET target_id = ? WHERE work_id = ? AND target_id = ?",
                    (primary_id, work_id, dup_id)
                )
                cur.execute(
                    "DELETE FROM entities WHERE work_id = ? AND entity_id = ?",
                    (work_id, dup_id)
                )

        # 写回 primary Markdown
        primary_meta["aliases"] = sorted(list(merged_aliases))
        primary_meta["attributes"] = merged_attributes
        if merged_voice:
            primary_meta["voice_profile"] = merged_voice
            primary_meta["attributes"]["voice_profile"] = merged_voice

        write_markdown_frontmatter(primary_file, primary_meta, primary_body)

        # 同步更新 SQLite entities 记录
        with self.db_client.transaction() as cur:
            rel_path = str(primary_file.relative_to(self.config.workspace_root))
            cur.execute(
                """
                INSERT INTO entities
                (entity_id, work_id, category, is_unique, name, aliases_json, attributes_yaml, file_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(work_id, entity_id) DO UPDATE SET
                    category = excluded.category,
                    is_unique = excluded.is_unique,
                    name = excluded.name,
                    aliases_json = excluded.aliases_json,
                    attributes_yaml = excluded.attributes_yaml,
                    file_path = excluded.file_path,
                    updated_at = excluded.updated_at
                """,
                (
                    primary_id,
                    work_id,
                    primary_meta.get("category", "character"),
                    1 if primary_meta.get("is_unique", True) else 0,
                    primary_meta.get("name", primary_id),
                    json.dumps(primary_meta["aliases"], ensure_ascii=False),
                    yaml.dump(primary_meta["attributes"], allow_unicode=True),
                    rel_path,
                )
            )

    def run_deduplication(self, work_id: str) -> dict[str, list[str]]:
        """全量扫描并去重合并作品下的所有重复实体"""
        entities_dir = self._work_root(work_id) / "entities"
        if not entities_dir.is_dir():
            return {}

        # 1. 扫描磁盘上所有实体
        from collections import defaultdict
        by_name: dict[tuple[str, str], list[tuple[str, Path]]] = defaultdict(list)
        display_names: dict[tuple[str, str], str] = {}
        for f in entities_dir.rglob("*.md"):
            try:
                meta, _ = read_markdown_frontmatter(f)
                name = meta.get("name", "").strip()
                eid = meta.get("entity_id", f.stem)
                if name:
                    category = self._category_key(meta.get("category", "character"))
                    key = (category, self._text_key(name))
                    by_name[key].append((eid, f))
                    display_names.setdefault(key, name)
            except Exception as exc:
                raise RuntimeError(
                    f"无法读取作品 [{work_id}] 的实体文件 {f.name}"
                ) from exc

        merged_report: dict[str, list[str]] = {}
        canonical_ids = self._load_work_canonical_ids(work_id)

        for (category, name_key), entries in by_name.items():
            if len(entries) <= 1:
                continue

            # 选定权威 primary_id
            eids = [eid for eid, _ in entries]
            name = display_names[(category, name_key)]
            canonical_target = canonical_ids.get((category, name_key))
            if canonical_target:
                if canonical_target in eids:
                    primary = canonical_target
                else:
                    candidates = sorted(eids, key=lambda x: (
                        1 if "_" in x and not x.startswith("character_") and not x.startswith("item_") else 2,
                        len(x)
                    ))
                    best_old_id = candidates[0]
                    best_old_file = next(f for eid, f in entries if eid == best_old_id)
                    primary_file = best_old_file.parent / f"{canonical_target}.md"
                    meta, body = read_markdown_frontmatter(best_old_file)
                    meta["entity_id"] = canonical_target
                    write_markdown_frontmatter(primary_file, meta, body)
                    best_old_file.unlink(missing_ok=True)
                    eids.remove(best_old_id)
                    eids.append(canonical_target)
                    primary = canonical_target
            else:
                # 优先选择规范命名的 ID (包含下划线，非随机哈希)
                candidates = sorted(eids, key=lambda x: (
                    1 if "_" in x and not x.startswith("character_") and not x.startswith("item_") else 2,
                    len(x)
                ))
                primary = candidates[0]

            duplicates = [eid for eid in eids if eid != primary]
            self.merge_entities(work_id, primary, duplicates)
            merged_report[primary] = duplicates

        return merged_report
