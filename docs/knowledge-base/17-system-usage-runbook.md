# Fxi 知识库系统调用说明

> 状态：`CURRENT`
>
> 核对日期：`2026-09-09`
>
> 适用对象：需要代表用户调用 Fxi CLI/API 的自动化入口、Studio 适配器和维护者。
>
> 证据：`AGENTS.md`、`src/fxi/cli/main.py`、`src/fxi/cli/commands_v3.py`、`src/fxi/api/router_v3.py`、`docs/knowledge-base/16-current-implementation-status-and-boundaries.md`。

## 1. Fxi 的职责

Fxi 是来源、证据、候选、评测、决策、批准知识、版本、ContextView、审查、提案、提交和可重建投影的权威边界。

Fxi 不负责：

- 理解用户想写什么小说；
- 手工编写人物、剧情、章纲或正文；
- 把未审批候选直接当成正式事实；
- 替外部 Studio 决定叙事顺序和文风；
- 用 OAG/Wiki/FTS 投影反向覆盖权威知识。

调用者必须通过 Fxi 的公开 CLI/API 操作，不得直接写 SQLite、YAML 兼容表或内部服务对象。

## 2. 入口

```powershell
python -m fxi.cli.main --help
python -m fxi.cli.main v3 --help
```

### 2.1 v3 公开入口

```text
capabilities
project
source-bind
source-snapshot
candidate
evaluate
decide
promote
context
query
review
proposal
approve
commit
projection-rebuild
health
readiness
```

除 `capabilities`、`health`、`readiness` 外，v3 操作使用结构化 JSON 请求。每次调用都应传入或保存幂等键，并保留服务返回的请求 ID、trace ID、错误码、版本和 hash。

### 2.2 旧入口

顶层 `project import`、`extract`、`extract-all` 仍可能存在，但它们属于旧链路，不应作为 v3 资料重写流程的默认入口。章节目录导入还要求文件名含数字且编号连续，不能直接表达“索引、人物档案、设定百科、勘误”等资料类型。

若 v3 适配器或服务能力缺失，必须返回缺口并停止，不能通过旧入口或内部函数绕过证据和作用域约束。

## 3. 固定执行顺序

```text
确认 work_id/source_id/branch/version
  → 查询 capabilities/readiness
  → 建立或验证 source binding
  → 创建不可变 snapshot 和 evidence
  → 只产生候选 Candidate
  → Evaluation/duplicate/conflict/适用性
  → Decision/Approval
  → Promotion/KnowledgeVersion
  → ContextView 和投影
```

候选抽取数量、模型返回非空 JSON、FTS 命中或 OAG/Wiki 页面存在，都不能单独证明正式知识已经写入。

## 4. 能力与文风隔离

抽取能力必须由调用请求或项目策略显式声明。文档标题、文件名、目录名和正文中的“文风”“台词”“风格”等词不会自动开启 style、voice、fingerprint 或 few-shot。

适用于同人资料的能力可以拆成：

```text
fact bundle: entities, relationships, causal_events, abilities,
             continuity, character_knowledge
idea bundle: creative_seeds, plot_patterns, tropes, memes,
             adaptation_candidates
style bundle: style_profile, voice, exemplars
```

没有明确的 style 策略时，style bundle 必须保持关闭；关闭状态需要进入运行策略 hash，供恢复和审计校验。

## 5. 多来源和冲突

多个来源必须分别保存 source snapshot、版本和证据。相似内容可以建立聚类，但不能直接覆盖。应保留：

- canonical identity 与别名候选；
- `same_core`、`variant_of` 等关系；
- conflict set；
- canon、fanon、AU、unknown 或待人工裁决状态；
- 每个判断的证据和来源优先级。

priority 只是组合元数据，不等于自动判真。无法裁决时返回待处理状态，不把冲突文本送进正式 ContextView。

## 6. 来源路径和证据门禁

外部绝对路径不能直接假定为 Fxi 来源。受控适配器需要负责 staging、相对路径、文件数量/大小、编码、symlink/junction、内容 hash、文档类型和版本清单。

适配器未被真实验证时，不得手工复制文件后声称“已导入”。来源读取失败、hash 不匹配、版本过期和证据缺失必须显式返回错误。

## 7. 审批和提交边界

以下动作不能由普通查询或写作请求自动完成：

- Candidate 晋升为正式知识；
- 冲突事实的裁决；
- proposal approval；
- chapter commit 或状态提交；
- purge、restore、不可回放数据的 rebuild。

`INCOMPLETE`、`APPROVAL_REQUIRED`、`INVALID_SCOPE`、`CONFLICT`、`EVIDENCE_MISMATCH`、`CAPABILITY_UNSUPPORTED`、`UNKNOWN_OUTCOME` 等状态必须停止当前链路。超时提交先查回执，不可盲目重试。

## 8. OAG/Wiki 投影

OAG/Wiki/FTS/vector 都是 approved KnowledgeVersion 或 ContextView 的派生投影，只能重建、删除或标记过期，不能成为权威事实源。投影状态 `BUILT` 也不代表候选已经批准，更不代表 Studio 已完成写作提交。

## 9. 最小验收记录

每次操作至少保留：

1. 真实 CLI/API 命令和退出码/HTTP 状态；
2. work/source/branch/version 作用域；
3. snapshot、evidence、candidate、evaluation、decision、approval、promotion 或 commit ID；
4. 输入/输出 hash、幂等键和 trace/request ID；
5. 未实现能力、人工待确认项和错误摘要。

`health=ok` 只能证明进程存活；业务就绪还必须检查认证、作品/来源绑定、来源对象、版本、投影和模型依赖。

## 10. 与 Studio 的交接

Studio 负责学习编排、叙事计划、正文、审查协调和运行恢复；Fxi 只提供已批准的证据、ContextView、审查依赖和提交边界。Studio 的调用说明见 `D:/Code/novel-Studio/docs/system-usage-runbook.md`。
