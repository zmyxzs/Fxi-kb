# 03 故事世界模型：人物、物品、能力、关系、事件与伏笔

## 这一部分用来做什么

它负责描述“故事里有什么，以及这些东西在故事中如何因果演化”，核心用于：

- 记录原著或作者自己的设定事实；
- 查询某章某场景时人物、物品、能力、关系和数值的状态；
- 支持剧情规划、伏笔追踪和同人 AU 改写；
- 支持中途引入新数值体系（三态数值与基线锚点），防范历史虚假报错；
- 为主张可信度、时间线因果和防 OOC 检查提供稳定的结构化对象。

---

## 核心对象模型

### 1. Entity（故事实体）
故事宇宙中具有稳定标识的对象：
- `character`：人物、神祇、分身；
- `place`：地理区域、城市、宗门据点、秘境；
- `organization`：门派、朝廷、阵营、商会；
- `object`：道具、武器、法宝、遗物、关键信物；
- `concept`：修炼体系、天道法则、宗规礼法；
- `species`：种族、妖兽类别。

#### 1.1 角色性格与心性演变相态（Personality Phases）
长篇小说中角色的心性往往随经历剧烈转变（如前期隐忍坚毅，中期遭遇血海深仇黑化杀伐果断，后期超然通透）。
实体模型支持挂载 **时序性格相态（Phases）**：
- 每个相态定义 `valid_from_chapter` ~ `valid_to_chapter`；
- 包含该阶段的“核心特质”、“反面行为禁令（anti_behaviors）”与“语气范例”；
- 写作上下文装配时，系统根据当前章节自动加载角色的生效性格，杜绝“全书输出同一套扁平性格”导致的严重 OOC。

#### 1.2 唯一神兵 vs 量产物品二分法（Prototype vs Unique Instance）
网络小说中物品存在两种根本形态，必须严格区分：
1. **唯一神兵孤品（Unique Artifact）**：
   - 天地间仅此一件（如“远古祖石”、“诛仙剑”）；
   - 具备独立的**形态演变强化链**（如：封印受损 $\rightarrow$ 吸收真龙血觉醒 $\rightarrow$ 破碎）与不可变的唯一实体 ID；
   - 归属权通过**流转事件（Ownership Events）**记录（拾取、赠予、被夺），杜绝角色已送出法宝却在后续章节依然使用的穿帮。
2. **量产通用原型（Item Prototype）**：
   - 人手一件、通用量产的装备与消耗品（如“下品储物袋”、“外门青钢剑”、“生骨回春丹”）；
   - 系统定义原型规格（容量、造价、功能），每个角色在其专属背包（Inventory）中按 `prototype_id` 和数量独立持有，一人损坏不影响他人。

### 2. Attribute 与三态数值模型（Three-State Metric）
为了支持在小说写到中途（如第 50 章）才加装“金币”或“战力”系统，避免系统将前 49 章误认为 `0` 进而报出“透支消费”虚假错误，数值属性底层采用三态区分：

- `EXPLICIT`：有明确数值记录（如 `balance: 500`）；
- `UNMEASURED`：未统计/混沌态（系统不报错，不做透支阻断）；
- `NOT_APPLICABLE`：该实体不适用该维度（如凡人不具有法力值）。

#### 历史基线锚点（Baseline Anchor）
当在第 50 章引入新系统时，只需插入一条基线锚点事件：
```yaml
event_id: "anchor_gold_init_ch50"
event_type: "baseline_anchor"
scene_uuid: "scene_ch50_inn_arrival"
target_entity: "character:protagonist"
metric: "gold"
set_value: 3000
status: "EXPLICIT"
note: "自第 50 章起正式开启主角金币记录，前文保持 UNMEASURED"
```
系统从此事件起向后参与收支计算，向前保持混沌，实现零痛点中途无缝加装系统。

### 3. Relation（实体间有向关系）
实体与实体之间的网络联系：
- 社交情感：`knows`, `trusts`, `loves`, `hates`, `owes`, `fears`；
- 归属持有：`owns`, `carries`, `wields`, `located_at`；
- 组织层级：`belongs_to`, `leads`, `infiltrates`（卧底）, `allied_with`, `enemy_of`；
- 身份映射：`same_as`, `masquerades_as`, `suspected_same_as`, `revealed_as`。

关系支持时间区间（`valid_from` ~ `valid_to`）。当实体被标记软删除时，关系自动降级为 `dormant`（休眠），保证旧文回溯不抛外键异常。

### 4. Event（因果状态改变事件）
改变故事世界状态的离散原子节点：
- 交互事件：相遇、结盟、决裂、背叛；
- 状态变动：获得/失去物品、受重伤、中毒、突破境界；
- 认知事件：获悉秘密、身份揭示、发现真相、产生误解；
- 话语事件：某人在某场景中说了某段重要对白。

### 5. Foreshadowing（伏笔生命周期管理）
长篇创作中作者极易遗忘前期埋下的暗线。系统将伏笔作为独立对象纳入故事模型：
```yaml
foreshadowing_id: "fs-mysterious-wooden-box"
title: "破庙中发现的雕花木盒"
planted_scene: "scene_ruined_temple_01"
hinted_scenes: ["scene_market_old_man_02"]
status: "planted"     # planted (已埋下) | hinted (侧面暗示) | resolved (正式回收) | abandoned (废弃烂尾)
associated_entities: ["object:wooden_box", "character:mysterious_monk"]
notes: "计划在主角进入中州后揭示盒中钥匙的用处"
```
作者可通过 CLI 一键输出当前项目未回收的暗线清单，避免剧情烂尾。

---

## 战力与换算规则版本化（Rule Versioning）

当写到后期战力膨胀、作者重构战力换算规则时：
- 战力公式绑定版本号（`rule_version: v1`, `v2`）；
- 早期章节锁定其写作当时的 `rule_version: v1`；
- 新公式在指定场景锚点后生效，旧章节绝不报出虚假战力失衡冲突。

---

## 时空非线性与区间继承（防状态爆炸）

1. **相对时间与因果 DAG**：事件通过 `happened_after: [event_a]` 串联，无缝支持“三年后”、“大火熄灭前”等文学模糊时间；
2. **惯性继承**：仅在状态改变时写入事件，后续章节默认顺延生效，避免按章节刷快照导致的数据暴增；
3. **双重时空轨**：客观故事因果时间（`story_time`，防战力穿越与 OOC） vs 叙事阅读顺序（`narrative_order`，防剧透与控伏笔）。