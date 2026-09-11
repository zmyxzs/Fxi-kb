# 自用写作与同人知识库规划与总索引

> 状态：`CURRENT + PLANNED`

这是知识库规划与技术架构的统一入口。本文档维护全局索引、系统核心原则与阅读导航。详细规划按“它是什么、用于什么、如何关联、如何演进”进行彻底的问题域解耦。

> **现实校准入口**：本目录的 `00`～`15` 主要描述目标模型、设计原则和演进方案。要判断某项能力当前是否真正可用、需要哪些数据前置条件，以及哪些设计尚未落地，请优先阅读 [16-current-implementation-status-and-boundaries.md](16-current-implementation-status-and-boundaries.md)，并结合 [dev-docs/08-current-implementation-and-operations.md](../../dev-docs/08-current-implementation-and-operations.md)。当设计蓝图与代码行为不一致时，以当前实现文档、代码和实际验证结果为准。

> **禁止误读**：下方“核心设计总原则”和专题中的“支持/必须”是目标约束，不是功能已上线的证明。当前 embedding、向量/RRF、离线队列、自动 failover、rollback approval 和任意全量 rebuild 均以“未实现/条件可用”处理。

> **入口优先级**：本总索引和专题包含规划内容；当前可调用入口、source-bind/source-attach、v3 API、runtime 及其边界以 [16 当前实现状态](16-current-implementation-status-and-boundaries.md) 与 [17 系统调用说明](17-system-usage-runbook.md) 为准。`knowledge-base-plan.md` 保持 `PLANNED`，不作为实现证明。

---

## 核心设计总原则（必读）

1. **纯文本优先与单真理源（Text-First Single Source of Truth）**：
   - 核心设定、人物卡、事件、草稿和素材一律以本地 Markdown + YAML Frontmatter 纯文本形式持久化；
   - SQLite、FTS5 中文全文表、向量索引属于**目标上的衍生数据**；当前含动态账本、角色认知、连续性和 v2 审计表的数据库不能无条件丢弃，重建遇到不可回放投影会 fail closed。
2. **状态与对象解耦**：
   - 人物、物品、能力、关系、事件和对话是故事世界中的**实体对象**；
   - 事实、猜测、谎言、误解、AI 判断和 AU 设定是这些对象的**认识论状态**。
3. **动态状态账本与三态模型（State Ledger & Three-State Values）**：
   - 金币、修为境界、战力和资源采用“变动事件账本 + 时间点快照”驱动计算，杜绝孤立静态数值；
   - 支持 `EXPLICIT` / `UNMEASURED` / `NOT_APPLICABLE` 三态模型与 `baseline_anchor` 基准锚点，写到中途（如第50章）接入新指标零历史包袱。
4. **同人分歧点（POD）与项目隔离**：
   - 三个写作项目逻辑严格隔离，默认禁止跨库串查；
   - 同人作品通过**分歧点（Point of Divergence）**锚定原著：分歧点后自动切断原著动态情节事实继承，但自动保留原著静态世界观法则。
5. **章节抗脆性（Semantic Scene UUIDs）**：
   - 以语义场景 UUID（`scene_uuid`）作为底层因果、伏笔与账本关联键，物理章节序号仅作为动态计算展示层，作者插章、删章、加更绝不断链。
6. **视点盲区与防偷看剧本（POV Observer Filter）**：
   - 检索接口强制支持 `pov_character_id`，检索组装 Prompt 前物理过滤当前观察者尚未获知的机密底牌，严防反派/配角视角出现全知视角穿帮。
7. **合法吃书（Retcon 追溯性修正事件）**：
   - 作者主动推翻旧设定登记为 `retcon` 事件，自动关联新旧主张与生效节点，豁免 OOC 扫描冲突报警并在正文生成中注入圆场过渡提示。
8. **审核防疲劳（Lazy Extraction & Triage）**：
   - 拒绝全书全量抽取；采用“按需惰性抽取”；
   - 提议自动分流：低风险原文引用自动标记 `auto_accepted`，高危冲突/设定提交人工审核。
9. **中文检索零暗礁**：
   - FTS5 配合应用层 jieba 分词，实体名称与物品名称自动导出为**项目专有分词词典（Project Lexicon）**。
10. **本地硬件守护与 Token 预算（RTX 2060 6GB）**：
   - 本地严守 6GB 显存红线；Embedding 常驻属于规划项，当前检索以 FTS 为准，单次检索控制在 2500~4000 tokens 黄金预算。
11. **四级生命周期语义**：
    - 严禁单点物理删除；区分 `exclude`（排除）、`disable`（停用/归档）、`supersede`（替代）与 `purge`（受控物理清理）。
12. **统一大模型网关与系统边界解耦**：
    - 知识库（Fxi）专注世界观事实、时空因果与状态记账，正文小说生成属于外部项目；
    - 知识库内部的抽取与审查统一收敛于 `model-gateway`；任务分级路由、JSON 容错修复和哈希缓存已作为当前边界，向量化、离线队列、自动 failover 与提示词外置仍需后续落地。

---

## 全套专题文档索引

| 序号 | 文档名称 | 核心职责与回答的问题 | 权威模块 |
|:---|:---|:---|:---|
| **00** | [00-canonical-architecture.md](00-canonical-architecture.md) | **架构总纲**：最高设计原则、数据流、真理源、模块拓扑与生命周期 | 全部模块 |
| **01** | [01-scope.md](01-scope.md) | **产品目标与边界**：系统定位、自用约束、非目标与核心闭环 | `scope` |
| **02** | [02-sources-and-import.md](02-sources-and-import.md) | **原始资料与导入**：txt/md 解析、不可变版本、结构层/检索层双层切片与精确定位 | `source`, `document` |
| **03** | [03-story-world-model.md](03-story-world-model.md) | **故事世界模型**：人物、物品、能力、关系、事件、对话及非线性时空因果 DAG | `domain`, `timeline` |
| **03a** | [03a-domain-model-purpose.md](03a-domain-model-purpose.md) | **领域模型深度解析**：阐述故事对象与事实/猜测/谎言/身份/时间线的本质区别与典型示例 | `domain` |
| **04** | [04-claims-and-provenance.md](04-claims-and-provenance.md) | **主张与可信度状态**：四层模型（命题/断言/判断/证据）、谎言与误解、状态迁移 | `claims` |
| **05** | [05-projects-and-overlays.md](05-projects-and-overlays.md) | **项目隔离与世界线**：多项目强隔离、AU 覆写、分歧点（POD）与蝴蝶效应继承阻断 | `project` |
| **06** | [06-character-knowledge-and-ooc.md](06-character-knowledge-and-ooc.md) | **角色认知与防 OOC**：时间区间有效性、认知获取途径、秘密揭示与防 OOC 自动检查 | `character-knowledge` |
| **07** | [07-materials.md](07-materials.md) | **创意素材库**：金手指、剧情套路、场景种子与灵感条目，严防污染原著事实 | `materials-style` |
| **08** | [08-retrieval-and-embeddings.md](08-retrieval-and-embeddings.md) | **检索与分词增强**：FTS5 中文分词方案、专有词典、本地 Embedding、混合检索与场景剪枝 | `index`, `retrieval` |
| **09** | [09-api-contract.md](09-api-contract.md) | **标准 API 契约 v1**：本地 CLI 与 JSON 协议、权限分级、错误码体系与调用约定 | `api-cli` |
| **09-int** | [09-integration-contract.md](09-integration-contract.md) | **集成边界总则**：知识库与 `novel-Skill` 的调用职责划分与解耦原则 | `api-cli`, `novel-Skill` |
| **09a** | [09a-novel-skill-style-integration.md](09a-novel-skill-style-integration.md) | **文笔风格协作机制**：风格档案、标注范例召回、场景风格注入与生成质量反思 | `materials-style` |
| **10** | [10-roadmap-and-acceptance.md](10-roadmap-and-acceptance.md) | **路线图与验收**：分阶段实施里程碑（MVP-0 到全功能）、回归测试用例与完成标准 | `ops` |
| **11** | [11-risks-and-decisions.md](11-risks-and-decisions.md) | **风险与决策备忘**：核心技术决断、系统风险及长远待决策项 | 全局 |
| **12** | [12-environments-conflicts-and-missing-capabilities.md](12-environments-conflicts-and-missing-capabilities.md) | **环境冲突控制**：多使用环境边界划分、权限四级模型与关键缺失能力防护 | 全局安全 |
| **13** | [13-module-map-and-document-index.md](13-module-map-and-document-index.md) | **模块功能映射**：模块代码依赖拓扑、功能归属字典与查阅指引 | 系统工程 |
| **14** | [14-future-scenarios-compatibility-and-evolution.md](14-future-scenarios-compatibility-and-evolution.md) | **后期场景与演进**：金币战力动态账本、风格评测回滚、四级删除生命周期与向后兼容 | `state-ledger`, `lifecycle` |
| **14a** | [14a-api-extension-notes.md](14a-api-extension-notes.md) | **API 扩展备忘**：动态状态账本、风格反馈、生命周期影响分析扩展接口规范 | `api-cli` |
| **15** | [15-model-management-and-llm-gateway.md](15-model-management-and-llm-gateway.md) | **大模型统一网关**：Provider 抽象、任务分级路由、JSON 容错修复、调用哈希缓存与成本记账 | `model-gateway` |
| **16** | [16-current-implementation-status-and-boundaries.md](16-current-implementation-status-and-boundaries.md) | **当前实现校准**：真实可用能力、依赖前置、设计与代码冲突、不可用功能、验收和故障边界 | 全局实现 |
| **17** | [17-system-usage-runbook.md](17-system-usage-runbook.md) | **系统调用说明**：Fxi CLI/API 路由、来源/候选/审批边界、能力隔离、多来源冲突和与 Studio 的交接 | `api-cli`, `ops` |

---

## 推荐阅读与实现顺序

0. **先读现实校准（避免把设计当成现状）**
   - 读 [16-current-implementation-status-and-boundaries.md](16-current-implementation-status-and-boundaries.md) 了解当前代码的真实边界；
   - 需要启动、认证、CLI/API、备份和重建细节时，继续读 [dev-docs/08-current-implementation-and-operations.md](../../dev-docs/08-current-implementation-and-operations.md)。
   - 需要代表用户调用知识库时，先读 [17-system-usage-runbook.md](17-system-usage-runbook.md)，再选择 CLI/API 入口。
1. **第一步：通读总纲与边界（建立全局观）**
   - 先读 [00-canonical-architecture.md](00-canonical-architecture.md) 了解最高原则与模块拓扑；
   - 读 [01-scope.md](01-scope.md) 确认当前自用边界与非目标。
2. **第二步：理解存储底座与文本导入**
   - 读 [02-sources-and-import.md](02-sources-and-import.md) 掌握原始文件不可变性、双层切片与精确定位。
3. **第三步：掌握故事模型与认识论区分**
   - 读 [03-story-world-model.md](03-story-world-model.md) 与 [03a-domain-model-purpose.md](03a-domain-model-purpose.md)；
   - 读 [04-claims-and-provenance.md](04-claims-and-provenance.md) 理解四层可信度模型；
   - 读 [05-projects-and-overlays.md](05-projects-and-overlays.md) 与 [06-character-knowledge-and-ooc.md](06-character-knowledge-and-ooc.md) 掌握多项目隔离与防 OOC 机制。
4. **第四步：掌握动态状态与创作素材**
   - 读 [07-materials.md](07-materials.md) 区分创意素材与原著事实；
   - 读 [14-future-scenarios-compatibility-and-evolution.md](14-future-scenarios-compatibility-and-evolution.md) 掌握金币战力账本与风格回滚。
5. **第五步：检索、调用契约与实施路线**
   - 读 [08-retrieval-and-embeddings.md](08-retrieval-and-embeddings.md) 与 [09-api-contract.md](09-api-contract.md)；
   - 读 [10-roadmap-and-acceptance.md](10-roadmap-and-acceptance.md) 准备按阶段落地编码。

---

## 文档维护规则

- 涉及**数据权威层级、写入流控、核心概念定义**时，必须严格遵守 `00`，修改需同步 `00`；
- 涉及**具体模块的功能扩展**时，修改对应专题文件，并在 `13` 中检查模块依赖是否发生变更；
- 任何新增状态、接口或实体类型，必须向后兼容并登记在 `14` 与 `14a`；
- 设计变更完成后，必须同步检查 `16` 与 `dev-docs/08`，明确标注“已实现”“条件可用”“仅规划”或“不可用”，不能只更新蓝图；
- `README.md` 与旧入口 `../knowledge-base-plan.md` 仅维护索引和导读，严禁在入口复制专题正文。
