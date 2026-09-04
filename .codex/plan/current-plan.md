状态：done
# 实施计划：知识库规划领域重构（2026-09-04）

## 1. 目标与约束

- 目标：将规划按“原始资料、故事世界模型、主张证据、项目覆写、角色认知/OOC、创意素材、检索接口”重新分层。
- 非目标：不实现应用代码、不解析真实作品、不下载模型、不调用外部 API。
- 硬约束：每个专题回答一个清晰问题；事实状态不与数据对象混为一谈；项目之间默认隔离；旧文件只在同目录内重命名，不删除内容。
- Done when：新文件清单存在，入口链接完整，旧总入口指向新结构，专题内容覆盖用户提出的对象、状态和用途。

## 2. 现状结构（只读侦察结论）

现有规划已经拆成多个文件，但将主张、身份、时间线、人物状态和世界实体放在同一专题，导致“信息是什么”和“信息处于什么状态/用于什么检查”混杂。项目隔离、原始资料、检索和素材文件可继续复用，但需要重新命名和明确边界。

## 3. 目标架构

    01-scope
      → 02-sources-and-import
      → 03-story-world-model
      → 04-claims-and-provenance
      → 05-projects-and-overlays
      → 06-character-knowledge-and-ooc
      → 07-materials
      → 08-retrieval-and-embeddings
      → 09-integration-contract
      → 10-roadmap-and-acceptance
      → 11-risks-and-decisions

- 01：回答系统为什么存在、当前做什么。
- 02：回答原文和草稿如何保存、解析、定位。
- 03：回答故事里有哪些实体、属性、关系、事件和对话。
- 04：回答这些信息的来源、真假状态、推测关系和变更关系。
- 05：回答三个写作项目如何独立、复用资料和保存覆写。
- 06：回答角色在某个时间点知道什么，以及如何检查 OOC。
- 07：回答创意素材如何独立保存和复用。
- 08：回答如何通过全文和语义检索找到上述内容。
- 09：回答 CLI、JSON 和 novel-Skill 如何调用。
- 10：回答先实现什么、如何验收。
- 11：回答风险和未决策项。

## 4. 复用映射

| 原文件 | 新职责文件 | 处理 |
|---|---|---|
| 01-scope.md | 01-scope.md | 重写为总边界和用途说明 |
| 03-sources-and-import.md | 02-sources-and-import.md | 同目录重命名并聚焦来源/解析 |
| 新增 | 03-story-world-model.md | 新增故事世界对象和结构化状态 |
| 04-claims-identities-timeline.md | 04-claims-and-provenance.md | 重命名并聚焦主张、证据、真假状态 |
| 02-projects-and-versions.md | 05-projects-and-overlays.md | 重命名并聚焦项目覆写和版本 |
| 新增 | 06-character-knowledge-and-ooc.md | 新增角色认知和 OOC 检查 |
| 05-materials.md | 07-materials.md | 重命名并聚焦创意素材 |
| 06-retrieval-and-embeddings.md | 08-retrieval-and-embeddings.md | 重命名并聚焦检索 |
| 07-integration-contract.md | 09-integration-contract.md | 重命名并聚焦调用契约 |
| 08-roadmap-and-acceptance.md | 10-roadmap-and-acceptance.md | 重命名并聚焦路线图和测试 |
| 09-risks-and-decisions.md | 11-risks-and-decisions.md | 重命名并聚焦风险和决策 |

## 5. 接口契约（冻结区）

- README 只维护入口和专题用途，不复制专题正文。
- sources 提供 source_version、document、structural_unit、chunk 和 locator。
- story-world-model 提供 entity、attribute、relation、event、dialogue。
- claims-and-provenance 为上述对象附加 assertion、evidence、status、truth_status 和关系。
- projects-and-overlays 提供 project_id、worldline_id、revision、link 和 as-of。
- character-knowledge-and-ooc 提供 knowledge_state 和 consistency_check。
- materials 提供独立的 material 条目。
- retrieval 消费这些作用域和对象，返回分组证据。
- integration 只消费公开证据包，不读取其他专题内部实现。

## 6. 工作包

### W1：重命名并重写领域文件

- 写入范围：docs/knowledge-base/01-scope.md；02-sources-and-import.md；04-claims-and-provenance.md；05-projects-and-overlays.md；07-materials.md；08-retrieval-and-embeddings.md；09-integration-contract.md；10-roadmap-and-acceptance.md；11-risks-and-decisions.md
- 依赖：无
- 并行组：A
- 验收：每个文件标题和职责与 README 一致
- 串行项：无

### W2：新增故事世界模型和角色认知专题

- 写入范围：docs/knowledge-base/03-story-world-model.md；06-character-knowledge-and-ooc.md
- 依赖：W1 的领域边界
- 并行组：A
- 验收：包含人物、物品、能力、数据、关系、事件、对话、时间变化、角色认知和 OOC 用例
- 串行项：无

### W3：更新入口与兼容入口

- 写入范围：docs/knowledge-base/README.md；docs/knowledge-base-plan.md
- 依赖：W1、W2
- 并行组：B
- 验收：入口链接指向新文件；旧专题不出现在主导航；兼容入口只保留摘要和链接
- 串行项：主 agent 收尾

### W4：结构验证

- 写入范围：.codex/plan/current-plan.md
- 依赖：W1、W2、W3
- 并行组：C
- 验收：文件数量、关键标题、链接目标和状态均通过检查
- 串行项：主 agent 收尾

## 7. 里程碑与验证

| 里程碑 | 工作包 | 验证 | 失败时 |
|---|---|---|---|
| M1 | W1, W2 | 新文件清单与关键标题检查 | 补齐对应专题后重检 |
| M2 | W3 | README 和旧入口链接检查 | 修正链接后重检 |
| M3 | W4 | 文件数量、计划状态和职责关键字检查 | 修正结构后重检 |

## 8. 明确不做

- 不实现解析器、数据库、CLI、向量索引或一致性检查器。
- 不把事实、猜测、谎言、身份、时间线继续作为互相孤立的顶层“资料类型”。
- 不为三个项目创建自动共享的全局当前事实。
- 不把创意素材混进原著事实。
- 不添加 UI、OCR、Copilot、多模态或多人协作。