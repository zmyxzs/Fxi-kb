# 02 原始资料、作品导入与文本解析

> 状态：`CURRENT + PLANNED`
> **实现状态提示（2026-09）**：当前已落地 txt/md 解析、来源版本对象、哈希校验、场景切片和 FTS 写入；向量任务队列以及“删除数据库后 100% 重建”尚未形成可调用闭环，实际限制见 [16-current-implementation-status-and-boundaries.md](16-current-implementation-status-and-boundaries.md)。

## 这一部分用来做什么

它只负责回答：

- 原文文件保存在哪里（纯文本文件第一公民）；
- 原文是什么版本、文件哈希是否一致；
- 卷、章节、段落、场景和对白位于哪里；
- 如何把原文切分为可检索但**严格可回溯到行与字符位置**的片段；
- 怎样通过语义场景 UUID 隔离章节重排冲突，以及毫秒级增量分词。

> 它不负责判断角色说的是否是真话，也不负责决定人物关系。那些属于故事世界模型（03）和主张专题（04）。

---

## 来源层级模型

```text
source (逻辑作品或资料来源，如《武动乾坤》原著)
  └── source_version (某次导入的文件快照、SHA-256 哈希)
        └── document (规范化为 UTF-8 的解析文档)
              └── structural_unit (卷、章、节、语义场景 UUID)
                    └── chunk (512~1024 字符的检索单元，带精确定位)
```

---

## 语义场景锚点 vs 物理章节号（防重排灾难）

### 传统主键设计的隐患
长篇连载中，作者经常在第 15 章前面插入两个过渡章节，原第 15 章被重命名为第 17 章。如果系统底层将物理章号 `ch-015` 作为硬主键，所有绑定该章的主张、状态快照和定位链接将全线报废。

### 解决方案：语义场景 UUID
- **物理章号仅为展示 Label**：`display_label: "第十五章 荒原对峙"`，可随时重命名；
- **底层锚定语义 UUID**：每个场景块分配不可变语义标识 `scene_uuid: "scene_battle_cliff_01"`；
- 无论章节怎么插入、调换顺序，所有主张与证据引用永不断链。

---

## 输入类型与编码处理

1. **Markdown（.md）**：
   - 提取 YAML Frontmatter 保存为元数据；
   - 严格保留一至六级标题形成的卷、章、节路径树（`heading_path`）；
   - 代码块、引用块、列表完整保留，不因清洗破坏排版。
2. **纯文本（.txt）**：
   - 自动探测编码（UTF-8, GBK, GB2312, Big5），记录原始编码，统一转为标准 UTF-8；
   - 采用正则规则识别“第 X 卷”、“第 X 章”、“Chapter X”等章节标记；
   - 缺少章节标记时，按自然段和空行切分，不让模型瞎猜章节。

### 草稿处理模式（按需解析防疲劳）
| 模式 | 行为 | 适用场景 |
|---|---|---|
| `store-only` | 仅做文件哈希校验与版本归档，不切片不抽取 | 随手记录的零碎灵感、临时大纲 |
| `index-only`（默认） | 解析章节结构，更新 jieba 中文 FTS5 索引（毫秒级） | 正在写作的正式小说草稿 |
| `extract` | 仅在用户显式指定时，对特定章节产生待审提议 | 确认定稿并需要归档为正式设定的章节 |

---

## 完整导入流水线（CPU 增量毫秒级响应）

```text
原始 txt/md 文件保存
   │
   ├─► 1. 计算 SHA-256 哈希，对比已有版本判断是否为重复或更新
   ├─► 2. 规范化处理（去除 BOM、统一换行符 \n）
   ├─► 3. 提取章节/场景树，分配不可变 scene_uuid
   ├─► 4. 划分 512~1024 字符的 chunk（在段落边界断开，保留 10% 上下文重叠）
   │
   ├─► 5. 提取实体专有词汇，热加载至 jieba 项目专有词典 (Project Lexicon)
   ├─► 6. 应用层结巴增量分词（纯 CPU 执行，耗时 < 10ms，心流零卡顿）
   ├─► 7. 写入 SQLite FTS5 中文全文虚拟表
   │
   └─► 8. （规划）向量任务推入异步闲时队列（Idle Worker），不阻塞当前写作保存
```

> **铁律**：导入和解析必须由纯确定性算法完成，严禁在基础导入中引入外部大模型，保障断网可用、极速与结果 100% 确定。

---

## 检索切片（Chunk）元数据规范

每个检索切片必须携带完整的出处凭证与语义锚点：

```yaml
chunk_id: "chk-canon-01-cliff-004"
source_id: "canon-original-01"
source_version_id: "sv-hash-a1b2c3d4"
heading_path: "第二卷 大荒秘境 / 第十二章 荒原对峙"
scene_uuid: "scene_battle_cliff_01"   # 语义锚点，不受章号重排影响
display_chapter: "第 12 章"           # 可变展示标签
start_line: 145
end_line: 182
char_start: 3204
char_end: 3850
prev_chunk_id: "chk-canon-01-cliff-003"
next_chunk_id: "chk-canon-01-cliff-005"
text: "林动握紧了手中的石符，目光穿过迷雾..."
```

---

## 对话原文视图（Dialogue View）

```yaml
dialogue_id: "dlg-0089"
chunk_id: "chk-canon-01-cliff-004"
speaker_id: "character:lin_dong"
audience_id: "character:ling_qingzhu"
story_time: "canon: vol-2/chapter-12"
text_span: "此地不宜久留，跟我走！"
locator: "lines 156-158"
```

---

## 容灾与受限重建机制（Text-First 目标）

- **数据解耦**：原始 `.md` 和 `.txt` 是磁盘上的独立物理文件；
- **可回放投影重建**：运行 `kb rebuild` 时，系统只扫描并刷新当前具备 replay contract 的实体、阶段、关系和 FTS 投影；动态账本、认知、连续性和 v2 审计表存在数据时会拒绝清理，不能当作全量恢复；
- **故障透明**：如果某个文件解析失败，明确输出文件路径与错误行号，绝不跳过或静默吞并错误。
