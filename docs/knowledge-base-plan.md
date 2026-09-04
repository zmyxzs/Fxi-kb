# 自用写作与同人资料知识库规划

本文件是全库兼容入口。系统整体规划与架构方案已全面升级，并按问题域完成模块化拆分。

👉 **[点击打开知识库完整规划与专题索引](knowledge-base/README.md)**

---

## 核心总原则摘要

1. **纯文本优先（Text-First）**：以 Markdown + YAML Frontmatter 为唯一真理源，SQLite/FTS5/向量索引皆为可随时一键全量重建的加速缓存；
2. **实体对象与认识状态解耦**：故事对象（人物/物品/能力/关系/事件/对话）与认识状态（事实/推测/谎言/误导/推翻）严格分层；
3. **动态状态账本与三态模型（State Ledger）**：金币、战力与资源采用“变动事件账本 + 时间点快照”驱动计算，支持 `EXPLICIT`/`UNMEASURED` 三态与基准锚点，写到中途接入零包袱；
4. **多项目强隔离与分歧点（POD）**：多项目逻辑隔离；同人从原著分歧点（POD）切断动态情节事实继承，自动保留静态世界观法则；
5. **章节抗脆性（Semantic Scene UUIDs）**：语义场景 UUID 解耦物理章号变动，作者插章、删章、加更绝不断链；
6. **视点盲区与防穿帮（POV Filter）**：检索接口物理剔除观察者不可知的情报，杜绝反派/配角视角出现全知透视泄密；
7. **合法吃书（Retcon 追溯性修正）**：支持作者主动推翻旧设，豁免 OOC 误报并输出叙事圆场过渡建议；
8. **审核防疲劳**：按需惰性抽取，低风险引用自动标记，高危冲突人工阻塞审核；
9. **中文 FTS5 零失真**：Python jieba 结合实体动态词典保障玄幻/修仙/人名精准召回；
10. **本地硬件守护（RTX 2060 6GB）**：本地轻量 0.6B Embedding (~1.5GB) 配合超低成本云端 API，2500~4000 Token 黄金预算剪枝；
11. **四级生命周期**：区分 `exclude`、`disable`、`supersede` 与 `purge`，严禁粗暴单点物理删除；
12. **统一大模型网关与系统解耦**：知识库（Fxi）专注事实与状态提供，正文写作属于外部项目；知识库内部的抽取、审查与向量化统一经由 `model-gateway` 接入，支持任务分级路由、JSON 容错修复、哈希调用缓存与成本记账。

---

## 专题文档完整导航

- [00 知识库统一架构与文档总纲](knowledge-base/00-canonical-architecture.md)
- [01 产品目标与边界](knowledge-base/01-scope.md)
- [02 原始资料、作品导入与解析](knowledge-base/02-sources-and-import.md)
- [03 故事世界模型：人物、物品、能力、关系、事件与对话](knowledge-base/03-story-world-model.md)
- [03a 领域模型深度解析：这些区分分别解决什么问题](knowledge-base/03a-domain-model-purpose.md)
- [04 主张、证据、身份与信息状态](knowledge-base/04-claims-and-provenance.md)
- [05 写作项目、世界线与项目覆写](knowledge-base/05-projects-and-overlays.md)
- [06 角色认知、时间状态与 OOC 检查](knowledge-base/06-character-knowledge-and-ooc.md)
- [07 创意素材库](knowledge-base/07-materials.md)
- [08 检索、Embedding 与用途过滤](knowledge-base/08-retrieval-and-embeddings.md)
- [09 知识库 API 契约 v1](knowledge-base/09-api-contract.md)
- [09 知识库与 novel-Skill 集成契约总则](knowledge-base/09-integration-contract.md)
- [09a 知识库与 novel-Skill 的文笔风格协作](knowledge-base/09a-novel-skill-style-integration.md)
- [10 路线图与验收](knowledge-base/10-roadmap-and-acceptance.md)
- [11 风险、当前决定与待决策项](knowledge-base/11-risks-and-decisions.md)
- [12 使用环境、冲突控制与能力边界](knowledge-base/12-environments-conflicts-and-missing-capabilities.md)
- [13 模块、功能与文档关联索引](knowledge-base/13-module-map-and-document-index.md)
- [14 后期使用场景、兼容性与系统演进](knowledge-base/14-future-scenarios-compatibility-and-evolution.md)
- [14a API 扩展备注：动态状态、风格反馈与生命周期](knowledge-base/14a-api-extension-notes.md)
- [15 模型管理与统一大模型网关](knowledge-base/15-model-management-and-llm-gateway.md)