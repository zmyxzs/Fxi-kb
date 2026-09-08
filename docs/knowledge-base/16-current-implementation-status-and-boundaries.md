# 16 当前实现状态、功能可用性与边界

本文是知识库专题文档中的“现实校准层”。前面的专题主要说明目标模型与长期设计；本文只回答：当前代码是否真的提供了这项能力、调用前需要什么、返回结果能否被当作事实，以及哪些功能目前根本不能用。

## 1. 判断标准

结论按以下证据优先级排列：

1. 当前代码中的真实调用链和显式错误分支；
2. 可重复的 CLI/API/测试运行结果；
3. 配置文件和数据契约；
4. 设计文档中的目标或路线图。

“接口存在”“返回了非空 JSON”“页面显示成功”都不能单独证明功能可用。必须同时检查依赖数据、版本绑定、证据哈希、权限、模型配置和错误状态。

## 2. 一览结论

| 能力 | 当前状态 | 可以相信什么 |
|---|---|---|
| CLI 命令发现 | 可用 | Typer 帮助和参数定义已存在 |
| txt/md 导入 | 可用但有输入约束 | 规范化来源、版本对象、场景切片和 FTS 可生成 |
| 中文全文检索 | 可用 | jieba+SQLite FTS5 的词法召回和 BM25/章节排序 |
| 实体、关系、物品、技能查询 | 条件可用 | 已落盘且已同步的数据；没有数据不会自动推理 |
| 状态账本 | 条件可用 | 已登记指标/事件的确定性结算；无事件是 `UNMEASURED`，不是零 |
| POD/蝴蝶效应 | 条件可用 | 已登记事件和分歧映射的过滤/影响；不是任意文本的全自动推理 |
| POV 上下文 | 条件可用 | 已提供版本、知识和观察者范围后的可见事实；可能明确返回 `INCOMPLETE` |
| v1 审查和问答 | 条件可用 | 依赖作品数据、规则和模型；v1 不是提交审计闭环 |
| v2 来源快照/证据 | 可用 | 版本、清单、对象和文档哈希一致时的不可变正文 |
| v2 review→proposal→commit | 默认不完整 | 需要外部注入语义 reviewer、审批和完整版本/hash 绑定 |
| 风格候选和晋升 | 条件可用 | 候选 package hash、评测引用和一次性审批正确时可晋升 |
| embedding、向量搜索、RRF | 不可用 | 当前 provider 抽象显式抛出 unsupported |
| 离线队列、自动 failover | 不可用 | 当前没有可调用实现 |
| v2 rollback approval | 不可用 | API 明确拒绝 `rollback` action |
| `/health` | 仅存活检查 | 不能证明 SQLite、来源、模型或认证健康 |
| 费用统计 | 可用作内部估算 | 不是供应商实际 usage 或账单 |
| 无条件全量 rebuild | 不可用 | 非回放投影有数据时会 fail closed |

## 2.1 模块闭环矩阵

“闭环”要求同时具备：入口、权威状态、成功写入、失败可见、读取/消费和可重复验证。仅有 Python 类或 CLI 帮助不计为闭环。

| 模块 | 闭环结论 | 当前缺口/边界 | 可复用的权威边界 |
|---|---|---|---|
| `core` 配置、标识、canonical/hash | 基本闭环 | 题材词汇不能进入通用分支；作品词汇必须进配置/资产 | `validate_work_id/segment`、`canonical_json/sha256_hex` |
| `storage` SQLite、备份、版本对象、rebuild | 条件闭环 | 主库 schema/迁移可用；非回放表会阻止重建；恢复需 manifest 和隔离目录 | `DatabaseClient`、`BackupManager`、`VersionedStore` |
| `sources` import/segment/evidence | 条件闭环 | 正式证据必须有 source/version/hash；模型抽取只产候选 | `EvidenceStore.validate_ref`、`SourceImporter` |
| `materials/candidate/style` | 条件闭环 | 审批、评测引用、证据和 actor 绑定缺一即拒绝；旧调用方需迁移 | `CandidateStore`、`EvidenceStore`、`core` hash |
| `claims` 生命周期/retcon/triage | 条件可用 | 结构化记录可读写，但不是 v2 chapter commit 的替代审批链 | claim schema、生命周期状态机 |
| `domain` 实体/关系/物品/能力/变动 | 条件可用 | 必须已有记录；部分旧模块仍有 YAML 兼容投影 | `EntityManager`、`MutationLedger`、`CanonicalRegistry` |
| `state-ledger` | 条件闭环 | event → snapshot 可重算；无事件返回 `UNMEASURED`；不能从空目录推导事实 | `LedgerCalculator`、`state_event_receipts` |
| `timeline` DAG/POD/continuity | 条件闭环 | 事件/链接/连续性需显式登记；rollback 尚未接入 v2 approval | `CausalDAG`、`PODFilter`、`ContinuityManager` |
| `character-knowledge/OOC/POV` | 条件可用 | 依赖 knowledge claim、时间范围和 reviewer；无输入不产生全知结论 | `POVFilter`、knowledge tracker、OOC checker |
| `index/retrieval` | 词法问答闭环，语义未闭环 | 多能力计划、证据状态、timeline/source 隔离已落地；无 embedding/vector/RRF | `RetrievalPlanBuilder`、`ChineseFTS`、`QueryEngine`、`WorkRegistry.resolve` |
| `API` auth/registry/v1/v2 | v2 条件闭环；v1 兼容门面 | v2 默认 review 需 semantic reviewer；v1 不具备不可变提交约束 | actor auth、`WorkRegistry`、v2 contracts/router |
| `CLI` project/query/ops | 条件闭环 | import/query/backup/restore 可用；旧快捷入口不能绕过 scope/provenance | CLI → service/repository，不重复写 SQL |
| `model-gateway` | chat 闭环 | cache/repair/cost 可用；embedding、offline queue、failover 明确 unsupported | `ModelGateway`、`LLMCache`、provider capability errors |
| `territory/game-engine` | 条件可用 | 规则/默认值必须来自作品配置；没有输入数据不自动生成世界事实 | `load_work_config/select_work_section` |
| `tools/legacy` | 迁移工具非生产闭环 | legacy migration/continuity 已标记弃用；bootstrap 要求显式章节；旧清理入口仅转发 | `project clear-parsing`、`ContinuityManager` |

## 2.2 已处理与仍需关注的问题分类

| 类别 | 当前结论 | 证据/处置 |
|---|---|---|
| 通用硬编码 | 已清理主要高风险项 | query 分解不再默认伪造主体，不再比较作品专有词；题材 intent 改由 work config 提供 |
| 重复实现 | W4 已收敛主要 hash、DDL、upsert 和章节提交边界 | 主库 DDL 在 `DatabaseClient`；独立 cache DB 保留自身 schema；生产章节提交唯一入口是 `api.router_v2.commit_chapter`；`ChapterCommitService` 仅为兼容/测试适配，不参与生产路由 |
| 作用域/路径漏洞 | W1-W3 已加 fail-closed | work/source 校验、containment、版本 FTS、审批服务端 binding、隔离 restore |
| 跨存储一致性 | v2 主链有收据/幂等/CAS；旧 YAML 兼容路径仍需迁移 | 不把兼容投影当成事实源；失败保留诊断和恢复状态 |
| 遗弃/一次性代码 | 已加弃用和删除版本 | `clear_zhanshen_parsing`、`migrate_continuity_ledger`、`migrate_legacy_to_kb`；不再有隐式作品/路径驱动 |
| 文档冲突 | 已在总纲、来源、检索、网关和入口添加现实状态声明 | 目标能力必须以本文件和 as-built 运维手册为准 |

## 3. 已实现能力的真实前置条件

### 3.1 导入、索引和问答

`project import` 能读取单个 txt/md 或章节目录。章节目录的文件必须是 `.md`/`.txt`，文件名必须含章节号，且在选定范围内从 1 连续排列。导入结果同时包含：

- `sources/<source_id>/source.yaml`；
- 兼容正文/章节和 `scenes/*.md`；
- `sources/objects/<source_id>/<version>/` 下的不可变对象；
- SQLite `work_sources` 绑定；
- `fts_scenes` 检索记录。

`query find`/`search` 只做 jieba 预分词和 FTS5 MATCH。查询词会先按 AND 尝试，结果不足时再按 OR 补充；`rank` 是 BM25 排序，`chronological` 是章节排序。当前不存在向量生成或向量重排链路。

`query ask` 会做实体/关键词拆解、结构化数据读取、FTS 场景召回、POD/来源范围过滤，最后尝试通过 ModelGateway 生成答案。没有可验证证据时返回明确的无证据文本；答案生成模型失败时也返回不可用提示并记录诊断，不应将该文本当作事实。

### 3.1.1 能力驱动问答的实际边界

`QueryEngine` 先把规则、作品 `work.yaml` 配置和模型输出合并为多个 canonical capability 信号，再由 `RetrievalPlanBuilder` 生成只读计划。旧的 `decomposition.intent` 仍保留为主能力兼容投影，但不再决定唯一检索分支。

每次问答还返回 `retrieval_plan` 和 `evidence_blocks`。每个证据块都带 `status`、scope、provenance 和 diagnostics：`AVAILABLE` 只表示有可验证范围和来源的证据；`INCOMPLETE` 表示范围或前置条件未满足；`EMPTY` 表示当前能力没有命中；`LEGACY_HINT` 只允许定位提示，不能进入权威答案；`UNSUPPORTED` 必须显式暴露，不能静默降级。

因果查询支持 `timeline_id` 过滤，因果事件可绑定 `source_id/source_version`；未绑定来源版本的旧事件会标为 `LEGACY_HINT`。`ContextPruner` 仍是写作上下文、POV、knowledge/source version 和预算的唯一所有者，问答证据不能直接当作写作安全上下文。

### 3.2 状态、时间线和实体

状态账本以 `state_events` 为事实事件，以 `state_snapshots` 为按查询节点写入的计算结果。`LedgerCalculator` 使用最近基准锚点和后续 delta 结算，并带有 `EXPLICIT`/`UNMEASURED`/`NOT_APPLICABLE` 状态。`state_snapshots` 旧值不是不可变真理；需要重新计算时调用 `recompute_balance` 语义更清晰。

时间线、因果事件、关系、角色相态、物品持有、技能和领地等模块均有 Python 实现，但它们是否“有用”取决于相应记录是否已经登记。模块不会从一个空目录自动生成完整世界事实，也不会用模型回答替代缺失的事件/状态记录。

### 3.3 来源证据

`VersionedStore` 每份文档保存 `raw.bin`、`normalized.txt`、`metadata.yaml`；快照清单是 `snapshot.yaml`。读取会验证 raw hash、规范正文 hash、字符数、元数据和 `[start_char,end_char)` 区间。`EvidenceStore.validate_ref` 还会验证引用中的 `excerpt_hash` 和可选 quote。

因此以下两种结果不能混淆：

- `content_source=evidence_store`：正文来自指定不可变来源版本，具有哈希边界；
- `content_source=fts_snippet` 或 `unversioned_fts_hint`：只可作为检索提示，不能替代版本化证据；对应证据块必须是 `LEGACY_HINT` 或 `INCOMPLETE`。

## 4. 真正的冲突与错觉来源

### 4.1 “纯文本可重建”与当前数据库事实并不完全一致

设计文档把 SQLite 描述为可丢弃派生层；当前 `Rebuilder` 为避免静默丢失，已经只清理并重建实体、阶段、关系和 FTS，且遇到以下任一非空表就拒绝清理：

```text
causal_events
causal_links
state_metrics
state_events
state_snapshots
character_known_claims
chapter_continuity
```

v2 snapshot、review、proposal、approval、commit 也属于不可随意删除的审计记录。结论是：当前不存在“任意时刻删除 SQLite 后仅靠 `rebuild` 100% 恢复完整知识库”的保证。需要恢复时必须使用数据库热快照和来源/作品文本备份，并对投影回放能力逐表确认。

### 4.2 “支持 embedding”与当前检索实现冲突

设计文档和路线图提到 embedding、混合检索、本地向量索引；实际 `BaseProvider.capabilities` 只有 `chat_completion`，`embed`、`vector_search`、`rrf`、`enqueue_offline`、`failover` 都是显式 unsupported。`QueryEngine` 的真实召回入口也是 `ChineseFTS`。

在功能验收中，不能把 FTS 的命中数量、模型回答或配置中的 embedding 字样作为向量检索已上线的证据。

### 4.3 `/health` 与“系统可用”不是一回事

`GET /health` 固定返回 `status=ok` 和 `service=fxi`。它不访问 actor 凭据、不验证作品注册表、不读取来源对象、不调用模型，也不检查关键表是否存在或是否能重放。它只能作为进程存活探针；业务就绪检查必须另外验证认证、作品/source 绑定、版本对象、FTS、账本和模型。

### 4.4 v1 与 v2 不是同一份提交契约

v1 主要是兼容查询/检查门面，输入集中在 `work_id`、场景、问题、正文和章节等字段；v1 的 OOC/连续性接口直接请求 LLM 检查。

v2 把 source version、knowledge version、style version、正文 hash、计划/上下文 hash、审批和幂等键作为写入边界。v1 返回一个检查结果，不等于 v2 可以提交；要进入正式 commit 必须创建 v2 review、proposal 和审批记录。

### 4.5 v2 审核默认会被语义检查卡住

`/v2/writing/review` 即使调用方没有请求 `semantic_review`，也会自动追加该检查。默认 `create_app()` 没有注入 `app.state.semantic_reviewer` 时，结果为 `INCOMPLETE / MODEL_REVIEWER_NOT_CONFIGURED`。`action_density` 和 `style` 检查当前也固定为未配置；`rollback` 审批动作则直接返回 `UNSUPPORTED_APPROVAL_ACTION`。

这意味着当前默认应用实例适合验证契约和失败闭环，不足以直接完成“任意正文自动审核并提交”。必须显式注入语义 reviewer，并让它返回匹配正文 hash、合法 check id 和合法状态。

### 4.6 来源绑定不是由目录名推断

API registry 要求 `work_sources` 中存在明确的 `(work_id, source_id)` 绑定，并且会核对 source_dir/source_version。当前公开命令主要通过导入流程把同一 `source_id` 绑定到同名作品；没有独立的公开 source-binding API/CLI。跨作品或同人底座复用时，不能仅创建目录或使用 `_fanfic` 后缀期待 API 自动识别。

### 4.7 费用数字是估算值

网关把 prompt 和 completion 的字符长度除以 2 作为 token 估算，再按代码中的固定单价写入 `api_usage_logs`。provider response 的实际 usage 没有被作为账单真值采集。`ops cost` 可用于趋势和相对比较，不可用于供应商结算或精确成本审计。

## 5. 推荐的可用工作流

### 5.1 只做本地知识查询

```text
导入来源
  → 检查 source.yaml/version
  → query find / timeline / relations / items / skills
  → 需要自然语言时再 query ask
```

该路径不需要完整 v2 提交闭环，但仍需有结构化数据或可检索场景。问答输出必须保留 evidence/diagnostics，并把模型综合和 FTS 提示与不可变来源正文区分开。

### 5.2 进行版本化写作提交

```text
显式 work/source 绑定
  → POST source snapshot
  → POST writing/context
  → 注入 semantic reviewer 后 POST writing/review
  → review 必须 PASSED
  → POST writing/proposals
  → POST approvals(action=chapter_commit)
  → POST writing/commits
```

每一步都应保存返回的版本、hash、ID 和 `request_id`。重试时复用相同幂等键只能复用同一载荷；修改正文、版本或审批目标必须生成新幂等键并重新走绑定校验。

### 5.3 进行抽取入库

```text
project extract / extract-all
  → CandidateStore 的隔离候选
  → 人工审批/评测引用
  → project ingest-candidates
  → 重新检查实体、关系、事件、连续性和 FTS
```

抽取完成的计数不是正式知识写入证明。`extract` 明确停留在候选阶段；`extract-all --auto-ingest` 也只尝试已批准候选。

## 6. 验收清单

一次“功能真正可用”的验收至少应覆盖：

- CLI 四组帮助可启动，且没有把警告误读为业务成功；
- 导入一份小型连续章节集后，`source.yaml`、不可变对象、场景文件和 FTS 都能对应；
- 修改/损坏对象时，来源读取返回 `SOURCE_SNAPSHOT_UNAVAILABLE` 或 `EVIDENCE_MISMATCH`，而不是静默回读当前目录；
- 作品隔离测试能证明 A 作品查询不会读取 B 作品；
- POV 测试能证明未获知主张不会进入角色上下文；
- POD 测试能证明分歧点后的动态事件被过滤、静态世界观事件按规则保留；
- 状态无事件、锚点前、锚点后和幂等重放分别返回正确状态/值；
- v2 review 缺少 semantic reviewer 时明确 `INCOMPLETE`，不能伪造 `PASSED`；
- review、proposal、approval、commit 的 source/version/hash 不一致时返回冲突；
- approval 过期或已消费时不能再次提交；
- 主数据库含不可回放投影时 `rebuild` 不清理现有表；
- `health=ok` 不能替代以上业务检查；
- `ops cost` 的数字在报告中标为估算，不作为真实账单。

## 7. 权威文档与代码入口

- 当前实现与运维细节：[dev-docs/08-current-implementation-and-operations.md](../../dev-docs/08-current-implementation-and-operations.md)
- API 路由：[router_v1.py](../../src/fxi/api/router_v1.py)、[router_v2.py](../../src/fxi/api/router_v2.py)
- API 服务与 health：[server.py](../../src/fxi/api/server.py)
- 认证：[auth.py](../../src/fxi/api/auth.py)
- 来源对象：[versioned_store.py](../../src/fxi/storage/versioned_store.py)、[evidence_store.py](../../src/fxi/sources/evidence_store.py)
- FTS 与问答：[jieba_fts.py](../../src/fxi/index_retrieval/jieba_fts.py)、[query_engine.py](../../src/fxi/index_retrieval/query_engine.py)
- 重建与备份：[rebuild.py](../../src/fxi/storage/rebuild.py)、[backup.py](../../src/fxi/storage/backup.py)
- v2 契约：[contracts.py](../../src/fxi/api/contracts.py)

专题文档中关于 embedding、离线队列、全量可重建或自动 rollback 的描述，应标记为规划/演进内容，直到对应代码和验收测试实际落地。
