# Fxi 知识库与创作底座：系统开发设计全景与主索引

> 状态：`CURRENT + PLANNED`

---

## 1. 系统定位与全局关系

本项目（`d:\Code\Fxi`）是面向长周期（3~5 年、数百万字）创作的**本地优先、纯文本底座、模块化单体知识库与创作数据中心**。

### 1.1 双系统协同定位
- **外部学习与写作引擎（`d:\Code\novel-Skill`）**：负责从海量参考文本中提取写作技法、运行未见章节留出验证、执行行为评测，以及在写作时进行章纲规划、场景编排与小说正文起草；
- **知识库与数据中心（`d:\Code\Fxi`，本项目）**：
  1. **承载写作作品**：存储作者的所有作品目录、章节正文草稿（`draft.md`）、草稿分支沙箱及作品元数据；
  2. **承载学习成果**：存储 `novel-Skill` 提炼晋升的写作技法规则（`skills/`）、反面禁写教条（`anti_patterns`）、创意素材（`materials/`）与可信主张；
  3. **提供数据服务**：提供客观世界观实体、时空因果 DAG、动态状态账本（金币/战力）、同人分歧点过滤（POD）、视点防穿帮（POV）以及 FTS5 中文检索。

---

## 2. 开发文档体系总览

本套开发文档（`dev-docs/`）为 `Fxi` 的工程落地蓝图，严格按照“可落地、可编码、文件职责明确、接口契约冻结”的标准制定：

> **文档层次**：`01`～`07` 主要是设计蓝图、契约和实施路线；[08-current-implementation-and-operations.md](08-current-implementation-and-operations.md) 是当前工作区的 as-built 实现手册。实现状态、真实命令、失败分支和未落地能力以 `08`、代码和实际验证为准，不能仅凭蓝图中的“支持”判断功能已上线。

| 编号 | 文档名称 | 核心内容与回答的工程问题 | 对应核心代码路径 |
|:---:|:---|:---|:---|
| **01** | [01-system-architecture.md](01-system-architecture.md) | **系统全景架构与双系统协同**：`Fxi` 与 `novel-Skill` 双核交互拓扑、数据流向闭环、真理源与派生层划分 | 全局架构 |
| **02** | [02-directory-structure-and-storage-layout.md](02-directory-structure-and-storage-layout.md) | **物理目录结构与存储落盘规范**：工程完整目录树、作品/章节存储、学习成果与技能存储落盘格式 | `data/`, `projects/`, `skills/` |
| **03** | [03-module-and-file-responsibilities.md](03-module-and-file-responsibilities.md) | **模块清单与文件职责详细划分**：`src/fxi/` 下每一个具体 Python 文件的职责、类设计、函数签名与入参出参 | `src/fxi/**/*.py` |
| **04** | [04-dependencies-associations-and-code-reuse.md](04-dependencies-associations-and-code-reuse.md) | **关联、引用与代码复用设计**：模块单向依赖拓扑、与 `novel-Skill` 的 Pydantic 契约复用、双系统桥接 | `src/fxi/core/`, 跨项目接口 |
| **05** | [05-data-schema-and-sqlite-ddl.md](05-data-schema-and-sqlite-ddl.md) | **数据契约、YAML 规范与 SQLite DDL**：纯文本 Markdown/YAML 规范、SQLite 表结构建表 DDL、一键重建流水线 | `src/fxi/storage/` |
| **06** | [06-engineering-standards-and-testing.md](06-engineering-standards-and-testing.md) | **工程规范、错误处理与测试验收**：Windows/PowerShell 运行纪律、异常类继承树、日志与 19 项回归测试用例 (含游戏技能与领地结算) | `tests/`, 全库代码 |
| **07** | [07-implementation-roadmap.md](07-implementation-roadmap.md) | **分步实施路线图与开发任务包**：Step 0 到 Step 5 的循序渐进落地计划、各阶段代码清单与交付里程碑 | 研发执行全周期 |
| **08** | [08-current-implementation-and-operations.md](08-current-implementation-and-operations.md) | **当前实现与运维**：真实 CLI/API、认证、数据权威层、FTS、模型网关、备份/重建、可用性矩阵与故障排查 | 当前代码与运行流程 |

---

## 3. 核心设计铁律（开发必须严格遵守）

1. **纯文本第一公民（Text-First Single Source of Truth）**：
   - 核心资产（设定、人物卡、因果事件、草稿 `draft.md`、技能规则）一律以 Markdown + YAML Frontmatter 格式存储在硬盘上；
   - 设计目标是让 SQLite、FTS5 中文全文表和向量索引成为可重建的派生层；当前只能安全重放部分实体/关系/FTS 投影，实际边界以 [08-current-implementation-and-operations.md](08-current-implementation-and-operations.md) 为准。
2. **游戏文与领主流的阶梯式投影（Anti-Bloat & Deterministic Calculation）**：
   - 绝不将 100 个技能与 20 种领地资源全量倾倒进 Prompt；
   - 采用【场景显性焦点面板 + 宏观剧作语义标签 + 被动防吃书雷达】的三层阶梯投影，将设定占用严控在 300~500 Tokens 内；
   - 技能冷却、蓝量消耗、领地日产耗必须由 Python 确定性规则引擎计算，严禁依赖大模型心算。
3. **知识库内部模型统一网关（Model Gateway）**：
   - 知识库自身的抽取、审查、摘要与向量化统一经由 `src/fxi/model_gateway/` 调用；
   - 业务模块严禁手写散乱的 HTTP 请求与第三方 SDK 调用；
   - 提示词模板外部化为 Markdown 文件（`prompts/`），支持无重启热更新。
4. **分歧点与视点过滤（POD & POV）**：
   - 同人作品从原著分歧点自动阻断动态情节事件，保留静态世界观；
   - 场景检索强制按观察者 `pov_character_id` 物理剥离未知的机密底牌。
5. **状态账本三态与语义场景 UUID**：
   - 数值（金币/战力）支持 `EXPLICIT` / `UNMEASURED` / `NOT_APPLICABLE` 三态模型，中途插入 `baseline_anchor` 基准锚点零历史包袱；
   - 底层关系一律绑定不可变 `scene_uuid`，物理章节重排、插章加更绝不断链。
6. **软删除与墓碑机制**：
   - 严禁粗暴 `DELETE`，采用 `exclude` / `disable` / `supersede` / `purge` 四级删除生命周期。

---

## 4. 推荐开发阅读与执行顺序

0. **第 0 步：先确认当前实现边界**
   - 阅读 [08-current-implementation-and-operations.md](08-current-implementation-and-operations.md)，确认哪些接口能调用、哪些能力需要数据或外部注入、哪些功能尚未实现；
   - 再回到 `docs/knowledge-base/16-current-implementation-status-and-boundaries.md` 阅读面向知识库使用者的可用性结论。
1. **第 1 步：通读架构全景与目录规划**
   - 阅读 [01-system-architecture.md](01-system-architecture.md) 确立整体心智模型；
   - 阅读 [02-directory-structure-and-storage-layout.md](02-directory-structure-and-storage-layout.md) 掌握落盘目录树。
2. **第 2 步：掌握模块、文件与跨项目复用**
   - 阅读 [03-module-and-file-responsibilities.md](03-module-and-file-responsibilities.md) 查阅具体代码文件与函数定义；
   - 阅读 [04-dependencies-associations-and-code-reuse.md](04-dependencies-associations-and-code-reuse.md) 了解如何复用 `novel-Skill` 已有成果与契约。
3. **第 3 步：掌握存储契约与工程标准**
   - 阅读 [05-data-schema-and-sqlite-ddl.md](05-data-schema-and-sqlite-ddl.md) 获取建表 DDL 与 YAML 格式模板；
   - 阅读 [06-engineering-standards-and-testing.md](06-engineering-standards-and-testing.md) 掌握异常体系与测试规范。
4. **第 4 步：按路线图编码实施**
   - 按照 [07-implementation-roadmap.md](07-implementation-roadmap.md) 的任务包逐步推进。

实现每个任务包后，回写 `08` 中对应的命令、接口、数据依赖和失败边界；若目标能力仍未落地，应保留“规划/条件可用/不可用”标记，避免设计文档与实际状态再次分叉。
