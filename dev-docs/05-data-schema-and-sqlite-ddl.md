# 05 数据契约、YAML 规范与 SQLite DDL

> 状态：`PLANNED`

---

## 1. 纯文本单真理源格式规范（Text-First Contracts）

### 1.1 实体卡规范：`projects/<work-id>/entities/characters/<entity_id>.md`
```markdown
---
entity_id: "char_lin_dong"
work_id: "chusheng"
category: "character"
name: "林动"
aliases: ["武祖", "林动哥", "小动"]
created_at: "2026-09-01T10:00:00Z"
updated_at: "2026-09-04T12:00:00Z"
attributes:
  gender: "male"
  primary_clan: "青阳镇林家"
  initial_realm: "淬体五重"

# 性格与心性随时间的阶段相态 (Personality Phases)
phases:
  - phase_id: "phase_early_struggle"
    name: "前期：青阳镇隐忍蜕变期"
    valid_from_chapter: 1
    valid_to_chapter: 30
    traits: ["隐忍坚毅", "稍显稚嫩", "极度护短", "对家族不公心存芥蒂"]
    anti_behaviors: ["严禁狂妄自大", "严禁主动挑衅大宗门"]
    tone_examples: ["林动咬了咬牙，没有说话，只是将拳头在袖中攥得发白。"]

  - phase_id: "phase_mid_blackened"
    name: "中期：大荒古碑血腥杀伐期"
    valid_from_chapter: 31
    valid_to_chapter: 150
    traits: ["杀伐果断", "心机深沉", "冷酷无情", "斩草除根"]
    anti_behaviors: ["严禁任何圣母仁慈", "严禁放走任何已知仇敌"]
    tone_examples: ["林动面无表情，甚至未看尸体一眼，指尖微弹，黑炎便将其彻底焚为灰烬。"]
---

# 林动

## 人物生平与核心动机
青阳镇林家的平凡少年，因父亲重伤被家族冷落而立志苦修。偶然拾得祖石符，踏入逆天改命的修仙征途。

---

### 1.2 物品原型与唯一孤品规范（量产物品 vs 唯一神兵）

#### A. 唯一神兵孤品（Unique Artifact）：`projects/<work-id>/entities/items/item_stone_talisman.md`
```markdown
---
item_id: "item_stone_talisman"
work_id: "chusheng"
is_unique: true # 唯一孤品，全天下仅此一件
name: "神秘石符（远古祖石）"
category: "artifact"
created_at: "2026-09-01T10:00:00Z"

# 物品形态演变与强化链 (Evolution Stages)
evolution_stages:
  - stage_id: "stage_dormant"
    name: "石符形态（沉睡未苏醒）"
    valid_from_chapter: 1
    valid_to_chapter: 60
    abilities: ["提纯灵药", "完善武学武技"]
    condition: "受损残缺，表面有裂痕"

  - stage_id: "stage_awakened"
    name: "符祖祖石（第二层封印解开）"
    valid_from_chapter: 61
    valid_to_chapter: null
    abilities: ["提纯灵药", "武学推演", "祖石器灵岩苏醒", "净化一切异魔侵蚀"]
    condition: "吸收远古血脉修复裂痕"
---
```

#### B. 量产通用原型（Item Prototype）：`projects/<work-id>/entities/items/prototypes/storage_bag.yaml`
用于“人手一件、随时消耗”的量产型装备与丹药：
```yaml
prototype_id: "proto_storage_bag_low"
work_id: "chusheng"
is_unique: false # 量产通用模版
name: "下品储物袋"
category: "consumable_gear"
specifications:
  internal_volume_m3: 10
  material: "青丝空冥绢"
  market_price: "10 低级灵石"
  common_holders: "大宗门外门弟子、散修人手一个"
```

---

### 1.3 伏笔种子规范：`projects/<work-id>/entities/foreshadowing/seed_001.yaml`
```yaml
seed_id: "fs_stone_talisman_origin"
work_id: "chusheng"
name: "祖石符内部受损的真相"
planted_scene_uuid: "sc_001_cave_discovery"
planted_chapter_index: 3
status: "planted" # planted | hinted | resolved | abandoned
clue_summary: "石符中央有一道微不可察的雷纹裂痕，吸收天地灵气时偶尔散发黑色幽光"
intended_payoff_chapter: 120
payoff_scene_uuid: null
payoff_notes: "待主角突破造化境时揭晓乃远古符祖坐化之物"
```

---

## 2. 衍生加速数据库完整 DDL（`data/manifest.sqlite`）

本数据库为纯粹的衍生加速缓存，随时可通过 `kb rebuild` 重建：

```sql
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
    genre_ids_json TEXT NOT NULL,
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
    aliases_json TEXT NOT NULL,
    attributes_yaml TEXT NOT NULL,
    file_path TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, entity_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

-- 实体时序相态与性格阶段表 (解决角色性格黑化、心性演变、物品强化进阶)
CREATE TABLE IF NOT EXISTS entity_phases (
    phase_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    valid_from_order INTEGER NOT NULL DEFAULT 0,
    valid_to_order INTEGER, -- NULL 表示生效至今
    traits_json TEXT NOT NULL, -- 当前阶段的性格标签/物品能力
    anti_behaviors_json TEXT NOT NULL, -- 当前阶段的负面禁行规则
    tone_examples_json TEXT, -- 匹配当前性格的语气范例
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, entity_id, phase_id),
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL, -- master_disciple | enemy | member_of
    valid_from_order INTEGER NOT NULL DEFAULT 0,
    valid_to_order INTEGER, -- NULL 表示永久有效
    FOREIGN KEY(work_id, source_id) REFERENCES entities(work_id, entity_id)
);

-- 2.1 量产物品原型与背包持有表 (解决“人手一件”的通用量产消耗品)
CREATE TABLE IF NOT EXISTS item_prototypes (
    prototype_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL, -- storage | pill | weapon | talisman
    specs_json TEXT NOT NULL,
    PRIMARY KEY(work_id, prototype_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE TABLE IF NOT EXISTS item_instances (
    instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    owner_entity_id TEXT NOT NULL, -- 所属角色/势力
    item_type TEXT NOT NULL, -- unique_item | prototype
    item_ref_id TEXT NOT NULL, -- 关联 entity_id 或 prototype_id
    quantity INTEGER NOT NULL DEFAULT 1,
    current_durability REAL, -- 耐久/品质
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id, owner_entity_id) REFERENCES entities(work_id, entity_id)
);

-- 2.2 物品归属流转事件表 (解决物品在不同时间归属不同人的问题)
CREATE TABLE IF NOT EXISTS item_ownership_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    item_ref_id TEXT NOT NULL,
    from_owner_id TEXT, -- NULL 表示无主/山洞拾取
    to_owner_id TEXT, -- NULL 表示遗失/损毁
    transfer_type TEXT NOT NULL, -- looted | gifted | stolen | purchased | destroyed
    scene_uuid TEXT NOT NULL,
    narrative_order INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 3. 主张、凭证与合法吃书表 (兼容 novel-Skill claim 结构)
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

-- 4. 时空因果 DAG、多世界线与时间回溯表 (解决死亡回档、读档流与时间循环)
CREATE TABLE IF NOT EXISTS timelines (
    timeline_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL, -- 如 "main", "loop_1", "loop_2_blackened"
    parent_timeline_id TEXT,
    fork_narrative_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, timeline_id),
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

-- 时间回溯存档点 (Checkpoint: 记录物理世界快照与跨轮回保留记忆的主角清单)
CREATE TABLE IF NOT EXISTS reversion_checkpoints (
    checkpoint_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    timeline_id TEXT NOT NULL,
    physical_time TEXT NOT NULL, -- 故事内日历时间 (如 "建安三年五月初一")
    narrative_order INTEGER NOT NULL, -- 全局单调递增叙事序号
    world_state_json TEXT NOT NULL, -- 物理世界回滚快照 (金币、他人存亡、环境)
    retained_entities_json TEXT NOT NULL, -- 跨轮回保留记忆/能力的观察者 (如 ["char_lin_dong"])
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_id, checkpoint_id),
    FOREIGN KEY(work_id, timeline_id) REFERENCES timelines(work_id, timeline_id)
);

CREATE TABLE IF NOT EXISTS causal_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    work_id TEXT NOT NULL,
    timeline_id TEXT NOT NULL DEFAULT 'main',
    scene_uuid TEXT NOT NULL,
    story_time TEXT NOT NULL,
    narrative_order INTEGER NOT NULL, -- 始终单调递增，杜绝因果 DAG 成环
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

CREATE TABLE IF NOT EXISTS causal_links (
    parent_event_id TEXT NOT NULL,
    child_event_id TEXT NOT NULL,
    link_type TEXT NOT NULL, -- causes | enables | prevents | resets_to
    PRIMARY KEY(parent_event_id, child_event_id),
    FOREIGN KEY(parent_event_id) REFERENCES causal_events(event_id),
    FOREIGN KEY(child_event_id) REFERENCES causal_events(event_id)
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
    is_anchor INTEGER NOT NULL DEFAULT 0, -- 1 为基准锚点 (baseline_anchor)
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

-- 5.1 【游戏文专项】角色多维属性、技能树与装备栏表
CREATE TABLE IF NOT EXISTS character_attributes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    primary_class TEXT NOT NULL,        -- 主职业 (如 "暗影刺客")
    secondary_class TEXT,               -- 副职业 (如 "初级炼金术士")
    level INTEGER NOT NULL DEFAULT 1,
    experience INTEGER NOT NULL DEFAULT 0,
    free_stat_points INTEGER NOT NULL DEFAULT 0,
    base_stats_json TEXT NOT NULL,      -- {"str": 15, "agi": 28, "int": 12, "con": 14}
    derived_stats_json TEXT NOT NULL,   -- 由 Python 规则引擎计算出的 {"atk": 71, "mp": 120, "crit": "14%"}
    narrative_order INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS skills_tree (
    skill_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    name TEXT NOT NULL,
    required_class TEXT,
    tier INTEGER NOT NULL DEFAULT 1,     -- 技能阶位 (1~9 阶)
    skill_type TEXT NOT NULL,           -- active | passive | aura | ultimate
    cost_mp INTEGER NOT NULL DEFAULT 0,
    cooldown_narrative_steps INTEGER NOT NULL DEFAULT 0, -- 冷却需要的叙事步长
    prerequisite_skill_id TEXT,         -- 前置依赖技能
    description TEXT NOT NULL,
    PRIMARY KEY(work_id, skill_id)
);

CREATE TABLE IF NOT EXISTS character_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    skill_level INTEGER NOT NULL DEFAULT 1,
    proficiency INTEGER NOT NULL DEFAULT 0,
    is_equipped INTEGER NOT NULL DEFAULT 0, -- 【核心】是否处于快捷装备栏 (Active Deck, 默认最多 6 个)
    last_cast_narrative_order INTEGER NOT NULL DEFAULT 0, -- 上次释放时的全局叙事步长
    FOREIGN KEY(work_id, entity_id) REFERENCES entities(work_id, entity_id),
    FOREIGN KEY(work_id, skill_id) REFERENCES skills_tree(work_id, skill_id)
);

-- 5.2 【领主流专项】领地治理、资源储备与建筑拓扑表
CREATE TABLE IF NOT EXISTS territory_ledgers (
    territory_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    lord_entity_id TEXT NOT NULL,       -- 领主角色 ID
    tier_level TEXT NOT NULL,           -- village | town | city | metropolis
    population_total INTEGER NOT NULL DEFAULT 100,
    population_soldiers INTEGER NOT NULL DEFAULT 10,
    loyalty_score REAL NOT NULL DEFAULT 80.0, -- 民心满意度 (0~100)
    security_score REAL NOT NULL DEFAULT 85.0,-- 治安指数 (0~100)
    tax_rate REAL NOT NULL DEFAULT 0.15,      -- 税率
    narrative_order INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(work_id, territory_id),
    FOREIGN KEY(work_id, lord_entity_id) REFERENCES entities(work_id, entity_id)
);

CREATE TABLE IF NOT EXISTS territory_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id TEXT NOT NULL,
    territory_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,        -- food | wood | stone | iron | gold | magic_crystal
    current_amount REAL NOT NULL,
    daily_net_yield REAL NOT NULL,      -- 每日净产出 (产出 - 消耗)
    storage_limit REAL NOT NULL,        -- 仓储上限
    narrative_order INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id, territory_id) REFERENCES territory_ledgers(work_id, territory_id)
);

CREATE TABLE IF NOT EXISTS territory_buildings (
    building_id TEXT NOT NULL,
    work_id TEXT NOT NULL,
    territory_id TEXT NOT NULL,
    building_proto_id TEXT NOT NULL,    -- 如 "barracks", "magic_tower", "wall"
    name TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    durability REAL NOT NULL DEFAULT 100.0,
    status TEXT NOT NULL DEFAULT 'completed', -- constructing | completed | damaged | upgrading
    assigned_workers INTEGER NOT NULL DEFAULT 0,
    narrative_order INTEGER NOT NULL,
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
    status TEXT NOT NULL, -- planted | hinted | resolved | abandoned
    clue_summary TEXT NOT NULL,
    payoff_scene_uuid TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);

-- 7. jieba 预分词 SQLite FTS5 全文搜索虚表
CREATE VIRTUAL TABLE IF NOT EXISTS fts_scenes USING fts5(
    scene_uuid UNINDEXED,
    work_id UNINDEXED,
    chapter_index UNINDEXED,
    segmented_content, -- 存放经由 jieba 预分词处理后的空格间隔文本
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

-- 9. 章节索引与文风技法资产表 (彻底淘汰万行 YAML 与单体大 JSON)
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

CREATE TABLE IF NOT EXISTS style_profiles (
    work_id TEXT PRIMARY KEY NOT NULL,
    author TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    lexicon_features_json TEXT NOT NULL DEFAULT '{}',
    fingerprint_path TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(work_id) REFERENCES works(work_id)
);
```


---

## 3. 调用缓存数据库 DDL（`data/cache.sqlite`）

独立存放大模型哈希响应，彻底避免业务库与缓存库混合：

```sql
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY NOT NULL, -- SHA256(model + prompt_ver + text + temp)
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    response_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS llm_cache_lookup_idx 
    ON llm_cache(cache_key, model, prompt_version);
```

---

## 4. `kb rebuild` 一键全量重建执行流水线

当开发者运行 `python -m fxi.cli rebuild` 时，系统执行严格确定的 6 步重建流程：

```text
[Step 1: 清空与初始化]
  DROP TABLE (清除全部衍生数据) -> 执行 DDL 重新建表 -> 预加载 SQLite PRAGMA (WAL 模式)
         ↓
[Step 2: 原始底本与参考小说加载]
  扫描 sources/ 目录 -> 校验 SHA-256 -> 执行 TextSegmenter 切片 -> 写入 fts_scenes
         ↓
[Step 3: 作品与实体卡同步]
  扫描 projects/<work-id>/entities/ -> 解析 YAML Frontmatter -> 填充 entities 与 relations
         ↓
[Step 4: 章节草稿与事件回溯]
  扫描 projects/<work-id>/chapters/ -> 提取 scene_uuid、state-delta 与因果事件 -> 写入 state_events
         ↓
[Step 5: 技能与反面教条索引]
  扫描 skills/ 目录 -> 加载 rules.yaml 与 anti_patterns.yaml -> 建立技能场景索引
         ↓
[Step 6: 账本与快照重算]
  从基准锚点向前向后重新计算 state_snapshots -> 导出最新 project_lexicon.txt -> 输出完成报告 (<15秒)
```
