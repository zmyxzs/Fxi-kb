"""
fxi.storage.sqlite_client - SQLite 连接池与 Schema 管理器
"""

import json
from hashlib import sha256
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional, Any


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

-- 2.0 实体能力时序表
CREATE TABLE IF NOT EXISTS entity_abilities (
    ability_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    ability_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'innate',
    sequence_num TEXT,
    valid_from_chapter INTEGER NOT NULL DEFAULT 1,
    valid_to_chapter INTEGER,
    cost_description TEXT DEFAULT '',
    effect_description TEXT NOT NULL DEFAULT '',
    source_origin TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, entity_id, ability_id),
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_abilities_chapter ON entity_abilities(work_id, entity_id, valid_from_chapter, valid_to_chapter);

-- 2.0.1 同人因果变动账本 (Mutation Ledger)
CREATE TABLE IF NOT EXISTS timeline_mutations (
    mutation_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    trigger_chapter INTEGER NOT NULL,
    cause_event TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    mutation_type TEXT NOT NULL, -- ability_grant | ability_modify | ability_suppress | state_override | relation_change
    target_name TEXT NOT NULL,
    original_canon_chapter INTEGER,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active', -- active | superseded | cancelled
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, mutation_id)
);

CREATE INDEX IF NOT EXISTS idx_timeline_mutations_chapter ON timeline_mutations(work_id, entity_id, trigger_chapter, status);

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

CREATE TABLE IF NOT EXISTS character_known_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    learned_narrative_order INTEGER NOT NULL,
    scene_uuid TEXT,
    UNIQUE(work_id, character_id, claim_id)
);
CREATE INDEX IF NOT EXISTS idx_character_known_claims_lookup
    ON character_known_claims(work_id, character_id, learned_narrative_order);

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
    source_id TEXT,
    source_version TEXT,
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
    rule_version TEXT NOT NULL DEFAULT 'v1',
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_character_skills_unique
    ON character_skills(work_id, entity_id, skill_id);

-- 5.2 【宏观据点基业】领地、组织治理、资源与建筑拓扑
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
    source_id UNINDEXED,
    source_version UNINDEXED,
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

-- 9. /v2 immutable versioned workflow objects and idempotency heads
CREATE TABLE IF NOT EXISTS v2_source_snapshots (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    version TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    documents_json TEXT NOT NULL,
    object_root TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, source_id, idempotency_key),
    UNIQUE(source_id, version)
);

CREATE TABLE IF NOT EXISTS v2_style_candidates (
    candidate_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    version TEXT NOT NULL,
    package_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key),
    UNIQUE(work_id, version)
);

CREATE TABLE IF NOT EXISTS v2_style_promotions (
    promotion_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS v2_style_heads (
    work_id TEXT PRIMARY KEY NOT NULL,
    active_version TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_approvals (
    approval_id TEXT PRIMARY KEY NOT NULL,
    action TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_hash TEXT NOT NULL,
    expected_version TEXT NOT NULL,
    work_id TEXT,
    source_id TEXT,
    source_version TEXT,
    candidate_version TEXT,
    evaluation_ref TEXT,
    actor_id TEXT,
    role TEXT,
    expires_at TEXT,
    validity TEXT NOT NULL,
    consumed_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_proposals (
    proposal_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key),
    UNIQUE(work_id, proposal_hash)
);

CREATE TABLE IF NOT EXISTS v2_commits (
    commit_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    chapter_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    source_version TEXT,
    actor_id TEXT,
    receipt_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS v2_work_heads (
    work_id TEXT PRIMARY KEY NOT NULL,
    knowledge_version TEXT NOT NULL,
    chapter_version TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_sources (
    source_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    source_dir TEXT,
    source_version TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_id, work_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
CREATE INDEX IF NOT EXISTS idx_work_sources_work_id ON work_sources(work_id);
CREATE TABLE IF NOT EXISTS v2_reviews (
    report_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
CREATE INDEX IF NOT EXISTS idx_v2_reviews_lookup ON v2_reviews(work_id, text_hash, plan_hash, context_hash, knowledge_version);
CREATE TABLE IF NOT EXISTS v2_commit_documents (
    commit_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    chapter_version TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(commit_id) REFERENCES v2_commits(commit_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
CREATE TABLE IF NOT EXISTS v2_commit_events (
    commit_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(commit_id, event_id),
    FOREIGN KEY(commit_id) REFERENCES v2_commits(commit_id)
);
CREATE TABLE IF NOT EXISTS v2_commit_state_changes (
    commit_id TEXT NOT NULL,
    state_change_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(commit_id, state_change_id),
    FOREIGN KEY(commit_id) REFERENCES v2_commits(commit_id)
);
CREATE TABLE IF NOT EXISTS v2_commit_knowledge (
    commit_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(commit_id, character_id),
    FOREIGN KEY(commit_id) REFERENCES v2_commits(commit_id)
);
CREATE TABLE IF NOT EXISTS v2_commit_index (
    commit_id TEXT PRIMARY KEY NOT NULL,
    document_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(commit_id) REFERENCES v2_commits(commit_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
CREATE TABLE IF NOT EXISTS timeline_event_mappings (
    work_id TEXT NOT NULL,
    canon_work_id TEXT NOT NULL,
    canon_event_id TEXT NOT NULL,
    fanfic_event_id TEXT NOT NULL,
    mapping_type TEXT NOT NULL DEFAULT 'divergence',
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, canon_work_id, canon_event_id),
    UNIQUE(work_id, fanfic_event_id)
);
CREATE TABLE IF NOT EXISTS state_event_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    event_slot TEXT NOT NULL,
    commit_id TEXT,
    idempotency_key TEXT,
    knowledge_version TEXT,
    payload_hash TEXT NOT NULL,
    state_event_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key),
    UNIQUE(work_id, commit_id, event_slot)
);
CREATE INDEX IF NOT EXISTS idx_state_event_receipts_version
    ON state_event_receipts (work_id, entity_id, metric_id, knowledge_version);

CREATE TABLE IF NOT EXISTS reversion_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    causal_event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, operation_key)
);
CREATE TABLE IF NOT EXISTS v2_projection_tasks (
    task_id TEXT PRIMARY KEY NOT NULL,
    commit_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    projection_kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'APPLIED',
    error_code TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(commit_id, projection_kind),
    FOREIGN KEY(commit_id) REFERENCES v2_commits(commit_id)
);
CREATE TABLE IF NOT EXISTS chapter_continuity (
    work_id TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    title TEXT NOT NULL,
    ending_location TEXT NOT NULL,
    active_characters_json TEXT NOT NULL DEFAULT '[]',
    ending_situation TEXT NOT NULL,
    unresolved_hooks_json TEXT NOT NULL DEFAULT '[]',
    tail_snippet TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, chapter_index),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
CREATE INDEX IF NOT EXISTS idx_continuity_work_chapter ON chapter_continuity(work_id, chapter_index);

-- 10. 章节索引与文风技法资产表 (彻底淘汰万行 YAML 与单体大 JSON)
CREATE TABLE IF NOT EXISTS source_chapters (
    work_id TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    chapter_title TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    file_path TEXT NOT NULL,
    is_analyzed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, chapter_index),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
CREATE INDEX IF NOT EXISTS idx_source_chapters_analyzed ON source_chapters(work_id, is_analyzed);

CREATE TABLE IF NOT EXISTS style_rules (
    rule_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    category TEXT NOT NULL,
    scene_scope TEXT NOT NULL DEFAULT 'ALL',
    instruction TEXT NOT NULL,
    anti_pattern TEXT NOT NULL DEFAULT '',
    support_chapters_count INTEGER NOT NULL DEFAULT 1,
    supporting_chapters_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id),
    UNIQUE(work_id, canonical_key)
);
CREATE INDEX IF NOT EXISTS idx_style_rules_scope ON style_rules(work_id, scene_scope, status, support_chapters_count);

CREATE TABLE IF NOT EXISTS style_evidences (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    chapter_index INTEGER NOT NULL,
    quote TEXT NOT NULL,
    offset_start INTEGER,
    offset_end INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(rule_id) REFERENCES style_rules(rule_id)
);
CREATE INDEX IF NOT EXISTS idx_style_evidences_rule ON style_evidences(rule_id, chapter_index);

CREATE TABLE IF NOT EXISTS style_profiles (
    work_id TEXT PRIMARY KEY NOT NULL,
    author TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    lexicon_features_json TEXT NOT NULL DEFAULT '{}',
    fingerprint_path TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
"""


# v3 authority storage deliberately keeps payloads opaque.  Domain-specific
# fields are registered by DomainPackage and are stored as canonical JSON;
# the kernel only indexes the shared identity, scope, evidence and hash
# columns below.  The legacy tables above remain available until the
# backup/restore and clean-break gates are complete.
KNOWLEDGE_V3_SCHEMA_VERSION = 3
KNOWLEDGE_V3_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_schema_meta (
    schema_name TEXT PRIMARY KEY NOT NULL,
    schema_version INTEGER NOT NULL,
    contract_revision TEXT NOT NULL,
    contract_schema_hash TEXT NOT NULL,
    ddl_hash TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_source_bindings (
    binding_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    role TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    branch_id TEXT NOT NULL,
    validity_json TEXT NOT NULL,
    license TEXT NOT NULL,
    access TEXT NOT NULL,
    allowed_purposes_json TEXT NOT NULL DEFAULT '[]',
    sync_direction TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(work_id, binding_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_bindings_scope
    ON knowledge_source_bindings(work_id, branch_id, source_id, priority);

CREATE TABLE IF NOT EXISTS knowledge_source_snapshots (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    status TEXT NOT NULL,
    document_refs_json TEXT NOT NULL DEFAULT '[]',
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, source_id, source_version),
    FOREIGN KEY(binding_id) REFERENCES knowledge_source_bindings(binding_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_source_snapshots_scope
    ON knowledge_source_snapshots(work_id, source_id, source_version, status);

CREATE TABLE IF NOT EXISTS knowledge_evidence (
    evidence_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    source_snapshot_ref TEXT,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    document_id TEXT NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    excerpt_hash TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    scope_json TEXT,
    actor_id TEXT,
    license TEXT,
    evidence_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(start_char >= 0),
    CHECK(end_char > start_char),
    UNIQUE(source_id, source_version, document_id, start_char, end_char, excerpt_hash)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_scope
    ON knowledge_evidence(work_id, branch_id, source_id, source_version, document_id);

CREATE TABLE IF NOT EXISTS knowledge_candidates (
    candidate_id TEXT PRIMARY KEY NOT NULL,
    artifact_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    source_snapshot_ref TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    extractor_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    domain_package_version TEXT NOT NULL,
    model_route TEXT,
    prompt_hash TEXT,
    policy_hash TEXT NOT NULL,
    budget_ref TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    actor_id TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_scope
    ON knowledge_candidates(work_id, branch_id, artifact_kind, status);

CREATE TABLE IF NOT EXISTS knowledge_evaluations (
    evaluation_id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    evaluator_id TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    policy_json TEXT NOT NULL DEFAULT '{}',
    input_hash TEXT NOT NULL,
    evidence_coverage_json TEXT NOT NULL DEFAULT '{}',
    duplicate_cluster_ref TEXT,
    same_core INTEGER NOT NULL DEFAULT 0,
    variant_of TEXT,
    surface_similarity_risk REAL,
    suitability TEXT NOT NULL,
    required_adaptation_json TEXT NOT NULL DEFAULT '[]',
    uncertainty_json TEXT NOT NULL DEFAULT '{}',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    semantic_reviewer TEXT,
    result_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES knowledge_candidates(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_evaluations_candidate
    ON knowledge_evaluations(candidate_id, status, created_at);

CREATE TABLE IF NOT EXISTS knowledge_decisions (
    decision_id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    evaluation_ref TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT,
    scope_json TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES knowledge_candidates(candidate_id),
    FOREIGN KEY(evaluation_ref) REFERENCES knowledge_evaluations(evaluation_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_decisions_scope
    ON knowledge_decisions(work_id, branch_id, candidate_id, action);

CREATE TABLE IF NOT EXISTS knowledge_approvals (
    approval_id TEXT PRIMARY KEY NOT NULL,
    proposal_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    approval_hash TEXT NOT NULL,
    expected_version TEXT,
    knowledge_version TEXT,
    action TEXT,
    idempotency_key TEXT,
    expires_at TEXT,
    consumed INTEGER NOT NULL DEFAULT 0,
    consumed_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, approval_hash)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_approvals_scope
    ON knowledge_approvals(work_id, branch_id, proposal_id, consumed);

CREATE TABLE IF NOT EXISTS knowledge_objects (
    object_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    type_uri TEXT NOT NULL,
    schema_uri TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    origin TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    validity_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    evaluation_ref TEXT,
    knowledge_version TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(evaluation_ref) REFERENCES knowledge_evaluations(evaluation_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_objects_scope
    ON knowledge_objects(work_id, branch_id, type_uri, status, knowledge_version);

CREATE TABLE IF NOT EXISTS knowledge_claims (
    claim_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    object_type_uri TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    version TEXT,
    claim_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_claims_scope
    ON knowledge_claims(work_id, branch_id, subject_ref, predicate, status);

CREATE TABLE IF NOT EXISTS knowledge_relations (
    relation_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    relation_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_relations_scope
    ON knowledge_relations(work_id, branch_id, relation_type, status);

CREATE TABLE IF NOT EXISTS knowledge_versions (
    knowledge_version TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    parent_version TEXT,
    object_refs_json TEXT NOT NULL DEFAULT '[]',
    claim_refs_json TEXT NOT NULL DEFAULT '[]',
    relation_refs_json TEXT NOT NULL DEFAULT '[]',
    source_snapshot_refs_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, branch_id, version_hash)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_versions_scope
    ON knowledge_versions(work_id, branch_id, created_at);

CREATE TABLE IF NOT EXISTS knowledge_branches (
    branch_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    parent_branch TEXT,
    divergence_anchor_json TEXT,
    policy_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    actor_id TEXT,
    branch_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, branch_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_branches_work
    ON knowledge_branches(work_id, status, parent_branch);

CREATE TABLE IF NOT EXISTS knowledge_heads (
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    cas_revision INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, branch_id)
);

CREATE TABLE IF NOT EXISTS knowledge_context_views (
    view_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    purpose TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    blocks_json TEXT NOT NULL DEFAULT '[]',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    forbidden_refs_json TEXT NOT NULL DEFAULT '[]',
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    staleness TEXT NOT NULL,
    completeness TEXT NOT NULL,
    budget_json TEXT NOT NULL DEFAULT '{}',
    view_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    knowledge_version TEXT,
    source_snapshot_refs_json TEXT NOT NULL DEFAULT '[]',
    manifest_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_context_views_scope
    ON knowledge_context_views(work_id, branch_id, purpose, knowledge_version);

CREATE TABLE IF NOT EXISTS knowledge_reviews (
    report_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    draft_ref TEXT NOT NULL,
    draft_hash TEXT NOT NULL,
    coordinate_json TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    checks_json TEXT NOT NULL DEFAULT '[]',
    overall_status TEXT NOT NULL,
    blocking_findings_json TEXT NOT NULL DEFAULT '[]',
    report_hash TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_scope
    ON knowledge_reviews(work_id, branch_id, overall_status, context_hash);

CREATE TABLE IF NOT EXISTS knowledge_state_changes (
    change_set_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    review_ref TEXT NOT NULL,
    source_artifact_hash TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    change_set_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_state_changes_scope
    ON knowledge_state_changes(work_id, branch_id, review_ref);

CREATE TABLE IF NOT EXISTS knowledge_proposals (
    proposal_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    draft_ref TEXT NOT NULL,
    draft_hash TEXT NOT NULL,
    review_ref TEXT NOT NULL,
    state_change_set_ref TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    status TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key),
    UNIQUE(work_id, proposal_hash)
);

CREATE TABLE IF NOT EXISTS knowledge_commits (
    commit_id TEXT PRIMARY KEY NOT NULL,
    proposal_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    chapter_version TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    commit_hash TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    chapter_json TEXT NOT NULL DEFAULT '{}',
    state_changes_json TEXT NOT NULL DEFAULT '[]',
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(work_id, idempotency_key),
    FOREIGN KEY(proposal_id) REFERENCES knowledge_proposals(proposal_id)
);

CREATE TABLE IF NOT EXISTS knowledge_projection_tasks (
    task_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    projection_id TEXT NOT NULL,
    knowledge_version TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    projection_hash TEXT,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    dead_letter INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(work_id, branch_id, projection_id, knowledge_version)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_projection_tasks_health
    ON knowledge_projection_tasks(work_id, branch_id, status, dead_letter);

CREATE TABLE IF NOT EXISTS knowledge_operation_receipts (
    operation_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT,
    actor_id TEXT,
    operation_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    result_hash TEXT,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(work_id, operation_kind, idempotency_key)
);

-- Complete v3 authority records are retained in a generic, domain-neutral
-- envelope as well as the typed tables above.  The envelope preserves
-- service-local metadata (for example ReviewRecord/ProposalRecord request
-- bindings and commit fingerprints) without teaching the kernel any domain
-- fields.  Typed tables remain the relational/indexed projection of the same
-- authority state.
CREATE TABLE IF NOT EXISTS knowledge_runtime_records (
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    work_id TEXT,
    branch_id TEXT,
    idempotency_key TEXT,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(record_type, record_id),
    UNIQUE(record_type, work_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_runtime_records_scope
    ON knowledge_runtime_records(record_type, work_id, branch_id, status);

-- JSON arrays preserve the public contract shape.  These link tables make
-- the authority/evidence relationship enforceable without inventing a
-- domain-specific column or polymorphic SQL foreign key.
CREATE TABLE IF NOT EXISTS knowledge_object_evidence (
    object_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(object_id, evidence_id),
    FOREIGN KEY(object_id) REFERENCES knowledge_objects(object_id),
    FOREIGN KEY(evidence_id) REFERENCES knowledge_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS knowledge_claim_evidence (
    claim_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(claim_id, evidence_id),
    FOREIGN KEY(claim_id) REFERENCES knowledge_claims(claim_id),
    FOREIGN KEY(evidence_id) REFERENCES knowledge_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS knowledge_relation_evidence (
    relation_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(relation_id, evidence_id),
    FOREIGN KEY(relation_id) REFERENCES knowledge_relations(relation_id),
    FOREIGN KEY(evidence_id) REFERENCES knowledge_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS knowledge_context_evidence (
    view_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(view_id, evidence_id),
    FOREIGN KEY(view_id) REFERENCES knowledge_context_views(view_id),
    FOREIGN KEY(evidence_id) REFERENCES knowledge_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS knowledge_promotions (
    promotion_id TEXT PRIMARY KEY NOT NULL,
    candidate_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    expected_head TEXT NOT NULL,
    new_knowledge_version TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    work_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES knowledge_candidates(candidate_id),
    FOREIGN KEY(approval_id) REFERENCES knowledge_approvals(approval_id),
    FOREIGN KEY(new_knowledge_version) REFERENCES knowledge_versions(knowledge_version)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_promotions_scope
    ON knowledge_promotions(work_id, branch_id, new_knowledge_version);
"""


def knowledge_v3_ddl_hash() -> str:
    """Return the digest of the complete legacy + v3 schema definition."""

    payload = (MANIFEST_SCHEMA_DDL + "\n" + KNOWLEDGE_V3_SCHEMA_DDL).encode("utf-8")
    return sha256(payload).hexdigest()



class DatabaseClient:
    """SQLite 数据库客户端"""

    def __init__(self, db_path: Path, *, initialize: bool = True):
        self.db_path = Path(db_path)
        if initialize:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """获取并配置连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # 强制启用 WAL 模式与外键
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def init_db(self) -> None:
        """执行 schema 初始化并应用只增量的兼容迁移。"""
        with self.get_connection() as conn:
            conn.executescript(MANIFEST_SCHEMA_DDL)
            conn.executescript(KNOWLEDGE_V3_SCHEMA_DDL)
            self._migrate_work_sources(conn)
            self._migrate_fts_scenes(conn)
            self._ensure_columns(conn, "v2_source_snapshots", {"object_root": "TEXT"})
            self._ensure_columns(
                conn,
                "v2_approvals",
                {
                    "work_id": "TEXT",
                    "source_id": "TEXT",
                    "source_version": "TEXT",
                    "candidate_version": "TEXT",
                    "evaluation_ref": "TEXT",
                    "actor_id": "TEXT",
                    "role": "TEXT",
                    "expires_at": "TEXT",
                },
            )
            self._ensure_columns(conn, "v2_commits", {"source_version": "TEXT", "actor_id": "TEXT"})
            self._ensure_columns(conn, "state_metrics", {"rule_version": "TEXT NOT NULL DEFAULT 'v1'"})
            self._ensure_columns(
                conn,
                "causal_events",
                {"source_id": "TEXT", "source_version": "TEXT"},
            )
            self._ensure_columns(
                conn,
                "knowledge_evaluations",
                {"policy_json": "TEXT NOT NULL DEFAULT '{}'"},
            )
            self._ensure_columns(
                conn,
                "knowledge_decisions",
                {
                    "actor_json": "TEXT NOT NULL DEFAULT '{}'",
                    "reason": "TEXT",
                },
            )
            self._ensure_columns(
                conn,
                "knowledge_approvals",
                {
                    "knowledge_version": "TEXT",
                    "action": "TEXT",
                    "idempotency_key": "TEXT",
                    "consumed_by": "TEXT",
                },
            )
            self._ensure_columns(
                conn,
                "knowledge_context_views",
                {"manifest_json": "TEXT"},
            )
            self._ensure_columns(
                conn,
                "knowledge_reviews",
                {"request_json": "TEXT NOT NULL DEFAULT '{}'"},
            )
            self._ensure_columns(
                conn,
                "knowledge_commits",
                {
                    "fingerprint": "TEXT NOT NULL DEFAULT ''",
                    "chapter_json": "TEXT NOT NULL DEFAULT '{}'",
                    "state_changes_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            self._ensure_columns(
                conn,
                "knowledge_projection_tasks",
                {"manifest_json": "TEXT NOT NULL DEFAULT '{}'"},
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_causal_events_scope "
                "ON causal_events(work_id, timeline_id, source_id, source_version, narrative_order)"
            )
            self._migrate_knowledge_v3(conn)

    @staticmethod
    def _migrate_knowledge_v3(conn: sqlite3.Connection) -> None:
        """Record the v3 schema identity and reject an unknown future schema."""

        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        metadata = conn.execute(
            "SELECT schema_version FROM knowledge_schema_meta "
            "WHERE schema_name = ?",
            ("fxi-knowledge",),
        ).fetchone()
        recorded = 0 if metadata is None else int(metadata[0])
        if current > KNOWLEDGE_V3_SCHEMA_VERSION or recorded > KNOWLEDGE_V3_SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                "database schema is newer than this Fxi runtime"
            )

        from fxi.knowledge.contracts import CONTRACT_REVISION, SCHEMA_HASH

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO knowledge_schema_meta
               (schema_name, schema_version, contract_revision,
                contract_schema_hash, ddl_hash, applied_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(schema_name) DO UPDATE SET
                 schema_version = excluded.schema_version,
                 contract_revision = excluded.contract_revision,
                 contract_schema_hash = excluded.contract_schema_hash,
                 ddl_hash = excluded.ddl_hash,
                 applied_at = excluded.applied_at""",
            (
                "fxi-knowledge",
                KNOWLEDGE_V3_SCHEMA_VERSION,
                CONTRACT_REVISION,
                SCHEMA_HASH,
                knowledge_v3_ddl_hash(),
                now,
            ),
        )
        conn.execute(f"PRAGMA user_version = {KNOWLEDGE_V3_SCHEMA_VERSION}")

    def schema_metadata(self) -> dict[str, Any]:
        """Return the persisted schema/contract identity for readiness checks."""

        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT schema_name, schema_version, contract_revision, "
                "contract_schema_hash, ddl_hash, applied_at "
                "FROM knowledge_schema_meta WHERE schema_name = ?",
                ("fxi-knowledge",),
            ).fetchone()
            return {} if row is None else dict(row)

    @staticmethod
    def _migrate_work_sources(conn: sqlite3.Connection) -> None:
        """Upgrade legacy source_id-only keys to a work-scoped binding key."""
        columns = list(conn.execute("PRAGMA table_info(work_sources)"))
        primary_key = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        if primary_key != ["source_id"]:
            return
        conn.execute(
            """CREATE TABLE work_sources__migrated (
                source_id TEXT NOT NULL,
                work_id TEXT NOT NULL,
                source_dir TEXT,
                source_version TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(source_id, work_id),
                FOREIGN KEY(work_id) REFERENCES works(work_id)
            )"""
        )
        conn.execute(
            """INSERT INTO work_sources__migrated
            (source_id, work_id, source_dir, source_version, created_at)
            SELECT source_id, work_id, source_dir, source_version, created_at
            FROM work_sources"""
        )
        conn.execute("DROP TABLE work_sources")
        conn.execute("ALTER TABLE work_sources__migrated RENAME TO work_sources")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_work_sources_work_id ON work_sources(work_id)")

    @staticmethod
    def _migrate_fts_scenes(conn: sqlite3.Connection) -> None:
        """为旧 FTS5 表补齐来源版本列，并保留旧行作为 legacy 索引。"""

        columns = {row[1] for row in conn.execute("PRAGMA table_info(fts_scenes)")}
        required = {"source_id", "source_version"}
        if not columns or required.issubset(columns):
            return
        legacy_table = "fts_scenes__legacy"
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (legacy_table,)
        ).fetchone():
            raise sqlite3.DatabaseError("检测到未完成的 fts_scenes 迁移")
        conn.execute("ALTER TABLE fts_scenes RENAME TO fts_scenes__legacy")
        conn.execute(
            """CREATE VIRTUAL TABLE fts_scenes USING fts5(
                scene_uuid UNINDEXED,
                work_id UNINDEXED,
                source_id UNINDEXED,
                source_version UNINDEXED,
                chapter_index UNINDEXED,
                segmented_content,
                tokenize = 'unicode61'
            )"""
        )
        conn.execute(
            """INSERT INTO fts_scenes
                (scene_uuid, work_id, source_id, source_version, chapter_index, segmented_content)
            SELECT scene_uuid, work_id, NULL, NULL, chapter_index, segmented_content
            FROM fts_scenes__legacy"""
        )
        conn.execute("DROP TABLE fts_scenes__legacy")

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """事务上下文管理器"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield cursor
            conn.commit()
        except BaseException as exc:
            try:
                conn.rollback()
            except sqlite3.Error as rollback_error:
                raise sqlite3.DatabaseError(
                    f"SQLite rollback failed: {rollback_error}"
                ) from exc
            raise
        finally:
            conn.close()

    def upsert_source_chapters(self, work_id: str, chapters: list[dict[str, Any]]) -> int:
        """批量保存或更新章节索引（替代 source.yaml）"""
        with self.transaction() as cur:
            ensure_work(cur, work_id)
            count = 0
            for ch in chapters:
                cur.execute(
                    """
                    INSERT INTO source_chapters
                    (work_id, chapter_index, chapter_title, char_count, sha256, file_path, is_analyzed, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(work_id, chapter_index) DO UPDATE SET
                        chapter_title = excluded.chapter_title,
                        char_count = excluded.char_count,
                        sha256 = excluded.sha256,
                        file_path = excluded.file_path,
                        is_analyzed = excluded.is_analyzed
                    """,
                    (
                        work_id,
                        ch["chapter_index"],
                        ch.get("chapter_title", f"第{ch['chapter_index']}章"),
                        ch.get("char_count", 0),
                        ch.get("sha256", ""),
                        ch.get("file_path", ""),
                        1 if ch.get("is_analyzed") else 0,
                    ),
                )
                count += 1
            return count

    def get_source_chapters(self, work_id: str, is_analyzed: Optional[bool] = None) -> list[sqlite3.Row]:
        """按序号升序获取指定作品的章节索引"""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if is_analyzed is None:
                cur.execute(
                    "SELECT * FROM source_chapters WHERE work_id = ? ORDER BY chapter_index ASC",
                    (work_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM source_chapters WHERE work_id = ? AND is_analyzed = ? ORDER BY chapter_index ASC",
                    (work_id, 1 if is_analyzed else 0),
                )
            return cur.fetchall()
        finally:
            conn.close()

    def upsert_style_rule(self, work_id: str, rule: dict[str, Any]) -> str:
        """保存或聚合更新技法规则（带同义 canonical_key 自动累加）"""
        canonical_key = rule["canonical_key"]
        category = rule.get("category", "syntax")
        scene_scope = rule.get("scene_scope", "ALL")
        instruction = rule["instruction"]
        anti_pattern = rule.get("anti_pattern", "")
        new_ch = rule.get("chapter_index")
        rule_id = rule.get("rule_id") or f"rule_{canonical_key}"

        with self.transaction() as cur:
            ensure_work(cur, work_id)
            existing = cur.execute(
                "SELECT * FROM style_rules WHERE work_id = ? AND canonical_key = ?",
                (work_id, canonical_key),
            ).fetchone()

            if existing:
                ch_list = json.loads(existing["supporting_chapters_json"] or "[]")
                if new_ch and new_ch not in ch_list:
                    ch_list.append(new_ch)
                    ch_list.sort()
                new_count = len(ch_list) if ch_list else existing["support_chapters_count"] + 1
                cur.execute(
                    """
                    UPDATE style_rules
                    SET instruction = ?,
                        anti_pattern = ?,
                        scene_scope = ?,
                        category = ?,
                        support_chapters_count = ?,
                        supporting_chapters_json = ?,
                        updated_at = datetime('now')
                    WHERE work_id = ? AND canonical_key = ?
                    """,
                    (
                        instruction,
                        anti_pattern or existing["anti_pattern"],
                        scene_scope,
                        category,
                        new_count,
                        json.dumps(ch_list, ensure_ascii=False),
                        work_id,
                        canonical_key,
                    ),
                )
                return existing["rule_id"]
            else:
                ch_list = [new_ch] if new_ch else []
                cur.execute(
                    """
                    INSERT INTO style_rules
                    (rule_id, work_id, canonical_key, category, scene_scope, instruction, anti_pattern,
                     support_chapters_count, supporting_chapters_json, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', datetime('now'))
                    """,
                    (
                        rule_id,
                        work_id,
                        canonical_key,
                        category,
                        scene_scope,
                        instruction,
                        anti_pattern,
                        max(1, len(ch_list)),
                        json.dumps(ch_list, ensure_ascii=False),
                    ),
                )
                return rule_id

    def add_style_evidence(
        self,
        rule_id: str,
        work_id: str,
        chapter_index: int,
        quote: str,
        offset_start: Optional[int] = None,
        offset_end: Optional[int] = None,
    ) -> int:
        """追加原著证据锚点（零文本冗余，指针化存储）"""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO style_evidences
                (rule_id, work_id, chapter_index, quote, offset_start, offset_end, created_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (rule_id, work_id, chapter_index, quote, offset_start, offset_end),
            )
            return cur.lastrowid

    def query_scene_style_rules(
        self, work_id: str, scene_type: Optional[str] = None, limit: int = 3
    ) -> list[sqlite3.Row]:
        """按场景剪枝毫秒级提取 Top-K 规则"""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if scene_type:
                cur.execute(
                    """
                    SELECT * FROM style_rules
                    WHERE work_id = ?
                      AND status = 'ACTIVE'
                      AND (scene_scope = ? OR scene_scope = 'ALL')
                    ORDER BY support_chapters_count DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (work_id, scene_type, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM style_rules
                    WHERE work_id = ? AND status = 'ACTIVE'
                    ORDER BY support_chapters_count DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (work_id, limit),
                )
            return cur.fetchall()
        finally:
            conn.close()

    def save_style_profile(
        self,
        work_id: str,
        author: str,
        metrics: dict[str, Any],
        lexicon_features: Optional[dict[str, Any]] = None,
        fingerprint_path: Optional[str] = None,
    ) -> None:
        """保存或更新数理指纹画像"""
        with self.transaction() as cur:
            ensure_work(cur, work_id)
            cur.execute(
                """
                INSERT INTO style_profiles
                (work_id, author, metrics_json, lexicon_features_json, fingerprint_path, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(work_id) DO UPDATE SET
                    author = excluded.author,
                    metrics_json = excluded.metrics_json,
                    lexicon_features_json = excluded.lexicon_features_json,
                    fingerprint_path = excluded.fingerprint_path,
                    updated_at = datetime('now')
                """,
                (
                    work_id,
                    author,
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(lexicon_features or {}, ensure_ascii=False),
                    fingerprint_path,
                ),
            )

    def get_style_profile(self, work_id: str) -> Optional[sqlite3.Row]:
        """读取数理指纹画像"""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM style_profiles WHERE work_id = ?", (work_id,))
            return cur.fetchone()
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
