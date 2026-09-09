# Fxi 知识库与创作底座

Fxi 是一个本地优先的 Python 知识库与动态世界状态中心，为长篇写作提供作品隔离、来源版本、结构化实体、时空因果、动态状态、角色认知、中文全文检索和写作审核接口。小说正文生成不是 Fxi 的内置职责；正文生成和部分语义审核通过统一模型网关或外部写作系统协作完成。

## 先确认当前状态

本项目同时保留设计蓝图和实际代码。判断“某功能是否真正能用”时，优先阅读 [当前实现手册](dev-docs/08-current-implementation-and-operations.md) 和 [当前实现状态与边界](docs/knowledge-base/16-current-implementation-status-and-boundaries.md)，再阅读设计专题。当前明确的边界包括：

- 已有 txt/md 导入、来源版本对象、jieba + SQLite FTS5 检索、结构化查询、部分 v1/v2 API 和备份能力；
- embedding、向量搜索、RRF、离线队列和自动 failover 当前没有可调用实现；
- 默认 v2 review 会因未注入 semantic reviewer 而返回 `INCOMPLETE`，不能把 review→proposal→commit 视为开箱即用；
- `/health` 只表示服务进程存活，不证明数据库、来源、认证或模型健康；
- `rebuild` 对尚无安全回放契约的投影表 fail closed；`ops cost` 是字符长度推算的内部估算，不是供应商账单。

## 快速开始

项目要求 Python `>=3.10`。在 PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m fxi.cli.main --help
```

安装后的项目脚本 `kb` 与 `python -m fxi.cli.main` 使用同一个 CLI 应用。首次使用前应准备合法的章节文件、作品 ID 和可写的运行目录；系统不会从空目录自动推导完整世界事实。

## CLI

常用入口：

```powershell
python -m fxi.cli.main project import <chapters-dir> --work-id <work-id> --title "<title>"
python -m fxi.cli.main query find "<query>" --work-id <work-id> --limit 5
python -m fxi.cli.main query timeline --work-id <work-id> --start 1 --end 50
python -m fxi.cli.main query ask "<question>" --work-id <work-id>
python -m fxi.cli.main ops backup
python -m fxi.cli.main ops rebuild
```

顶层还提供 `rebuild`、`search`、`state`、`ripple` 等快捷命令；`project` 下包含实体、能力、变动、导入、抽取和候选入库命令，`query` 下包含检索、状态、时间线、关系、物品和技能命令。执行 `--help` 查看当前参数，不要依据设计文档猜测尚未实现的选项。

重要语义：`query find` 是词法召回，不是向量语义搜索；`query ask` 的模型综合文本不能升级为来源证据；`extract` 产生候选，不等于正式知识写入；`extract-all --auto-ingest` 仍只尝试已审批候选。

## API

启动本地 FastAPI 服务：

```powershell
python -m uvicorn fxi.api.server:create_app --factory --host 127.0.0.1 --port 8765
```

默认地址是 `http://127.0.0.1:8765`。`GET /health` 只返回存活状态。除 `/health` 外，v1/v2 路由需要服务端配置 actor 凭据，并使用以下任一请求头传递 token：

```text
X-Fxi-Actor-Token: <token>
Authorization: Bearer <token>
```

API 路由按两层理解：v1 主要用于查询和兼容检查；v2 将来源版本、知识版本、正文哈希、审核、审批、幂等键和 CAS 提交绑定在一起。版本化写作提交应按“source snapshot → writing context → writing review → proposal → approval → commit”顺序执行，具体请求体以 [API 契约](docs/knowledge-base/09-api-contract.md) 和 [当前实现手册](dev-docs/08-current-implementation-and-operations.md) 为准。

## 配置与数据目录

默认配置位于 [config/config.yaml](config/config.yaml)，模型路由位于 [config/models.yaml](config/models.yaml)。关键默认路径为：

```text
data/manifest.sqlite       # 主数据库
data/cache.sqlite          # 模型响应缓存
projects/                  # 作品、实体、章节和文风资产
sources/                   # 来源、场景和不可变来源对象
skills/                    # 正式技能包
materials/                 # 候选素材等创作数据
```

可通过 `FXI_WORKSPACE_ROOT` 覆盖工作区根目录，通过 `FXI_CORS_ORIGINS` 覆盖 CORS 来源。API actor 凭据使用 `FXI_API_ACTORS_JSON` 注入，token 不得写入仓库；模型 provider 所需密钥按 `config/models.yaml` 中的 `api_key_env` 配置。

来源对象包含原始 bytes、规范化文本、元数据和哈希清单。FTS、部分实体投影和缓存属于派生层，但当前因果、状态、认知、连续性及 v2 审计数据不能被无条件删除后靠 `rebuild` 恢复。执行 `clear-parsing` 或其他破坏性运维前先运行 `ops backup`，并在隔离目录演练恢复。

## 文档导航

- [知识库规划与专题总索引](docs/knowledge-base/README.md)：领域模型、来源、POD/POV、检索、API、风险和演进设计。
- [当前实现状态与边界](docs/knowledge-base/16-current-implementation-status-and-boundaries.md)：面向使用者的可用性判断、冲突来源和验收清单。
- [开发文档总索引](dev-docs/README.md)：工程设计、模块职责、数据契约、测试和实施路线。
- [当前实现手册与运维流程](dev-docs/08-current-implementation-and-operations.md)：安装、认证、CLI/API、SQLite、备份、重建、故障排查和维护规则。
- [数据结构与 SQLite DDL](dev-docs/05-data-schema-and-sqlite-ddl.md)：持久化契约和迁移注意事项。
- [工程标准与测试](dev-docs/06-engineering-standards-and-testing.md)：错误处理、验证和回归测试要求。

文档维护时必须区分“已实现”“条件可用”“仅规划”和“不可用”，并在代码、契约、测试和当前实现手册之间同步变更。
