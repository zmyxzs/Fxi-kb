# 08 当前实现手册、可用性边界与运维流程

本文是 Fxi 的 **as-built 文档**：描述当前工作区代码实际提供的能力、调用前置条件、失败状态和已知缺口。它补充而不替代 `dev-docs/01`～`07` 的设计文档；当设计蓝图与代码行为不一致时，以代码、测试和本文件的“当前实现”章节为准。

状态：CURRENT（as-built）；最近核对日期：2026-09-12。

核对依据：`src/fxi/`、`config/`、`pyproject.toml` 以及登记的 CLI 帮助核对命令。本文不把模型生成结果、数据库现有内容或设计目标当作已实现功能，也不据此宣称任何外部集成已验证。

## 1. 系统定位

Fxi 是一个本地优先的 Python 模块化单体，向上提供 Typer CLI 和 FastAPI HTTP API，向下使用 Markdown/YAML、来源版本对象和 SQLite。它负责知识库事实、来源证据、时空因果、动态状态、角色认知、检索和写作前审核；小说正文生成不是 Fxi 的内置职责，而是通过模型网关或外部写作系统协作完成。

当前实际形成的四条数据链：

```text
原始 txt/md
  └─ import ─> sources/<source_id>/ + objects/<source_id>/<version>/ + FTS5

作品 Markdown/YAML
  └─ rebuild ─> entities / phases / relations / FTS5（仅可安全重放的投影）

问题或写作场景
  └─ scope/version/POV/POD 过滤 ─> 事实、证据、状态和剪枝后的上下文

正文草稿
  └─ review ─> proposal ─> approval ─> commit ─> 不可变审计记录 + 正式投影
```

## 2. 安装、启动与最小配置

### 2.1 依赖与入口

项目要求 Python `>=3.10`。运行依赖、开发依赖和可安装脚本均登记在 [pyproject.toml](../pyproject.toml)：

```powershell
python -m pip install -e ".[dev]"
python -m fxi.cli.main --help
```

已核对的命令入口：

- `python -m fxi.cli.main --help`
- `python -m fxi.cli.main project --help`
- `python -m fxi.cli.main query --help`
- `python -m fxi.cli.main ops --help`

安装后也可以使用项目脚本 `kb`；两种入口调用的是同一个 `fxi.cli.main:app`。

### 2.2 配置文件与路径

默认配置文件是 `config/config.yaml`。当前默认值为：

| 配置项 | 当前默认值 | 作用 |
|---|---|---|
| `server.host` | `127.0.0.1` | 服务监听地址 |
| `server.port` | `8765` | 服务监听端口 |
| `storage.data_dir` | `data` | SQLite、缓存、备份和抽取状态 |
| `storage.projects_dir` | `projects` | 作品实体、章节、文风资产 |
| `storage.skills_dir` | `skills` | 正式技能包 |
| `storage.sources_dir` | `sources` | 原始来源、切片和不可变对象 |
| `storage.materials_dir` | `materials` | 候选包等创作素材 |
| `storage.sqlite_path` | `data/manifest.sqlite` | 主数据库 |
| `storage.cache_db_path` | `data/cache.sqlite` | 模型响应缓存 |
| `storage.jieba_custom_dict_path` | `data/project_lexicon.txt` | 项目专有分词词典 |
| `context.default_budget` | `3500` | 默认上下文预算 |
| `context.max_budget` | `4000` | 默认配置中的最大预算 |
| `context.vram_safe_limit_mb` | `2048` | 配置层硬件安全提示值 |

`FXI_WORKSPACE_ROOT` 可以覆盖工作区根目录；未设置时，代码按安装包位置推导项目根目录。配置加载会创建上述运行目录，但不会自动创建作品、来源或审批数据。

测试与静态检查的可丢弃缓存统一位于 `tests/.cache/`：pytest 为 `tests/.cache/pytest`，ruff 为 `tests/.cache/ruff`，mypy 为 `tests/.cache/mypy`；标准命令使用 Python `-B` 选项避免生成分散的字节码目录，既有字节码已归档到 `tests/.cache/pycache/working-tree`。这些缓存不属于知识库、来源或业务运行数据。

启动 API 的推荐方式：

```powershell
python -m uvicorn fxi.api.server:create_app --factory --host 127.0.0.1 --port 8765
```

`create_app()` 会初始化配置、SQLite 客户端、作品/来源注册表、CORS 和 v1/v2/v3 路由。`run_server()` 也提供同样的 Uvicorn 启动逻辑，但 `server.py` 没有独立的 `__main__` 命令分支。

### 2.3 API 认证配置

除 `/health` 外，v1/v2 路由都要求服务端 actor 凭据。凭据来自环境变量 `FXI_API_ACTORS_JSON`，值是“token 到 actor 描述”的 JSON 对象；token 只应通过环境变量或安全注入提供，不应写入仓库。

示例结构（仅使用占位 token）：

```powershell
$env:FXI_API_ACTORS_JSON = '{
  "<reader-token>": {
    "actor_id": "reader-1",
    "roles": ["reader"],
    "work_ids": ["<work-id>"]
  },
  "<writer-token>": {
    "actor_id": "writer-1",
    "roles": ["reader", "writer"],
    "work_ids": ["<work-id>"]
  }
}'
```

请求可以使用以下任一方式传递 token：

```text
X-Fxi-Actor-Token: <token>
Authorization: Bearer <token>
```

同时传递两个 header 且值不同会返回 `403 AUTH_FORBIDDEN`。缺少 token 返回 `401 AUTH_REQUIRED`；环境变量缺失或 JSON 格式错误返回 `503 AUTH_CONFIG_UNAVAILABLE`；actor 没有所需角色、作品不在授权范围或审批主体不匹配时返回 `403`。认证不会信任请求体中的 actor 或 work scope。

`FXI_CORS_ORIGINS` 可用逗号分隔覆盖默认的 `http://127.0.0.1,http://localhost`。CORS 不允许 credentials；代码虽然把 `X-Fxi-Human-Approval-Token` 列入允许 header，但当前认证与 v2 审批实际使用的是数据库中的 `approval_id` 和 actor 身份，并没有独立的人审批 token 校验器。

## 3. 物理目录与数据权威层级

### 3.1 当前目录

```text
config/
  config.yaml             # 运行路径、服务和上下文默认值
  models.yaml             # provider、模型和任务路由
  canonical.yaml          # 作品规范配置模板
data/
  manifest.sqlite         # 主 SQLite
  cache.sqlite            # LLM 响应缓存
  project_lexicon.txt     # 重建/导入后可更新的 jieba 词表
  backups/                # 快照与 clear-parsing 冷备
projects/<work_id>/
  work.json               # 可选的作品元数据
  entities/               # Markdown 实体卡与 relationships.yaml
  chapters/<chapter>/draft.md
  style/                  # 旧版/本地文风画像资产
skills/<skill-slug>/      # 正式技能目录
materials/
  candidates/             # 候选 JSON，不等于正式技能
  candidate_heads/        # 候选正式头指针
sources/<source_id>/
  source.yaml             # 来源清单、版本和章节统计
  raw.txt                 # 单文件导入时的兼容正文
  chapters/               # 目录导入后的兼容章节正文
  scenes/                 # 兼容场景切片
  objects/<source_id>/<version>/
    snapshot.yaml
    <document_id>/raw.bin
    <document_id>/normalized.txt
    <document_id>/metadata.yaml
```

### 3.2 权威性不是单一规则

“纯文本第一公民”只对当前重建器能够安全解释的作品实体和检索投影成立。当前实现的真实分层如下：

| 数据 | 当前读取/写入事实 | 能否由 `rebuild` 安全重放 |
|---|---|---|
| 来源 `raw.bin`、`normalized.txt`、`snapshot.yaml` | 由 `VersionedStore` 只追加保存，读取时校验哈希和长度 | 不由 `rebuild` 重建；是证据对象 |
| 实体 Markdown/YAML、`work.json` | 重建时扫描并写入实体、阶段、作品元数据 | 部分可以 |
| 场景切片与 `fts_scenes` | 从章节/来源场景文件生成 | 可以 |
| `causal_events`、`causal_links` | 当前由写入流程/抽取入库产生 | 当前不能安全重放 |
| `state_metrics`、`state_events`、`state_snapshots` | 动态状态账本和快照 | 当前不能安全重放 |
| `character_known_claims`、`chapter_continuity` | 认知与连续性投影 | 当前不能安全重放 |
| v2 snapshot/review/proposal/commit/approval | SQLite 审计记录，绑定不可变版本和哈希 | 不应被文本重建删除 |
| `cache.sqlite`、LLM 响应缓存 | 加速数据 | 可删除，丢失后重新调用模型 |

因此，不能把“SQLite 都是可丢弃缓存”作为当前生产操作规则。尤其不能在 v2 工作流或动态账本已有数据时，把 `kb rebuild` 当成无条件的全量恢复工具。

## 4. 从导入到可检索证据

### 4.1 导入

入口是 `project import`，底层为 `SourceImporter`：

```powershell
python -m fxi.cli.main project import <chapters-dir> --work-id <work-id> --title "<title>"
python -m fxi.cli.main project import <file.txt> --work-id <work-id> --title "<title>"
```

目录导入只接受 `.md` 和 `.txt` 文件。文件名必须包含章节序号，序号不能重复、不能缺失且最终必须从 `1` 连续排列；`--limit` 会在排序后截取前 N 章。内容以严格编码规则读取，规范化文本后创建不可变来源对象，随后生成兼容目录、场景切片和 FTS5 记录。

导入会把 `source_id` 与同名 `work_id` 写入 `work_sources`。v3 提供独立的 `source-bind`（提交来源绑定载荷）和 `source-attach`（由运行时托管外部文本文件/目录，并一次完成绑定与不可变快照）命令；跨作品复用来源时仍必须显式绑定，不能依赖目录名或 `_fanfic` 命名猜测。

### 4.2 不可变来源与证据引用

来源版本目录使用：

```text
sources/objects/<source_id>/<source_version>/
```

每个文档同时保存原始 bytes、规范 UTF-8 文本和 `metadata.yaml`。创建时记录 raw SHA-256、规范正文哈希、字符数、章节号、编码和规范化版本。读取时会重新计算哈希；对象不完整、清单不一致、路径越界、区间越界或哈希不匹配都会失败。

`EvidenceRef` 绑定 `(source_id, source_version, document_id, start_char, end_char, excerpt_hash)`。区间是半开区间 `[start_char, end_char)`；`EvidenceStore` 是唯一校验边界，不能以“当前来源目录中相似的正文”替换指定版本。

### 4.3 FTS5 检索的真实范围

`ChineseFTS` 使用 jieba 预分词后写入 SQLite FTS5 `fts_scenes`，支持：

- `rank`：按 BM25 相关度排序；
- `chronological`：按章节顺序排序，供原因/源头追溯；
- 多词先尝试 AND，结果不足时用 OR 补充；
- `work_id` 隔离和 `required_terms` 约束。

当前代码没有接入 embedding 模型、向量数据库、RRF 混合重排或本地向量索引。设计文档中出现的“可选 embedding/混合检索”属于演进目标，不是当前可调用能力。

当 `QueryEngine` 收到明确 `source_id + source_version` 时，会尝试从不可变来源回读场景正文；若只能找到旧 FTS snippet，结果会附带诊断而不会把 snippet 冒充成已验证正文。没有版本范围时，旧场景文件/FTS 结果仍可能作为召回提示。

## 5. 作品事实、POD、POV 与上下文

### 5.1 实体与动态状态

`EntityManager` 负责作品范围内的人物、物品、地点和阵营实体；实体卡中的 `phases` 会进入 `entity_phases`。技能树、角色技能、物品持有和关系分别落到对应表或 `relationships.yaml`。

动态账本由 `LedgerCalculator` 维护：

1. 在查询叙事序位前寻找最近的 `is_anchor=1` 基准锚点；
2. 从锚点值开始累加后续变动；
3. 没有锚点但有事件时，从零累加；
4. 没有任何事件时返回 `UNMEASURED`；
5. 查询点早于最早锚点时返回 `UNMEASURED`。

指标状态支持 `EXPLICIT`、`UNMEASURED`、`NOT_APPLICABLE`。状态快照是计算缓存，真正的变动来源是 `state_events`；同一提交/幂等键通过 `state_event_receipts` 防止重复写入和载荷冲突。

### 5.2 同人分歧点（POD）

`PODFilter` 的当前规则是：分歧点之前的事件保留；分歧点之后的动态事件过滤；明确标记为静态世界观的事件仍可保留。`CausalStatus` 有 `untouched`、`mutated`、`invalidated` 三态。

注意：POD 过滤依赖事件字段、作品范围和调用方提供的分歧序位。它不是对任意自然语言文本的自动事实判定；没有登记因果事件时，系统只能返回“没有可用事件证据”，不能凭空推导完整蝴蝶效应。

### 5.3 POV 与角色认知

`ContextPruner` 在 `mode=original_in_world` 时要求 `pov_character_id`。它按作品、来源版本、知识版本、叙事序位过滤主张和事件，并用 `POVFilter` 移除观察者尚不知道的秘密；参与角色的已知主张会作为 `character_knowledge` 返回。

上下文会加入来源版本、知识版本、POV、可见主张、POD 安全事件、有效同人变动和少量文风规则，然后按字符预算剪枝。当前 v2 预算字段允许 `1..10000`；风格上下文固定最多 3 条规则和 1 个范例，并在 `pruning_log` 中记录剪枝。

`completeness_status` 是重要的可用性信号：缺少 work/source/knowledge 版本、原世界模式缺 POV、风格选择与版本不一致，都会使结果成为 `INCOMPLETE`。调用方不得只检查 `assembled_context` 非空就把结果当作完整上下文。

## 6. CLI 完整目录

### 6.1 顶层快捷命令

| 命令 | 当前行为 |
|---|---|
| `rebuild` | `ops rebuild` 的顶层快捷别名 |
| `search` | `query find` 实现的顶层快捷别名 |
| `state` | 动态状态查询 |
| `ripple` | 蝴蝶效应预检 |
| `ask` | 自然语言拆解、证据召回和回答生成 |
| `timeline` | 因果事件按章节区间查询 |
| `relations` | 关系与张力查询 |
| `items` | 物品及持有流转查询 |
| `skills` | 角色技能/能力查询 |
| `import` | 导入来源并建立场景索引 |
| `extract` | 对前 N 章执行一次自动抽取，结果进入候选阶段 |
| `extract-all` | 分批、可恢复、可并发抽取，默认批大小 20、并发 4 |

查询族也可通过 `query` 进入；作品和抽取族通过 `project` 进入；运维命令通过 `ops` 进入。快捷命令与嵌套命令可能显示不同命令名，但共享实现。

v3 入口通过 `v3` 命令族提供版本化 HTTP 客户端操作，包括 `project`、`source-bind`、`source-attach`、`source-snapshot`、候选、评测、决策、晋升、写作审核/提议/批准/提交、投影重建、`context`、`query`、`health` 和 `readiness`。`v3 runtime` 提供本地服务的 `start`、`status`、`stop`；`start` 在服务已就绪时复用已有进程，`status` 不启动服务。

### 6.2 作品与抽取命令

| 命令 | 关键参数/事实 |
|---|---|
| `project list` | 列出 SQLite 中已登记的作品 |
| `project entities <work_id>` | 可用 `--category` 筛选 |
| `project abilities <work_id> <character>` | 可用 `--chapter` 查某一节点 |
| `project mutations <work_id>` | 可用 `--chapter`、`--character` 筛选 |
| `project add-mutation <work_id>` | 必须提供 `--chapter`、`--character`、`--target`、`--cause`；类型默认 `ability_grant` |
| `project effective-state <work_id> <character>` | 必须提供 `--chapter`，可用 `--base-work` |
| `project style <work_id>` | 读取 `projects/<work_id>/style/style_profile.yaml` |
| `project audit-style <work_id>` | 用 FTS 反查禁词和比喻，属于启发式审计，不是语义真实性证明 |
| `project ingest-candidates <work_id>` | 只允许通过审批的候选进入正式 Markdown/SQLite |
| `project clear-parsing <work_id>` | 先冷备，再删除指定作品的解析事实和实体卡；保留来源与文风资产，属于破坏性操作 |

`extract` 明确提示“候选包已保存，等待人工审批；未写入正式知识”。`extract-all` 支持 `--resume/--no-resume`、`--start`、`--limit`、`--provider`、`--model`、`--concurrency` 和 `--auto-ingest`；`--auto-ingest` 也只会尝试已审批候选，不能绕过审批。

### 6.3 检索与运维命令

```powershell
python -m fxi.cli.main query find "<query>" --work-id <work-id> --limit 5
python -m fxi.cli.main query state <work-id> <entity-id> <metric-id> --chapter 12
python -m fxi.cli.main query timeline --work-id <work-id> --start 1 --end 50
python -m fxi.cli.main query relations --work-id <work-id>
python -m fxi.cli.main query items --work-id <work-id> --history
python -m fxi.cli.main query skills --work-id <work-id>
python -m fxi.cli.main ops backup
python -m fxi.cli.main ops cost
```

v3 帮助核对命令：

```powershell
python -m fxi.cli.main v3 --help
python -m fxi.cli.main v3 source-bind --help
python -m fxi.cli.main v3 source-attach --help
python -m fxi.cli.main v3 runtime --help
```

`ops cost` 展示 `api_usage_logs` 汇总。当前网关用 `len(prompt)//2` 和 `len(output)//2` 估算 token，再按固定公式估算人民币成本；这不是 provider 返回的真实 usage 或账单。

## 7. HTTP API

### 7.1 通用错误格式

应用层将错误统一包装为：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "请求失败",
    "request_id": "...",
    "retryable": true,
    "details": {}
  }
}
```

`X-Request-ID` 会被原样用于关联；未提供时服务生成随机 request id。`422` 参数校验固定为 `INCOMPLETE_INPUT` 且不可重试；`409` 默认标记为可重试，但调用方仍需先重新读取版本、审批或哈希，不能盲重放不同载荷。未捕获异常返回 `500 INTERNAL_ERROR`，不泄露实现细节。

`GET /health` 只返回 `{"status":"ok","service":"fxi"}`，不检查 SQLite、来源对象、模型网关、认证配置或作品注册表。因此它是存活探针，不是“知识库全功能健康检查”。

### 7.2 v1：查询与兼容门面

所有 v1 路由都在 `/v1` 下，均为 `POST`：

| 路由 | 所需角色 | 主要用途 |
|---|---|---|
| `/v1/context/assemble` | `reader` | 场景上下文剪枝；预算 1000～8000 |
| `/v1/timeline/check-canon-compatibility` | `reader` | 检查同人沿用原著事件是否安全 |
| `/v1/timeline/ripple-impact` | `writer` | 登记/分析分歧点向下游的影响 |
| `/v1/state/query` | `reader` | 查询指定实体、指标和叙事序位的结算 |
| `/v1/checks/ooc` | `reviewer` | OOC、秘密早泄和声线审查；路由调用 LLM |
| `/v1/checks/continuity` | `reviewer` | 连续性审查；路由调用 LLM |
| `/v1/query/ask` | `reader` | 问题拆解、实体/事件/主张/场景证据聚合和回答 |
| `/v1/context/continuity` | `reader` | 章节结尾台账 |
| `/v1/context/voices` | `reader` | 角色声线档案 |
| `/v1/context/relationships` | `reader` | 人际关系和张力 |
| `/v1/context/style` | `reader` | 作品文风画像 |

v1 请求/响应字段以 [contracts.py](../src/fxi/api/contracts.py) 为准。v1 适合查询和兼容现有调用方；它没有 v2 那样完整的 source/version/hash/approval 提交约束，不能用它替代 v2 的不可变写入闭环。

### 7.3 v2：版本化写入闭环

v2 路由表：

| 路由 | 所需角色 | 作用与关键前置 |
|---|---|---|
| `POST /v2/sources/{source_id}/snapshots` | `writer` | 校验作品绑定、来源版本、章节范围和幂等键，创建不可变快照 |
| `GET /v2/sources/{source_id}/snapshots/{version}/documents/{document_id}` | `reader` | 校验清单、对象和文档哈希后返回正文 |
| `POST /v2/writing/context` | `reader` | 绑定 source version；可选 approved/evaluation candidate 风格 |
| `POST /v2/writing/review` | `reviewer` | 校验正文哈希、来源快照和知识版本，落库审核报告 |
| `GET /v2/style/packages/{version}?work_id=...` | `reader` | 读取作品范围内的风格候选/正式包 |
| `POST /v2/style/candidates` | `writer` | 校验 package hash，状态初始为 `EVALUATION_CANDIDATE` |
| `POST /v2/style/promotions` | `approver` | 校验候选、active version、审批记录和幂等键后晋升 |
| `POST /v2/approvals` | `approver` | 为 style promotion 或 chapter commit 创建 15 分钟有效审批 |
| `POST /v2/writing/proposals` | `writer` | 只接受同作品/来源/正文/章节且 `PASSED` 的 review |
| `POST /v2/writing/commits` | `writer` | 消费一次性审批，CAS 更新知识版本并写入正式投影 |

推荐调用顺序：

```text
source.yaml 当前 version
  → source snapshot
  → writing context（记录 source/knowledge/style version）
  → writing review（正文、plan、context hash）
  → chapter proposal（必须绑定 PASSED review）
  → approval（action=chapter_commit，15 分钟有效）
  → chapter commit（哈希 + 版本 + CAS + 幂等键）
```

提交校验包括：正文 `text_hash`、提议 `proposal_hash`、提交 `payload_hash`、来源版本、知识版本、风格正式版本、proposal/review/approval 的目标绑定、状态变更 ID 白名单、事件 ID 去重，以及知识版本 CAS。相同幂等键重放相同载荷会返回 `idempotent_replay=true`；重放不同载荷返回冲突。

### 7.4 v2 审核的实际限制

v2 `writing/review` 当前固定执行：

- `continuity`：确定性连续性检查，不调用 LLM；规则缺失或检查异常会进入 `INCOMPLETE`；
- `semantic_review`：若 `app.state.semantic_reviewer` 未注入，返回 `MODEL_REVIEWER_NOT_CONFIGURED` 和 `INCOMPLETE`；
- `sacred_whitelist`：检查白名单文本是否仍在正文中；
- `action_density`、`style`：当前明确返回 `*_CHECK_NOT_CONFIGURED` 和 `INCOMPLETE`；
- 未知检查 ID：返回 `UNSUPPORTED_REVIEW_CHECK` 和 `INCOMPLETE`。

即使请求没有列出 `semantic_review`，路由也会自动追加一次语义检查。因此在默认 `create_app()` 没有注入语义审查器的情况下，常规 v2 review 通常不能得到 `PASSED`；随后 proposal 会因为没有通过的 review 被拒绝。这是当前真实可用性限制，不是调用方参数错误。

v2 approval 的 `rollback` action 当前明确返回 `409 UNSUPPORTED_APPROVAL_ACTION`，不能据此启动回滚。回滚领域模块存在，但没有接入 v2 审批/提交 API。

### 7.5 v3：版本化公共门面

v3 路由由 `router_v3` 注册在 `/v3` 下；服务端同时保留 v1、v2 和 v3。v3 CLI 通过 HTTP 公共边界调用这些路由，不在 CLI 内复制服务层。当前公开操作覆盖能力查询、项目、来源绑定/附加/快照、候选与生命周期操作、上下文、查询、投影重建，以及 `/v3/health` 和 `/v3/readiness`。具体请求字段、角色、版本/hash/幂等约束以 v3 contracts 和运行时返回为准；本手册不将外部模型或 Studio 集成视为已验证。

## 8. 模型网关与生成质量边界

`ModelGateway` 负责任务路由、OpenAI-compatible chat completion、可选缓存、JSON 修复和调用审计。当前 `config/models.yaml` 登记了多个 provider 和任务路线；真实调用需要对应 `api_key_env` 环境变量。provider 的有限重试上限为 3，模型只返回 `reasoning_content` 而没有正文 `content` 时会显式失败。

当前 `BaseProvider.capabilities` 只有 `chat_completion`。以下接口虽然作为抽象方法存在，但调用会抛出 unsupported capability：

- `embedding`
- `vector_search`
- `rrf`
- `offline_queue`
- `failover`

`MockProvider` 只用于测试或显式离线模拟，返回固定的合法 JSON，不代表真实模型可用。OOC、连续性语义检查和 `ask` 的答案生成依赖真实 provider 或注入的测试桩；模型不可用时必须保留 `INCOMPLETE`/失败诊断，不能把 mock 或错误文本当成权威事实。

## 9. SQLite、事务与备份

### 9.1 主数据库

`DatabaseClient` 每次连接启用 SQLite WAL、外键和 `synchronous=NORMAL`；事务上下文正常退出提交，异常回滚并关闭连接。主表按功能分组：

- 作品与实体：`authors`、`works`、`entities`、`entity_phases`、`entity_relations`；
- 物品/能力/领地：`item_*`、`skills_tree`、`character_skills`、`territory_*`；
- 主张/证据/认知：`claim_*`、`claim_evidence`、`character_known_claims`、`retcon_declarations`；
- 因果/回档：`timelines`、`causal_events`、`causal_links`、`reversion_checkpoints`；
- 状态账本：`state_metrics`、`state_events`、`state_snapshots`、`state_event_receipts`；
- 检索/审计：`fts_scenes`、`api_usage_logs`、`source_chapters`、`style_*`；
- v2 工作流：`v2_source_snapshots`、`v2_reviews`、`v2_style_*`、`v2_approvals`、`v2_proposals`、`v2_commits`、`v2_work_heads`、`v2_projection_tasks` 及提交投影表。

现有数据库迁移是只增量的列补充和 `work_sources` 主键升级；没有通用降级器。变更 DDL 时必须同步 [05-data-schema-and-sqlite-ddl.md](05-data-schema-and-sqlite-ddl.md)、迁移逻辑和测试。

### 9.2 备份

```powershell
python -m fxi.cli.main ops backup
```

`BackupManager` 使用 SQLite `VACUUM INTO` 创建 `manifest.sqlite` 热快照，并复制 `projects/`、`skills/`、`sources/`、`materials/` 到 `data/backups/snapshot_<timestamp>_<nonce>/`。显式目标目录必须不存在；中途失败会尝试删除不完整快照。

备份完成不等于恢复验证完成。恢复演练至少需要：复制快照到隔离工作区、确认 SQLite 能初始化、读取来源对象、执行受限查询和对比关键哈希。不要把原工作区直接覆盖作为第一次恢复测试。

### 9.3 重建

```powershell
python -m fxi.cli.main ops rebuild
```

重建器当前只清理并重建 `entities`、`entity_phases`、`entity_relations`、`fts_scenes`；保留 `authors` 和 `works`，扫描实体卡、关系文件、章节草稿、来源场景和技能目录。

在任何清理前，它会检查以下“当前没有完整回放契约”的非空表：

```text
causal_events
causal_links
state_metrics
state_events
state_snapshots
character_known_claims
chapter_continuity
```

只要其中一张表有数据，重建返回 warning、数据库不清理，CLI 退出码为 1。若没有 blocker 但扫描/解析/词表导出产生 warning，同样不是成功。只有无 warning 才能把报告当作成功；不要只看 CLI 的“重建完成”文案或计数。

## 10. 当前可用性矩阵

| 能力 | 当前结论 | 需要什么才能真正使用 |
|---|---|---|
| CLI 帮助与命令发现 | 可用，已实际核对 | Python 依赖安装 |
| 来源导入 | 可用 | 合法 txt/md、连续章节号、可写 `sources/` |
| FTS5 中文检索 | 可用 | 已导入/已索引场景；只保证词法召回 |
| 作品实体/关系/物品/技能查询 | 可用但依赖数据 | 实体卡或正式 SQLite 数据 |
| 动态状态结算 | 可用 | 指标和事件已登记；无事件不是零而是 `UNMEASURED` |
| POD/蝴蝶效应 | 可用但不自动创造事实 | 已登记原著事件、分歧映射和同人事件 |
| POV 上下文 | 可用但可返回 `INCOMPLETE` | 作品、版本、知识账本、POV 和可见主张 |
| v1 OOC/连续性 | 条件可用 | 作品规则、前序台账和真实模型配置 |
| v1 `ask` | 条件可用 | FTS/结构化证据；答案阶段还需要模型 |
| v2 来源快照/文档 | 可用 | `work_sources` 显式绑定、版本和对象完整 |
| v2 风格候选/晋升 | 可用 | 候选 hash、评测引用、approver 审批和 active CAS |
| v2 review → proposal → commit | 默认不完整 | 注入 semantic reviewer，并提供全套版本/hash/审批 |
| embedding/向量/RRF | 当前不可用 | 尚无 adapter 或存储实现 |
| 离线排队/failover | 当前不可用 | provider 抽象明确抛出 unsupported |
| v2 rollback approval | 当前不可用 | API 明确拒绝 rollback action |
| `health` | 仅存活可用 | 不能证明数据、模型或认证健康 |
| 费用统计 | 可用作估算 | 只能解释为本地估算，不是供应商账单 |
| `rebuild` | 有条件可用 | 非回放投影必须为空，否则 fail closed |
| `backup` | 可用作文件快照 | 仍需隔离环境恢复演练 |

## 11. 失败诊断速查

| 现象 | 真实原因方向 | 处理 |
|---|---|---|
| `/health` 成功但业务请求失败 | health 不检查依赖 | 另外验证 token、作品注册、来源对象和模型 |
| `401 AUTH_REQUIRED` | 没有 actor header | 补 `X-Fxi-Actor-Token` 或 Bearer |
| `503 AUTH_CONFIG_UNAVAILABLE` | `FXI_API_ACTORS_JSON` 缺失/损坏 | 修复环境变量，避免写入仓库 |
| `404 WORK_NOT_FOUND` | `works` 没登记或 registry 不可用 | 先通过导入/初始化登记作品，区分 404 与 503 |
| `404 SOURCE_NOT_FOUND` | source 没有显式绑定到 work | 修复 `work_sources`，不要仅改目录名 |
| `409 VERSION_CONFLICT` | 来源/知识/style head 已变化 | 重新读取当前版本并重新生成绑定载荷 |
| `409 EVIDENCE_MISMATCH` | 对象、正文、清单或 hash 不一致 | 检查 source object 和调用方 hash，不要强行提交 |
| v2 review 是 `INCOMPLETE` | semantic reviewer 未注入、规则缺失或检查不支持 | 查看 `error_code`，补 reviewer/规则后重审 |
| proposal 无法创建 | review 不是同作品/来源/正文/章节的 `PASSED` | 不能复用另一版正文的报告 |
| commit 被拒绝 | approval 过期/已消费、head 变化、style 非正式或提议不匹配 | 重新走审批和 CAS 流程 |
| `ask` 返回“没有可验证证据” | FTS、实体、事件、状态或连续性均无命中 | 先导入/索引/入库，不能把回答当事实 |
| `rebuild` 拒绝且数据库未清理 | 存在非回放投影 | 先备份并制定投影回放方案；不要手动删除表规避守卫 |
| 模型调用失败 | provider 未配置 key、网络/超时、响应无 content 或 JSON 无法修复 | 检查 `models.yaml`、对应 key 和错误诊断 |

## 12. 维护与扩展规则

1. 新增 API 字段时，同时更新 `contracts.py`、路由行为、错误码、v1/v2 文档和测试；v2 写入字段必须说明绑定的版本/hash/幂等语义。
2. 新增持久化表或列时，更新 `MANIFEST_SCHEMA_DDL`、只增量迁移、`dev-docs/05` 和恢复说明；先判断该表是否具备完整 replay contract。
3. 修改来源规范化、场景切片或分词逻辑时，必须改变/登记版本，重新验证哈希和 FTS 结果；不要静默覆盖旧对象。
4. 任何检索回答都要区分“结构化/不可变证据”“FTS 召回提示”“模型综合文本”；模型综合不能升级证据等级。
5. 新增题材规则必须来自作品配置或知识库，不把特定作品实体写进通用模块；对白提及、背景遗物和现场出场要保持语义区分。
6. 变更 `rebuild` 前先说明新增表的权威来源、回放顺序、失败恢复和并发策略；没有回放契约就保持 fail closed。
7. 所有破坏性运维（尤其 `clear-parsing`）先运行 `ops backup`，在隔离副本验证，再对工作区执行。
8. 文档交付前运行 `git diff --check`；文档只描述已经能由代码、测试或可重复命令证明的行为，设计目标必须标注为“计划/未实现”。

## 13. 相关入口

- 设计总纲：[01-system-architecture.md](01-system-architecture.md)
- 目录与落盘：[02-directory-structure-and-storage-layout.md](02-directory-structure-and-storage-layout.md)
- 模块职责：[03-module-and-file-responsibilities.md](03-module-and-file-responsibilities.md)
- 数据契约与 DDL：[05-data-schema-and-sqlite-ddl.md](05-data-schema-and-sqlite-ddl.md)
- 测试规范：[06-engineering-standards-and-testing.md](06-engineering-standards-and-testing.md)
- 当前能力审计：[../docs/knowledge-base/16-current-implementation-status-and-boundaries.md](../docs/knowledge-base/16-current-implementation-status-and-boundaries.md)
- API 服务：[../src/fxi/api/server.py](../src/fxi/api/server.py)
- CLI 入口：[../src/fxi/cli/main.py](../src/fxi/cli/main.py)
