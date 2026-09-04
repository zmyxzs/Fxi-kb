"""
fxi.storage.sqlite_client - SQLite 连接池与 Schema 管理器
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional


# 完整的 Schema DDL 定义 (基于 dev-docs/05-data-schema-and-sqlite-ddl.md)
MANIFEST_SCHEMA_DDL = """
-- 1. 基础元数据与作品表 (完全兼容 novel-Skill)
CREATE TABLE IF NOT EXISTS authors (
    owner_id TEXT PRIMARY KEY NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS works (
    work_id TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    genre_ids_json TEXT NOT NULL DEFAULT '[]',
    source_dir TEXT,
    skill_root TEXT,
    divergence_anchor TEXT, -- 同人分歧点锚点 (scene_uuid)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_id) REFERENCES authors(owner_id)
);

-- 2. 故事实体与相态表
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    category TEXT NOT NULL, -- character | item | location | faction
    is_unique INTEGER NOT NULL DEFAULT 1, -- 1 为唯一孤品，0 为量产原型
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    attributes_yaml TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, entity_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE TABLE IF NOT EXISTS entity_phases (
    phase_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    valid_from_order INTEGER NOT NULL DEFAULT 0,
    valid_to_order INTEGER, -- NULL 表示生效至今
    traits_json TEXT NOT NULL DEFAULT '[]',
    anti_behaviors_json TEXT NOT NULL DEFAULT '[]',
    tone_examples_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, entity_id, phase_id),
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    valid_from_order INTEGER NOT NULL DEFAULT 0,
    valid_to_order INTEGER,
    FOREIGN KEY(work_id, source_id) REFERENCES entities(work_id, entity_id)
);

-- 2.1 量产物品原型与背包持有表
CREATE TABLE IF NOT EXISTS item_prototypes (
    prototype_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    specs_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(work_id, prototype_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE TABLE IF NOT EXISTS item_instances (
    instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    owner_entity_id TEXT NOT NULL,
    item_type TEXT NOT NULL, -- unique_item | prototype
    item_ref_id TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    current_durability REAL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id, owner_entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS item_ownership_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    item_ref_id TEXT NOT NULL,
    from_owner_id TEXT,
    to_owner_id TEXT,
    transfer_type TEXT NOT NULL, -- looted | gifted | stolen | purchased | destroyed
    scene_uuid TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 3. 主张、凭证与合法吃书表
CREATE TABLE IF NOT EXISTS claim_families (
    family_key TEXT PRIMARY KEY NOT NULL,
    claim_family_id TEXT NOT NULL,
    work_id TEXT,
    owner_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    family_key TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    claim_family_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    work_id TEXT,
    status TEXT NOT NULL, -- accepted | candidate | superseded | rejected
    statement TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(family_key, version),
    FOREIGN KEY(family_key) REFERENCES claim_families(family_key)
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    scene_uuid TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    quote TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retcon_declarations (
    retcon_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    superseded_claim_id TEXT NOT NULL,
    new_claim_id TEXT NOT NULL,
    effective_narrative_order INTEGER NOT NULL,
    author_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

-- 4. 时空因果 DAG、多世界线与时间回溯表
CREATE TABLE IF NOT EXISTS timelines (
    timeline_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_timeline_id TEXT,
    fork_narrative_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(work_id, timeline_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE TABLE IF NOT EXISTS causal_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    timeline_id TEXT NOT NULL,
    scene_uuid TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    physical_time TEXT NOT NULL,
    summary TEXT NOT NULL,
    is_canon INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'untouched', -- untouched | mutated | invalidated
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE TABLE IF NOT EXISTS causal_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    cause_event_id TEXT NOT NULL,
    effect_event_id TEXT NOT NULL,
    link_type TEXT NOT NULL DEFAULT 'direct_cause',
    FOREIGN KEY(cause_event_id) REFERENCES causal_events(event_id),
    FOREIGN KEY(effect_event_id) REFERENCES causal_events(event_id)
);

CREATE TABLE IF NOT EXISTS reversion_checkpoints (
    checkpoint_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    timeline_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    physical_timestamp TEXT NOT NULL,
    world_state_json TEXT NOT NULL,
    retained_entities_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

-- 5. 动态状态账本表 (三态数值与基准锚点)
CREATE TABLE IF NOT EXISTS state_metrics (
    metric_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    unit TEXT NOT NULL,
    status_type TEXT NOT NULL DEFAULT 'EXPLICIT', -- EXPLICIT | UNMEASURED | NOT_APPLICABLE
    allows_negative INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(work_id, metric_id)
);

CREATE TABLE IF NOT EXISTS state_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    delta REAL NOT NULL,
    new_value REAL,
    is_anchor INTEGER NOT NULL DEFAULT 0, -- 1 为基准锚点
    scene_uuid TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    reason TEXT NOT NULL,
    rule_version TEXT NOT NULL DEFAULT 'v1',
    created_at TEXT NOT NULL,
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS state_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    computed_value REAL,
    status TEXT NOT NULL, -- EXPLICIT | UNMEASURED | NOT_APPLICABLE
    based_on_event_id INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE(work_id, entity_id, metric_id, narrative_order)
);

-- 5.1 【微观能力体系】角色属性、技能树与装备栏
CREATE TABLE IF NOT EXISTS character_attributes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    primary_class TEXT NOT NULL,
    secondary_class TEXT,
    level INTEGER NOT NULL DEFAULT 1,
    experience INTEGER NOT NULL DEFAULT 0,
    free_stat_points INTEGER NOT NULL DEFAULT 0,
    base_stats_json TEXT NOT NULL DEFAULT '{}',
    derived_stats_json TEXT NOT NULL DEFAULT '{}',
    narrative_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS skills_tree (
    skill_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL,
    required_class TEXT,
    tier INTEGER NOT NULL DEFAULT 1,
    skill_type TEXT NOT NULL DEFAULT 'active', -- active | passive | aura | ultimate
    cost_mp INTEGER NOT NULL DEFAULT 0,
    cooldown_narrative_steps INTEGER NOT NULL DEFAULT 0,
    prerequisite_skill_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(work_id, skill_id)
);

CREATE TABLE IF NOT EXISTS character_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    skill_level INTEGER NOT NULL DEFAULT 1,
    proficiency INTEGER NOT NULL DEFAULT 0,
    is_equipped INTEGER NOT NULL DEFAULT 0,
    last_cast_narrative_order INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id),
    FOREIGN KEY(work_id, skill_id) REFERENCES skills_tree(work_id, skill_id)
);

-- 5.2 【宏观据点基业】领地/宗门治理、资源与建筑拓扑
CREATE TABLE IF NOT EXISTS territory_ledgers (
    territory_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    lord_entity_id TEXT NOT NULL,
    tier_level TEXT NOT NULL,
    population_total INTEGER NOT NULL DEFAULT 100,
    population_soldiers INTEGER NOT NULL DEFAULT 10,
    loyalty_score REAL NOT NULL DEFAULT 80.0,
    security_score REAL NOT NULL DEFAULT 85.0,
    tax_rate REAL NOT NULL DEFAULT 0.15,
    narrative_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, territory_id),
    FOREIGN KEY(work_id, lord_entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS territory_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    territory_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    current_amount REAL NOT NULL,
    daily_net_yield REAL NOT NULL DEFAULT 0.0,
    storage_limit REAL NOT NULL DEFAULT 10000.0,
    narrative_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id, territory_id) REFERENCES territory_ledgers(work_id, territory_id)
);

CREATE TABLE IF NOT EXISTS territory_buildings (
    building_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    territory_id TEXT NOT NULL,
    building_proto_id TEXT NOT NULL,
    name TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    durability REAL NOT NULL DEFAULT 100.0,
    status TEXT NOT NULL DEFAULT 'completed',
    assigned_workers INTEGER NOT NULL DEFAULT 0,
    narrative_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(work_id, territory_id, building_id),
    FOREIGN KEY(work_id, territory_id) REFERENCES territory_ledgers(work_id, territory_id)
);

-- 6. 伏笔暗线跟踪表
CREATE TABLE IF NOT EXISTS foreshadowing (
    seed_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL,
    planted_scene_uuid TEXT NOT NULL,
    planted_chapter_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'planted', -- planted | hinted | resolved | abandoned
    clue_summary TEXT NOT NULL,
    payoff_scene_uuid TEXT,
    payoff_notes TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

-- 7. jieba 预分词 SQLite FTS5 全文搜索虚表
CREATE VIRTUAL TABLE IF NOT EXISTS fts_scenes USING fts5(
    scene_uuid UNINDEXED,
    work_id UNINDEXED,
    chapter_index UNINDEXED,
    segmented_content,
    tokenize = 'unicode61'
);

-- 8. 知识库大模型调用日志与审计表
CREATE TABLE IF NOT EXISTS api_usage_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_cny REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""


class DatabaseClient:
    """SQLite 数据库客户端"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """获取并配置连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # 强制启用 WAL 模式与外键
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_db(self) -> None:
        """执行 Schema 初始化"""
        with self.get_connection() as conn:
            conn.executescript(MANIFEST_SCHEMA_DDL)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """事务上下文管理器"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def ensure_work(cur: sqlite3.Cursor, work_id: str, owner_id: str = "default_author") -> None:
    """确保作者与作品基础表记录存在 (避免外键约束报错)"""
    cur.execute(
        "INSERT OR IGNORE INTO authors (owner_id, slug, display_name, created_at) VALUES (?, ?, ?, datetime('now'))",
        (owner_id, owner_id, owner_id)
    )
    cur.execute(
        "INSERT OR IGNORE INTO works (work_id, owner_id, slug, title, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
        (work_id, owner_id, work_id, work_id)
    )


def ensure_entity(cur: sqlite3.Cursor, work_id: str, entity_id: str, name: Optional[str] = None, category: str = "character") -> None:
    """确保实体表记录存在"""
    ensure_work(cur, work_id)
    cur.execute(
        "INSERT OR IGNORE INTO entities (entity_id, work_id, category, name, file_path, updated_at) VALUES (?, ?, ?, ?, '', datetime('now'))",
        (entity_id, work_id, category, name or entity_id)
    )


def ensure_territory(cur: sqlite3.Cursor, work_id: str, territory_id: str, lord_entity_id: str = "default_lord") -> None:
    """确保领地治理底盘记录存在"""
    ensure_entity(cur, work_id, lord_entity_id)
    cur.execute(
        """
        INSERT OR IGNORE INTO territory_ledgers
        (territory_id, work_id, lord_entity_id, tier_level, narrative_order, updated_at)
        VALUES (?, ?, ?, 'village', 0, datetime('now'))
        """,
        (territory_id, work_id, lord_entity_id)
    )
