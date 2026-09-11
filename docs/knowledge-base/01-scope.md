# 01 产品目标与边界

> 状态：`CURRENT + PLANNED`
## 一句话定位

这是一个自用的本地写作知识库，以**纯文本（Markdown/TXT）为第一公民和单真理源**，用于把原著资料、正在写的作品、结构化故事状态、动态状态账本和创意素材组织起来，并在写作时查证设定、还原原文、追踪数值、发现漏洞和避免 OOC。

---

## 关键修正：内容类型、信息状态与动态演进是不同维度

1. **内容类型（对象轴）**：人物、物品、能力、关系、事件、对话、草稿、创意素材；
2. **信息状态（认识轴）**：原文明确、角色相信、读者猜测、AI 候选提议、项目设定、已推翻、未知；
3. **动态账本（演进轴）**：金币收支、战力评估、修为境界提升、法宝消耗与伤势增减（事件驱动快照）；
4. **使用用途（功能轴）**：查原文出处、查当前状态、查角色认知防 OOC、查时间线因果、找创作灵感、风格辅助。

同一个人物可以同时拥有身份、能力、关系、事件记录、数值变动和多个互相冲突的主张；这些不是互相排斥的死板文件夹，而是同一个故事世界模型的不同属性与关联。

---

## 当前范围与约束

- **自用、本地优先、无重型 UI**（以 CLI 与本地轻量 API 优先）；
- **纯文本单真理源**：所有核心设定存为 Markdown + YAML Frontmatter，SQLite/向量库为可随时丢弃重建的派生缓存；
- **当前输入以 txt 和 md 为主**；
- 支持原著资料、写作草稿、项目设定、剧情笔记、动态账本和创意素材；
- 三个写作项目默认独立，只有显式引用才共享原著或素材；
- 同人写作支持**分歧点（POD）**，阻断分歧后原著动态情节事实，继承静态世界观；
- 基础检索使用支持中文分词（jieba + 专有词典）的 SQLite FTS5，embedding 为可选增强；
- OCR、音频、视频、多模态、大模型自动写小说和多人协作暂不做。

---

## 模块分工与用途

| 部分 | 主要用途 | 权威文档 |
|---|---|---|
| **原始资料** | 找回原著正文、章节、对话和准确出处，不可变版本快照 | [02-sources-and-import.md](02-sources-and-import.md) |
| **故事世界模型** | 记录人物、物品、能力、地点、关系、事件、对话与非线性因果 | [03-story-world-model.md](03-story-world-model.md) |
| **主张与证据** | 区分事实、猜测、谎言、误解、AI 提议、项目 AU 设定与推翻关系 | [04-claims-and-provenance.md](04-claims-and-provenance.md) |
| **项目覆写与 POD** | 保存各作品独立改写，控制分歧点后的蝴蝶效应继承 | [05-projects-and-overlays.md](05-projects-and-overlays.md) |
| **角色认知 / OOC** | 基于区间有效性判断角色当时知晓什么、能做什么，防止人设崩塌 | [06-character-knowledge-and-ooc.md](06-character-knowledge-and-ooc.md) |
| **创意素材** | 保存金手指、套路、场景种子与写作技法，杜绝污染原著事实 | [07-materials.md](07-materials.md) |
| **动态状态账本** | 统计金币、资源流转、修为境界与战力指标，支持剧情修改后重算 | [14-future-scenarios-compatibility-and-evolution.md](14-future-scenarios-compatibility-and-evolution.md) |
| **检索与剪枝接口** | 根据当前项目、场景类型（combat/dialogue 等）与 Token 预算召回证据 | [08-retrieval-and-embeddings.md](08-retrieval-and-embeddings.md) |

---

## 核心写作支持问题

知识库最终要支持这些高频创作问答：

- 原作中角色 A 什么时候见过角色 B？
- 角色 A 在第 3 章时知道秘密 X 吗？（防秘密早泄 OOC）
- 角色 A 当时有哪些能力、物品、伤势和金币余额？（防战力穿越与透支）
- 这句对话是谁说的、对谁说的、发生在什么时间、是否为谎话？
- 这是原作明确事实、角色谎话、读者猜测还是我的 AU 设定？
- 我的同人作品从第 10 章改变了剧情，原著第 15 章发生的大事件是否应该生效？
- 三个作品中，哪一个使用了这个金手指或剧情套路？
- 写的文笔效果很差，如何一键回滚最近引入的风格规则？

---

## 核心闭环

```text
原著 / 草稿 / 素材 (纯文本底本)
  → 保存不可变版本并建立 jieba 中文 FTS5 索引
  → 建立故事世界对象、主张证据与动态账本
  → 绑定项目分歧点 (POD)、世界线与时间点
  → 按场景类型 (战斗/对白/过渡) 实施上下文剪枝检索
  → 返回精炼证据包、冲突提示与防 OOC 警示
```

---

## 相关专题

- 总架构：[00-canonical-architecture.md](00-canonical-architecture.md)
- 原文与对白：[02-sources-and-import.md](02-sources-and-import.md)
- 故事对象：[03-story-world-model.md](03-story-world-model.md)
- 主张状态：[04-claims-and-provenance.md](04-claims-and-provenance.md)
- 角色认知：[06-character-knowledge-and-ooc.md](06-character-knowledge-and-ooc.md)
- 后期演进与账本：[14-future-scenarios-compatibility-and-evolution.md](14-future-scenarios-compatibility-and-evolution.md)
