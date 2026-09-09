"""
fxi.domain.entities - 故事世界实体管理器 (人物/物品/地理/势力)
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import CorruptedDataError, NotFoundError, ValidationError
from fxi.core.identifiers import validate_work_id as _validate_work_id
from fxi.domain.canonical import CanonicalRegistry
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.text_io import write_markdown_frontmatter


def validate_work_id(work_id: str) -> str:
    """Validate the explicit work scope before it reaches SQL or filesystem paths."""
    try:
        return _validate_work_id(work_id)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def load_work_config(
    config: FxiConfig,
    work_id: str,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read only the requested work's configuration and expose missing data explicitly."""
    work_id = validate_work_id(work_id)
    path = config.projects_dir / work_id / "work.yaml"
    if not path.is_file():
        return None, "WORK_CONFIG_MISSING"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None, "WORK_CONFIG_UNREADABLE"
    if not isinstance(raw, Mapping):
        return None, "WORK_CONFIG_INVALID"
    declared_work_id = raw.get("work_id")
    if declared_work_id not in (None, work_id):
        return None, "WORK_CONFIG_SCOPE_MISMATCH"
    return dict(raw), None


def get_worldview_genre(settings: Mapping[str, Any]) -> Optional[str]:
    """Return the explicitly configured genre without inventing a project default."""
    direct = settings.get("worldview_genre") or settings.get("genre")
    worldview = settings.get("worldview")
    if direct is None and isinstance(worldview, Mapping):
        direct = worldview.get("worldview_genre") or worldview.get("genre") or worldview.get("type")
    return direct.strip() if isinstance(direct, str) and direct.strip() else None


def select_work_section(
    settings: Mapping[str, Any],
    section_name: str,
    genre: Optional[str] = None,
) -> Any:
    """Select a work-level section, then an optional genre-specific subsection."""
    section = settings.get(section_name)
    if section is None:
        for parent_name in ("rules", "worldview_rules", "worldviews", "genres"):
            parent = settings.get(parent_name)
            if not isinstance(parent, Mapping):
                continue
            if section_name in parent:
                section = parent[section_name]
                break
            if genre and isinstance(parent.get(genre), Mapping):
                section = parent[genre].get(section_name)
                if section is not None:
                    break
    if isinstance(section, Mapping) and genre and isinstance(section.get(genre), Mapping):
        return section[genre]
    return section


@dataclass
class Vessel:
    """物理肉身 (Vessel)：物理存在、宿主标识、外貌体质与物理状态"""
    vessel_id: str
    name: str = ""
    location: str = ""
    status: str = "alive"  # alive | injured | unconscious | dead | sealed
    physique: str = ""     # e.g. "荒古圣体", "五行杂灵根"
    appearance: str = ""
    cultivation_physical: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Vessel":
        return cls(
            vessel_id=str(data.get("vessel_id", "")),
            name=str(data.get("name", "")),
            location=str(data.get("location", "")),
            status=str(data.get("status", "alive")),
            physique=str(data.get("physique", "")),
            appearance=str(data.get("appearance", "")),
            cultivation_physical=str(data.get("cultivation_physical", "")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Soul:
    """意识灵魂 (Soul)：真实真名、思维性格、万年秘密、法则掌握与灵魂境界"""
    soul_id: str
    true_name: str = ""
    realm: str = ""                # e.g. "仙尊残魂", "至尊神魂"
    core_secrets: list[str] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    dao_laws: list[str] = field(default_factory=list)
    is_controller: bool = True     # 是否为主控灵魂
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Soul":
        return cls(
            soul_id=str(data.get("soul_id", "")),
            true_name=str(data.get("true_name", "")),
            realm=str(data.get("realm", "")),
            core_secrets=list(data.get("core_secrets") or []),
            memories=list(data.get("memories") or []),
            dao_laws=list(data.get("dao_laws") or []),
            is_controller=bool(data.get("is_controller", True)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Persona:
    """社会相态 (Persona / Phase)：当前激活身份面具、公开声称、对外称谓与言行规则"""
    persona_id: str
    display_name: str = ""
    claimed_identity: str = ""     # 对外自称/社会身份
    is_disguise: bool = False      # 是否伪装/夺舍伪装
    speech_channel: str = "physical_dialogue"  # physical_dialogue | telepathy_inner | dormant
    voice_profile: dict[str, Any] = field(default_factory=dict)
    anti_behaviors: list[str] = field(default_factory=list)
    tone_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Persona":
        return cls(
            persona_id=str(data.get("persona_id", "")),
            display_name=str(data.get("display_name", "")),
            claimed_identity=str(data.get("claimed_identity", "")),
            is_disguise=bool(data.get("is_disguise", False)),
            speech_channel=str(data.get("speech_channel", "physical_dialogue")),
            voice_profile=dict(data.get("voice_profile") or {}),
            anti_behaviors=list(data.get("anti_behaviors") or []),
            tone_tags=list(data.get("tone_tags") or []),
            metadata=dict(data.get("metadata") or {}),
        )


class EntityManager:
    """实体模型管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.canonical_registry = CanonicalRegistry(self.config)

    def get_entity(self, work_id: str, entity_id: str) -> dict[str, Any]:
        """获取单个实体设定"""
        work_id = validate_work_id(work_id)
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
            }

    def get_entity_abilities(
        self,
        work_id: str,
        entity_name_or_id: str,
        chapter: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """获取指定角色在特定章节（或全篇）生效的超凡能力与技能体系"""
        work_id = validate_work_id(work_id)
        canonical_id = self.canonical_registry.get_canonical_id(
            work_id, entity_name_or_id, category="character"
        )
        entity_id = entity_name_or_id if entity_name_or_id.startswith("char_") else canonical_id
        entity_id = entity_id or entity_name_or_id
        if not entity_id.startswith("char_") and not entity_id.startswith("item_"):
            with self.db_client.get_connection() as conn:
                row = conn.execute(
                    "SELECT entity_id FROM entities WHERE work_id = ? AND (name = ? OR entity_id = ?)",
                    (work_id, entity_name_or_id, entity_name_or_id),
                ).fetchone()
                if row:
                    entity_id = row["entity_id"]

        with self.db_client.get_connection() as conn:
            if chapter is not None:
                cur = conn.execute(
                    """
                    SELECT * FROM entity_abilities
                    WHERE work_id = ? AND entity_id = ?
                      AND valid_from_chapter <= ?
                      AND (valid_to_chapter IS NULL OR valid_to_chapter >= ?)
                    ORDER BY valid_from_chapter ASC, ability_name ASC
                    """,
                    (work_id, entity_id, int(chapter), int(chapter)),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT * FROM entity_abilities
                    WHERE work_id = ? AND entity_id = ?
                    ORDER BY valid_from_chapter ASC, ability_name ASC
                    """,
                    (work_id, entity_id),
                )
            results = []
            for r in cur.fetchall():
                results.append({
                    "ability_id": r["ability_id"],
                    "work_id": r["work_id"],
                    "entity_id": r["entity_id"],
                    "ability_name": r["ability_name"],
                    "category": r["category"],
                    "sequence_num": r["sequence_num"],
                    "valid_from_chapter": r["valid_from_chapter"],
                    "valid_to_chapter": r["valid_to_chapter"],
                    "cost_description": r["cost_description"],
                    "effect_description": r["effect_description"],
                    "source_origin": r["source_origin"],
                    "metadata": json.loads(r["metadata_json"] or "{}"),
                })
            return results

    def get_effective_state(
        self,
        work_id: str,
        character_name_or_id: str,
        chapter: int,
        base_work_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        动态合成特定章节该实体的真实有效状态与超凡能力集。
        公式：EffectiveState = CanonBaseline(chapter) ⊕ AppliedMutations(<= chapter)
        保证既有原著基准时序的约束，又支持同人因果蝴蝶效应的合法魔改。
        """
        from fxi.domain.mutation_ledger import MutationLedger

        work_id = validate_work_id(work_id)
        if base_work_id is not None:
            base_work_id = validate_work_id(base_work_id)
        canonical_id = self.canonical_registry.get_canonical_id(
            work_id, character_name_or_id, category="character"
        )
        entity_id = character_name_or_id if character_name_or_id.startswith("char_") else canonical_id
        entity_id = entity_id or character_name_or_id

        # 只有调用方显式提供 base_work_id 时，才读取另一个作品作为基准。
        source_work_id = work_id
        canon_abilities = self.get_entity_abilities(source_work_id, character_name_or_id, chapter=chapter)
        if not canon_abilities and base_work_id:
            source_work_id = base_work_id
            canon_abilities = self.get_entity_abilities(source_work_id, character_name_or_id, chapter=chapter)

        # 获取实体基础信息（如名字）
        entity_name = character_name_or_id
        entity_card = None
        for candidate_work_id in dict.fromkeys((source_work_id, work_id)):
            try:
                entity_card = self.get_entity(candidate_work_id, entity_id)
                break
            except NotFoundError:
                continue

        if entity_card:
            entity_name = entity_card.get("name", character_name_or_id)

        # 读取变动账本
        ledger = MutationLedger(self.config, self.db_client)
        mutations = ledger.list_mutations(work_id, chapter=chapter, entity_name_or_id=entity_id, status="active")

        # 整理基准能力字典
        ability_map: dict[str, dict[str, Any]] = {}
        for ab in canon_abilities:
            name = ab["ability_name"]
            ability_map[name] = {
                **ab,
                "origin": "canon",
                "origin_label": f"原著第{ab['valid_from_chapter']}章解锁",
                "is_mutated": False,
            }

        # 依次叠加同人因果变动
        applied_mutations: list[dict[str, Any]] = []
        for m in mutations:
            target = m.target_name
            if m.mutation_type == "ability_grant":
                is_early = False
                prev_ch = m.original_canon_chapter
                if prev_ch and prev_ch > chapter:
                    is_early = True
                    origin_label = f"因果变动：外部干预提前于第{m.trigger_chapter}章生效 (原著第{prev_ch}章)"
                else:
                    origin_label = f"因果变动：第{m.trigger_chapter}章获得 ({m.cause_event})"

                ability_map[target] = {
                    "ability_id": m.mutation_id,
                    "work_id": work_id,
                    "entity_id": entity_id,
                    "ability_name": target,
                    "category": m.payload.get("category", "acquired"),
                    "sequence_num": m.payload.get("sequence_num"),
                    "valid_from_chapter": m.trigger_chapter,
                    "valid_to_chapter": None,
                    "cost_description": m.payload.get("cost_description", ""),
                    "effect_description": m.payload.get("effect_description", m.cause_event),
                    "source_origin": f"mutation:{m.mutation_id}",
                    "metadata": m.payload,
                    "origin": "mutation",
                    "origin_label": origin_label,
                    "is_mutated": True,
                    "cause_event": m.cause_event,
                    "is_early_awakened": is_early,
                }
                applied_mutations.append(m.to_dict())

            elif m.mutation_type == "ability_modify":
                if target in ability_map:
                    item = ability_map[target]
                    item["is_mutated"] = True
                    item["modified_by"] = m.mutation_id
                    item["modify_cause"] = m.cause_event
                    item["origin_label"] += f" (第{m.trigger_chapter}章因果改变: {m.cause_event})"
                    if "cost_description" in m.payload:
                        item["cost_description"] = m.payload["cost_description"]
                    if "effect_description" in m.payload:
                        item["effect_description"] = m.payload["effect_description"]
                    applied_mutations.append(m.to_dict())

            elif m.mutation_type == "ability_suppress":
                if target in ability_map:
                    ability_map.pop(target, None)
                    applied_mutations.append(m.to_dict())

        effective_abilities = sorted(
            ability_map.values(),
            key=lambda x: (x.get("valid_from_chapter", 1), x.get("ability_name", "")),
        )

        return {
            "work_id": work_id,
            "source_work_id": source_work_id,
            "entity_id": entity_id,
            "name": entity_name,
            "chapter": chapter,
            "canon_abilities_count": len(canon_abilities),
            "mutations_applied_count": len(applied_mutations),
            "effective_abilities": effective_abilities,
            "applied_mutations": applied_mutations,
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
        voice_profile: Optional[dict[str, Any]] = None,
    ) -> None:
        """持久化落盘实体至 Markdown 文件并同步到 SQLite"""
        work_id = validate_work_id(work_id)
        aliases = aliases or []
        attributes = attributes or {}
        if voice_profile and "voice_profile" not in attributes:
            attributes["voice_profile"] = voice_profile

        # 实体权威 ID 归一化守卫
        canonical_id = self.canonical_registry.get_canonical_id(work_id, name, category, default_id=entity_id)
        if canonical_id and canonical_id != entity_id:
            if entity_id not in aliases and not entity_id.startswith("character_") and not entity_id.startswith("item_"):
                aliases.append(entity_id)
            entity_id = canonical_id


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
        if voice_profile:
            metadata["voice_profile"] = voice_profile

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

    def get_character_voice(self, work_id: str, char_identifier: str) -> dict[str, Any]:
        """按 ID、姓名或别名查询角色声线档案"""
        from fxi.storage.text_io import read_markdown_frontmatter
        work_id = validate_work_id(work_id)
        char_dir = self.config.projects_dir / work_id / "entities" / "characters"
        if not char_dir.is_dir():
            return {}
        for f in char_dir.glob("*.md"):
            try:
                meta, _ = read_markdown_frontmatter(f)
                cid = meta.get("entity_id", f.stem)
                cname = meta.get("name", "")
                aliases = meta.get("aliases", [])
                if char_identifier in (cid, cname, f.stem) or char_identifier in aliases:
                    attrs = meta.get("attributes", {})
                    if isinstance(attrs, dict):
                        # 优先从三位一体当前激活的相态中检索 voice_profile
                        trinity = attrs.get("trinity", {})
                        if isinstance(trinity, dict):
                            act_pid = trinity.get("active_persona_id")
                            personas = trinity.get("personas", {})
                            if isinstance(personas, dict) and act_pid and act_pid in personas:
                                persona_vp = personas[act_pid].get("voice_profile")
                                if persona_vp:
                                    return persona_vp
                            elif isinstance(personas, dict) and personas:
                                # 安全回退：若未显式指定 active_persona_id，默认取首个相态的 voice_profile
                                first_persona = next(iter(personas.values()), {})
                                if isinstance(first_persona, dict):
                                    persona_vp = first_persona.get("voice_profile")
                                    if persona_vp:
                                        return persona_vp
                        if attrs.get("voice_profile"):
                            return attrs.get("voice_profile")
                    if meta.get("voice_profile"):
                        return meta.get("voice_profile")
            except (CorruptedDataError, OSError, UnicodeError, yaml.YAMLError):
                continue
        return {}

    def list_entities(self, work_id: str, category: Optional[str] = None) -> list[dict[str, Any]]:
        """列出指定作品下的实体"""
        work_id = validate_work_id(work_id)
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

    def get_trinity_state(self, work_id: str, entity_id: str) -> dict[str, Any]:
        """获取实体的三位一体（肉身 Vessel / 灵魂 Soul / 相态 Persona）架构状态"""
        try:
            entity = self.get_entity(work_id, entity_id)
            attrs = entity.get("attributes", {})
            return dict(attrs.get("trinity", {}))
        except NotFoundError:
            return {}

    def save_trinity_state(self, work_id: str, entity_id: str, trinity_data: dict[str, Any]) -> None:
        """保存三位一体状态至实体属性中"""
        entity = self.get_entity(work_id, entity_id)
        attrs = dict(entity.get("attributes", {}))
        attrs["trinity"] = trinity_data
        self.upsert_entity(
            work_id=work_id,
            entity_id=entity_id,
            name=entity.get("name", entity_id),
            category=entity.get("category", "character"),
            is_unique=entity.get("is_unique", True),
            aliases=entity.get("aliases", []),
            attributes=attrs,
        )

    def configure_possession(
        self,
        work_id: str,
        entity_id: str,
        soul: Soul,
        vessel: Vessel,
        disguise_persona: Persona,
    ) -> None:
        """配置夺舍关系：灵魂占据异体肉身，对外维持伪装相态"""
        disguise_persona.is_disguise = True
        trinity = {
            "mode": "possession",
            "vessels": {vessel.vessel_id: vessel.to_dict()},
            "souls": {soul.soul_id: soul.to_dict()},
            "personas": {disguise_persona.persona_id: disguise_persona.to_dict()},
            "active_vessel_id": vessel.vessel_id,
            "active_soul_id": soul.soul_id,
            "active_persona_id": disguise_persona.persona_id,
            "is_possessed": True,
        }
        try:
            entity = self.get_entity(work_id, entity_id)
            attrs = dict(entity.get("attributes", {}))
            attrs["trinity"] = trinity
            self.upsert_entity(
                work_id=work_id,
                entity_id=entity_id,
                name=disguise_persona.display_name or entity.get("name", entity_id),
                category="character",
                is_unique=True,
                aliases=entity.get("aliases", []),
                attributes=attrs,
            )
        except NotFoundError:
            self.upsert_entity(
                work_id=work_id,
                entity_id=entity_id,
                name=disguise_persona.display_name or vessel.name,
                category="character",
                is_unique=True,
                aliases=[vessel.name] if vessel.name and vessel.name != disguise_persona.display_name else [],
                attributes={"trinity": trinity},
            )

    def configure_dual_souls(
        self,
        work_id: str,
        entity_id: str,
        vessel: Vessel,
        primary_soul: Soul,
        symbiotic_soul: Soul,
        primary_persona: Optional[Persona] = None,
        symbiotic_persona: Optional[Persona] = None,
        active_controller_soul_id: Optional[str] = None,
    ) -> None:
        """配置双魂共生关系：单肉身内共存多个灵魂，主控者外部发声，共生者脑内传音"""
        p_persona = primary_persona or Persona(
            persona_id=f"persona_{primary_soul.soul_id}",
            display_name=primary_soul.true_name,
            speech_channel="physical_dialogue",
        )
        s_persona = symbiotic_persona or Persona(
            persona_id=f"persona_{symbiotic_soul.soul_id}",
            display_name=symbiotic_soul.true_name,
            speech_channel="telepathy_inner",
        )
        active_soul_id = active_controller_soul_id or primary_soul.soul_id
        trinity = {
            "mode": "dual_souls",
            "vessels": {vessel.vessel_id: vessel.to_dict()},
            "souls": {
                primary_soul.soul_id: primary_soul.to_dict(),
                symbiotic_soul.soul_id: symbiotic_soul.to_dict(),
            },
            "personas": {
                p_persona.persona_id: p_persona.to_dict(),
                s_persona.persona_id: s_persona.to_dict(),
            },
            "active_vessel_id": vessel.vessel_id,
            "active_soul_id": active_soul_id,
            "active_persona_id": p_persona.persona_id if active_soul_id == primary_soul.soul_id else s_persona.persona_id,
        }
        try:
            entity = self.get_entity(work_id, entity_id)
            attrs = dict(entity.get("attributes", {}))
            attrs["trinity"] = trinity
            self.upsert_entity(
                work_id=work_id,
                entity_id=entity_id,
                name=entity.get("name", vessel.name),
                category="character",
                is_unique=True,
                aliases=entity.get("aliases", []),
                attributes=attrs,
            )
        except NotFoundError:
            self.upsert_entity(
                work_id=work_id,
                entity_id=entity_id,
                name=vessel.name or primary_soul.true_name,
                category="character",
                is_unique=True,
                aliases=[],
                attributes={"trinity": trinity},
            )

    def switch_soul_controller(self, work_id: str, entity_id: str, new_controller_soul_id: str) -> None:
        """切换肉身主控灵魂。"""
        trinity = self.get_trinity_state(work_id, entity_id)
        if not trinity:
            raise NotFoundError(f"实体 {entity_id} 未配置三位一体模型")
        souls = trinity.get("souls", {})
        if new_controller_soul_id not in souls:
            raise ValueError(f"灵魂 {new_controller_soul_id} 不在实体 {entity_id} 的灵魂列表中")

        trinity["active_soul_id"] = new_controller_soul_id
        # 寻找该灵魂匹配的 persona
        personas = trinity.get("personas", {})
        for pid, pdata in personas.items():
            if pid.endswith(new_controller_soul_id) or pdata.get("display_name") == souls[new_controller_soul_id].get("true_name"):
                trinity["active_persona_id"] = pid
                break
        self.save_trinity_state(work_id, entity_id, trinity)

    def validate_dialogue_channel(
        self,
        work_id: str,
        entity_id: str,
        speaker_soul_id: str,
        channel: str = "physical_dialogue",
    ) -> tuple[bool, str]:
        """
        校验对白发声信道合法性：
        非主控灵魂（如寄宿残魂）严禁使用 physical_dialogue 物理发声，只允许 telepathy_inner 脑内传音。
        """
        trinity = self.get_trinity_state(work_id, entity_id)
        if not trinity:
            return True, "OK"
        active_soul_id = trinity.get("active_soul_id")
        if speaker_soul_id == active_soul_id:
            return True, "OK"

        # 属于共生灵魂但非主控
        if channel == "physical_dialogue":
            return False, f"灵魂 [{speaker_soul_id}] 非当前肉身控制者，严禁物理发声，仅允许灵识传音(telepathy_inner)"
        return True, "OK"

    def add_persona(self, work_id: str, entity_id: str, persona: Persona) -> None:
        """为实体增加多重人格相态"""
        trinity = self.get_trinity_state(work_id, entity_id)
        if not trinity:
            trinity = {
                "vessels": {},
                "souls": {},
                "personas": {},
                "active_persona_id": persona.persona_id,
            }
        personas = dict(trinity.get("personas", {}))
        personas[persona.persona_id] = persona.to_dict()
        trinity["personas"] = personas
        if "active_persona_id" not in trinity or not trinity["active_persona_id"]:
            trinity["active_persona_id"] = persona.persona_id
        self.save_trinity_state(work_id, entity_id, trinity)

    def switch_persona(self, work_id: str, entity_id: str, persona_id: str) -> None:
        """切换当前激活的性格人格相态"""
        trinity = self.get_trinity_state(work_id, entity_id)
        personas = trinity.get("personas", {})
        if persona_id not in personas:
            raise ValueError(f"人格相态 {persona_id} 未在实体 {entity_id} 中注册")
        trinity["active_persona_id"] = persona_id
        self.save_trinity_state(work_id, entity_id, trinity)

    def create_avatar(
        self,
        work_id: str,
        main_entity_id: str,
        avatar_id: str,
        avatar_vessel: Vessel,
        avatar_persona: Optional[Persona] = None,
    ) -> dict[str, Any]:
        """创建身外化身（分身）：独立物理肉身与地理坐标，独立经历记忆"""
        trinity = self.get_trinity_state(work_id, main_entity_id)
        if not trinity:
            entity = self.get_entity(work_id, main_entity_id)
            trinity = {
                "vessels": {f"vessel_{main_entity_id}": {"vessel_id": f"vessel_{main_entity_id}", "name": entity["name"]}},
                "souls": {f"soul_{main_entity_id}": {"soul_id": f"soul_{main_entity_id}", "true_name": entity["name"], "memories": []}},
                "active_soul_id": f"soul_{main_entity_id}",
                "avatars": {},
            }
        avatars = dict(trinity.get("avatars", {}))
        avatars[avatar_id] = {
            "avatar_id": avatar_id,
            "vessel": avatar_vessel.to_dict(),
            "persona": (avatar_persona or Persona(persona_id=avatar_id, display_name=avatar_vessel.name)).to_dict(),
            "memories": [],
            "synced": True,
        }
        trinity["avatars"] = avatars
        self.save_trinity_state(work_id, main_entity_id, trinity)
        return avatars[avatar_id]

    def record_avatar_experience(
        self,
        work_id: str,
        main_entity_id: str,
        avatar_id: str,
        memory_entry: str,
    ) -> None:
        """记录分身在远端的独立经历（未同步前本尊不知情）"""
        trinity = self.get_trinity_state(work_id, main_entity_id)
        avatars = trinity.get("avatars", {})
        if avatar_id not in avatars:
            raise NotFoundError(f"分身 {avatar_id} 不存在")
        avatars[avatar_id]["memories"].append(memory_entry)
        avatars[avatar_id]["synced"] = False
        self.save_trinity_state(work_id, main_entity_id, trinity)

    def sync_avatar_memories(
        self,
        work_id: str,
        main_entity_id: str,
        avatar_id: str,
    ) -> list[str]:
        """因果合流：分身将远端累积的认知与记忆同步给本尊灵魂"""
        trinity = self.get_trinity_state(work_id, main_entity_id)
        avatars = trinity.get("avatars", {})
        if avatar_id not in avatars:
            raise NotFoundError(f"分身 {avatar_id} 不存在")
        avatar_record = avatars[avatar_id]
        avatar_mems = avatar_record.get("memories", [])

        active_soul_id = trinity.get("active_soul_id")
        souls = trinity.get("souls", {})
        if active_soul_id and active_soul_id in souls:
            main_mems = souls[active_soul_id].setdefault("memories", [])
            for m in avatar_mems:
                if m not in main_mems:
                    main_mems.append(m)
        avatar_record["synced"] = True
        self.save_trinity_state(work_id, main_entity_id, trinity)
        return list(avatar_mems)

    def get_pov_entity_view(
        self,
        work_id: str,
        observer_char_id: str,
        target_entity_id: str,
        known_claim_ids: Optional[set[str]] = None,
    ) -> dict[str, Any]:
        """
        根据观察者 POV 生成目标实体的安全认知视图：
        - 本人/已获知机密者：可见完整灵魂真名、法则秘密与真实修为；
        - 外人视角：仅展示表面肉身特征、伪装相态与公开声称，真实灵魂机密物理脱敏！
        """
        entity = self.get_entity(work_id, target_entity_id)
        trinity = entity.get("attributes", {}).get("trinity")
        if not trinity:
            return {
                "entity_id": entity["entity_id"],
                "work_id": entity["work_id"],
                "name": entity["name"],
                "category": entity["category"],
                "is_redacted": False,
            }

        known_claim_ids = known_claim_ids or set()
        active_soul_id = trinity.get("active_soul_id", "")
        active_vessel_id = trinity.get("active_vessel_id", "")
        is_self = observer_char_id in (target_entity_id, active_soul_id, active_vessel_id)

        # 检查观察者是否拥有揭露夺舍/真相的 claim
        has_secret_revealed = any(
            cid in known_claim_ids for cid in (
                f"{target_entity_id}_possession_revealed",
                f"{target_entity_id}_true_identity",
                f"claim_{target_entity_id}_possession",
            )
        )

        active_persona_id = trinity.get("active_persona_id")
        personas = trinity.get("personas", {})
        active_persona = personas.get(active_persona_id, {})

        active_vessel = trinity.get("vessels", {}).get(active_vessel_id, {})
        souls = trinity.get("souls", {})
        active_soul = souls.get(active_soul_id, {})

        if is_self or has_secret_revealed:
            # 完整上帝/知情者/本人视角
            return {
                "entity_id": target_entity_id,
                "work_id": work_id,
                "name": active_soul.get("true_name") or entity["name"],
                "is_possessed": bool(trinity.get("is_possessed", False)),
                "active_soul": dict(active_soul),
                "active_vessel": dict(active_vessel),
                "active_persona": dict(active_persona),
                "is_redacted": False,
            }
        else:
            # 外人/受限 POV 视角：物理脱敏！
            display_name = active_persona.get("display_name") or entity["name"]
            claimed_id = active_persona.get("claimed_identity") or display_name
            return {
                "entity_id": target_entity_id,
                "work_id": work_id,
                "name": display_name,
                "claimed_identity": claimed_id,
                "is_possessed": False,  # 外人不知晓夺舍
                "active_vessel": {
                    "vessel_id": active_vessel.get("vessel_id", ""),
                    "appearance": active_vessel.get("appearance", ""),
                    "physique": active_vessel.get("physique", ""),
                    "status": active_vessel.get("status", "alive"),
                    "location": active_vessel.get("location", ""),
                },
                "active_persona": {
                    "persona_id": active_persona.get("persona_id", ""),
                    "display_name": display_name,
                    "claimed_identity": claimed_id,
                },
                "active_soul": None,  # 物理抹除
                "core_secrets": [],   # 物理抹除
                "dao_laws": [],       # 物理抹除
                "is_redacted": True,
            }
