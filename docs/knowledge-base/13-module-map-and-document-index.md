# 模块、功能与文档关联索引

> 状态：`CURRENT` + `PLANNED`
>
> - `CURRENT`：标有当前入口/边界的内容，须能由当前代码、测试或现行运行手册核对；入口可发现不代表认证、数据、模型或跨仓业务已经就绪。
> - `PLANNED`：标有设计目标的内容属于架构/路线图方向；没有对应的当前闭环证据时，不得当作保证。
>
> 当前核对依据：`src/fxi/api/server.py`、`src/fxi/api/router_v3.py`、`src/fxi/cli/main.py`、`src/fxi/cli/commands_v3.py`、`src/fxi/cli/commands_runtime.py`、`src/fxi/runtime/`、`tests/v3/`、`tests/test_runtime_local.py`、`docs/knowledge-base/16-current-implementation-status-and-boundaries.md`、`docs/knowledge-base/17-system-usage-runbook.md`。

## 1. 使用方式与总则

本文是“查问题应该先看哪份文档”的系统索引，也是多模块边界仲裁的唯一归属表。遇到语义冲突时，优先遵守 [00-canonical-architecture.md](00-canonical-architecture.md) 的总原则，再看对应模块的权威文档。

---

## 2. 全模块功能总览

| 模块 | 核心问题 | 主要功能 | 权威文档 | 依赖 / 被谁使用 |
|---|---|---|---|---|
| `storage` | 数据放在哪里、如何容灾恢复 | 纯文本目录底座、SQLite、事务、迁移；`一键全量重建` 是 `PLANNED` 设计目标，当前只允许条件重建，非回放表有数据时 fail closed | `00`、`10`、`14`、`16` | 所有持久化模块的基础 |
| `source` | 原始资料是什么 | 导入、不可变 revision、文件哈希、来源范围 | `02`、`12` | `document`、`index`、`analysis` |
| `document` | 如何从 txt/md 得到可引用片段 | 章节、段落、场景、对白结构化定位、检索切片 chunking | `02` | `source`、`retrieval`、`claims` |
| `domain` | 故事里有哪些对象与实体 | 人物、物品、能力、地点、组织、关系、事件、对话结构 | `03`、`03a` | `claims`、`timeline`、`retrieval` |
| `claims` | 这条说法是否有依据 | 命题规范化、断言状态、证据定位、冲突、推翻关系 | `04`、`03a`、`12` | `analysis`、`retrieval`、`checks` |
| `timeline` | 什么时候发生、先后因果关系 | 故事时间因果 DAG、叙事阅读顺序、区间有效性继承 | `03`、`04`、`06` | `checks`、`retrieval` |
| `character-knowledge` | 角色当时知道什么、防 OOC | 认知状态（known/suspected 等）、获取途径、秘密揭示检查 | `06`、`12` | `novel-Skill`、`checks` |
| `state-ledger` | 角色金币/资源/战力当时是多少 | 指标定义、变化事件记录、时间点派生快照计算 | `03`、`14`、`14a` | `checks`、`novel-Skill` |
| `rules-calculation` | 战力/资源如何换算与结算 | 项目规则集版本、多维战力指标、不可覆盖公式解释 | `14` | `state-ledger`、`project` |
| `project` | 内容属于哪个作品、AU 覆写 | project、world、timeline、分歧点（POD）与蝴蝶效应继承阻断；W27 提供显式迁移评估端口 | `05`、`12`、`16` | 所有故事与状态查询、`world-transfer` |
| `world-transfer` | 跨世界/分支迁移是否有足够显式依据 | 世界坐标、分歧点、资源/能力政策与历史声明的保守分类；输出 retained/invalidated/unknown 候选，不推断完整蝴蝶效应 | `05`、`12`、`16` | `project`、`checks`；当前仅库级纯计算 |
| `materials-style` | 如何复用写作经验与创意素材 | 金手指、桥段素材、风格档案、范例标注 | `07`、`09a` | `retrieval`、`novel-Skill` |
| `quality-evaluation` | 文笔写得差如何评测与回滚 | 生成上下文快照、用户评价、风格规则停用与回滚 | `09a`、`14`、`14a` | `novel-Skill`、`materials-style` |
| `analysis-proposals` | AI 能否协助整理且不添乱 | 按需惰性抽取、待审核提议队列、低风险自动分流标记 | `04`、`09`、`12` | 外部/本地模型 |
| `lifecycle` | 删错内容如何撤销与安全清理 | 四级生命周期（exclude/disable/supersede/purge）、影响分析 | `14`、`14a` | 全部持久化对象 |
| `index` | 如何快速且精准搜出中文内容 | jieba 中文分词、专有词典、FTS5、本地 embedding、失效检测 | `08`、`12` | `retrieval` |
| `retrieval` | 本次写作应该返回什么材料 | 作用域强制过滤、混合召回、重排、场景化上下文剪枝 | `08`、`09` | `novel-Skill`、CLI |
| `checks` | 草稿是否存在逻辑漏洞与 OOC | 时间线因果、能力可用性、认知跃迁、资源透支检查 | `06`、`10`、`09-api-contract` | `novel-Skill`、CLI |
| `model-gateway` | 如何统一接入大模型且不写重复代码 | Provider 抽象（OpenAI/Gemini/Ollama）、任务分级路由、JSON 容错修复、哈希调用缓存、成本记账、提示词注册表 | `15`、`00` | `analysis-proposals`、`character-knowledge`、`materials-style`、`document` |
| `api-cli` | 外部程序或作者如何安全调用 | 本地 v1/v2/v3 HTTP 契约、v3 CLI 命令、只读/写入保护、错误码与权限 | `09-api-contract`、`14a`、`16`、`17` | `novel-Skill`、`runtime`、未来工具 |
| `ops` | 系统如何体检、备份与重建 | 体检、SQLite VACUUM 备份、跨版本迁移；无条件一键全量重建是 `PLANNED` 设计目标，当前 `rebuild` 仅能安全重放允许的投影 | `10`、`11`、`14`、`16` | 管理命令与作者维护 |
| `runtime` | 如何启动、复用、查看和停止本地 Fxi 服务 | `kb v3 runtime start/status/stop`、有界 `/health` 探针、工作区 `.fxi/runtime/runtime.json` 非敏感运行清单；不负责 provider/auth，也不等同于业务 readiness | `16`（当前边界）；实现/测试：`src/fxi/runtime/`、`tests/test_runtime_local.py` | `api-cli`、本地作者/Studio 适配器 |

### 2.1 当前 v3 公开入口（`CURRENT`）

`src/fxi/api/server.py` 的 `create_app()` 同时挂载 v1、v2 和 v3 router；`src/fxi/api/router_v3.py` 的 router 前缀为 `/v3`。以下按实际注册路由分组，仅表示公共边界已存在并可由代码/测试核对，不表示依赖数据、认证、模型或跨仓业务已就绪。

| 公共面 | 实际入口 | 当前边界与证据 |
|---|---|---|
| 发现与状态 | `GET /v3/capabilities?work_id=...`、`GET /v3/health`、`GET /v3/readiness` | 能力发现、进程存活和分层 readiness 是不同信号；`/health` 不能单独证明业务可用。 |
| 项目与来源 | `POST /v3/projects`；`POST /v3/sources/bindings`、`/v3/sources/attachments`、`/v3/sources/snapshots`；`GET /v3/sources/active`；`POST /v3/sources/read`、`/v3/sources/windows` | 包含来源绑定、托管附件、不可变快照和有界只读来源读取；只读来源端点不创建候选或修改 KnowledgeVersion。 |
| 候选治理 | `POST /v3/candidates`、`/v3/evaluations`、`/v3/decisions`、`/v3/promotions` | 候选、评测、决策和晋升仍受作用域、证据、版本/hash 与审批条件约束。 |
| 上下文与查询 | `POST /v3/context/views`、`POST /v3/query` | 分别提供 ContextView 和知识查询公共入口；结果的证据状态与完整性不能由非空 JSON 推定。 |
| 写作与投影 | `POST /v3/writing/reviews`、`/v3/writing/proposals`、`/v3/approvals`、`/v3/writing/commits`、`/v3/projections/rebuild` | 审查、提案、批准、提交和投影重建均是显式写入边界；未满足条件时应保留错误码并停止。 |

对应的 v3 CLI 由 `src/fxi/cli/main.py` 注册为 `kb v3`（模块入口为 `python -m fxi.cli.main v3`）：`capabilities`、`project`、`source-attach`、`source-bind`、`source-snapshot`、`candidate`、`evaluate`、`decide`、`promote`、`context`、`query`、`review`、`proposal`、`approve`、`commit`、`projection-rebuild`、`health`、`readiness`。结构化操作通过 `src/fxi/cli/commands_v3.py` 调用版本化 HTTP 端点；命令注册和端点映射由 `tests/v3/test_cli_v3.py` 覆盖。

`kb v3 runtime start/status/stop` 是同一 CLI 下的本地进程外观：`start` 在必要时以有界等待启动 `uvicorn fxi.api.server:create_app --factory`，`status` 只检查 `/health` 而不启动服务，`stop` 使用运行清单请求停止；清单不保存 provider、token 或请求正文。该能力只证明本地进程管理边界，不证明 `/v3/readiness`、认证、数据库、模型或跨仓 M1 业务已经就绪。W22/W23/W24/W25/W27 等仍是库级边界的能力，不因存在 Python 实现而自动成为 v3 CLI/API 入口。

---

## 3. 核心依赖拓扑

```text
               storage (纯文本底座 + SQLite 衍生索引)
                   ↑
source ──► document ──► domain ──► claims
  │           │            │          │
  │           │            ├──────────┴─► state-ledger ◄── rules-calculation
  │           │            │                     │
  └───────────┴────────────┼─────────────────────┴──────────► index
                           ▼
                        project (POD 分歧点) ──────────────► retrieval
                           │                                      ▲
timeline ──────────────────┼──────────────────────────────────────┤
character-knowledge ───────┴──────────────────────────────────────┤
materials-style ◄── quality-evaluation ───────────────────────────┤
analysis-proposals ──────────────────► claims / domain / materials (经审核)
lifecycle ───────────────────────────► 全模块（软硬生命周期管控）
model-gateway ───────────────────────► 支撑 analysis-proposals / character-knowledge / materials-style / index
                                                                   │
                                                                retrieval / checks
                                                                       ▲
                                                                       │
                                                                    api-cli
                                                                       ▲
                                                                       │
                                                                  novel-Skill
```

### 依赖铁律：
1. **单向依赖**：底层模块严禁反向依赖 `novel-Skill` 或 API 表现层；
2. **只读检索**：`retrieval` 模块绝对禁止具有写入数据库的能力；
3. **提案隔离**：`analysis-proposals` 输出一律进入暂存队列，严禁绕过审核直接写入 `domain` 或 `claims`；
4. **派生重建目标**：`PLANNED` 目标是让 `index` 与 `state-ledger` 的派生数据可通过原始纯文本与变化事件重建；`CURRENT` 边界是仅安全回放有完整 replay contract 的投影，非回放表有数据时必须 fail closed，不能宣称 100% 恢复完整知识库；
5. **模型接入集中化**：知识库内部使用大模型必须全部收敛于 `model-gateway`，业务模块禁止直连第三方 SDK。

---

## 4. 全专题文档权威职责与范围

| 文档路径 | 权威负责的内容 | 明确不负责的内容 |
|---|---|---|
| `00-canonical-architecture.md` | 架构总纲、真理源、设计哲学、防坑守则、数据流与全局状态 | 不展开每个接口的 JSON 细节 |
| `01-scope.md` | 目标、自用约束、非目标、核心闭环 | 不定义具体数据库表字段 |
| `02-sources-and-import.md` | 原始文本导入、版本快照、双层切片、精确定位 | 不判断对白真伪 |
| `03-story-world-model.md` | 故事对象（人物/物品/能力/地点/关系/事件/对话）、时空因果 | 不决定主张的可信度与项目优先级 |
| `03a-domain-model-purpose.md` | 领域模型深度解析、对象与状态的区别、典型写作示例 | 不替代具体数据设计 |
| `04-claims-and-provenance.md` | 命题四层模型、主张状态、证据定位、谎言与误解、推翻关系 | 不负责文本检索实现 |
| `05-projects-and-overlays.md` | 多项目隔离、AU 覆写、分歧点（POD）与后分歧点继承阻断 | 不负责模型调用逻辑 |
| `06-character-knowledge-and-ooc.md` | 角色认知、时间区间有效性、信息获取途径、防 OOC 自动检查 | 不负责一般语言修辞文风 |
| `07-materials.md` | 金手指、套路、场景种子、灵感与技巧素材 | 严禁将素材作为原作事实检索 |
| `08-retrieval-and-embeddings.md` | 中文分词、专有词典、本地 embedding、混合重排、场景剪枝 | 不定义外部权限模型 |
| `09-api-contract.md` | v1 本地 API 契约、旧 CLI 映射、统一请求响应、错误码与权限 | 不定义 v3 wire contract，也不实现生成文本流程 |
| `09-integration-contract.md` | 知识库与 `novel-Skill` 的协作职责与边界原则 | 不列出重复的 endpoint 定义 |
| `09a-novel-skill-style-integration.md` | 风格档案、标注范例、写作任务风格注入机制 | 不定义世界观客观事实 |
| `10-roadmap-and-acceptance.md` | 实施路线图（阶段 0 到 5）、回归测试用例、MVP 验收标准 | 不更改既定模块归属 |
| `11-risks-and-decisions.md` | 核心决策备忘、技术风险防范、待决问题追踪 | 不成为运行时配置文件 |
| `12-environments-conflicts-and-missing-capabilities.md` | 使用环境边界、权限分级、环境冲突防护、关键缺失能力 | 不重复 API 字段定义 |
| `13-module-map-and-document-index.md` | 模块地图、依赖关系拓扑、问题查阅字典（本文档） | 不保存业务数据 |
| `14-future-scenarios-compatibility-and-evolution.md` | 动态数值账本、风格回滚评测、四级删除生命周期、向后兼容 | 不影响 v1 基础检索运行 |
| `14a-api-extension-notes.md` | 状态账本、评测反馈与生命周期接口扩展规范 | 不改变 v1 基础读写契约 |
| `15-model-management-and-llm-gateway.md` | 大模型统一网关、任务路由、容错修复、哈希缓存、成本记账、提示词注册表 | 不负责正文生成文学指导 |
| `16-current-implementation-status-and-boundaries.md` | `CURRENT` 现实校准：v1/v2/v3、CLI、存储、来源、模型和未完成能力的可用性边界 | 不替代各版本 wire schema，也不证明外部 Studio 的真实闭环 |
| `17-system-usage-runbook.md` | `CURRENT` 公开 CLI/API 入口、调用顺序、前置条件、停止条件和错误处理 | 不保存业务事实，也不替代外部工作区的计划或验收证据 |

---

## 5. 按开发与写作问题速查指南

| 遇到的具体问题 | 优先查阅文档 | 辅助查阅 |
|---|---|---|
| 原文修改了，不知道哪些数据需要重新计算 | `02`、`00` | `08`、`14` |
| AI 提取出了错误的主张，或者把猜测当成了原著真理 | `04`、`12` | `00`、`14` |
| 审核成百上千条 AI 提议太累，想简化流程 | `00`（第 3.2 节） | `04`、`12` |
| 两个项目同名角色串库了，或者 AU 设定泄露到了其他项目 | `05`、`12` | `09-api-contract` |
| 同人文写到后半截，原著后面已死角色的剧情被搜出来了 | `05`（分歧点 POD 机制） | `00`、`14` |
| 角色说出了当时还不该知道的秘密，导致情节 OOC | `06` | `09-api-contract` |
| 想给主角统计金币余额、消耗记录或战力境界变化 | `14`（第 3 节） | `03`、`14a` |
| 搜人名或修仙法宝名搜不出来，分词切碎了 | `08`（中文分词与专有词典） | `00`、`02` |
| 生成的小说文笔很烂，想换掉风格或回滚上一版风格 | `14`（第 4 节）、`09a` | `14a` |
| 数据库坏了，需要从纯文本文件完整复活知识库 | `00`（第 3.1 节）、`10` | `14` |
| 想安全删除某条错误的旧设定，担心引用报错 | `14`（第 5 节） | `14a` |
| 想查当前 v3 HTTP 入口及其真实边界 | `16`、`17` | `src/fxi/api/router_v3.py`、`tests/v3/test_api_v3.py`、`tests/v3/e2e/test_public_api_clean_room.py` |
| 想查 v3 CLI 命令或本地 runtime 生命周期 | `17`、`16` | `src/fxi/cli/commands_v3.py`、`src/fxi/cli/commands_runtime.py`、`src/fxi/runtime/`、`tests/test_runtime_local.py` |
| 准备编码实现 v1/v2 功能，找接口字段定义 | `09-api-contract`、`14a` | `16`、`17` |
| 想从文本恢复数据库或判断 rebuild 能否执行 | `00`、`10`（设计目标） | `16`、`dev-docs/08-current-implementation-and-operations.md`（当前条件边界） |
| 想切换大模型 API / 增加新模型 / 查调用花费 / 改抽取提示词 | [15-model-management-and-llm-gateway.md](15-model-management-and-llm-gateway.md) | `00`、`11` |
