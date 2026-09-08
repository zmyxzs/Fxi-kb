# 知识库 API 契约 v1

## 1. 定位

本文定义知识库对 `novel-Skill`、CLI 和未来本地扩展工具暴露的稳定接口规范。当前核心实现以本地 Python API 和 CLI 优先；本地 HTTP API 作为轻量适配层，不改变底层的领域语义与流控逻辑。

API 是薄边界：所有校验、作用域隔离、分歧点过滤、POV 视线盲区与权限规则归属于领域模块，严禁在 CLI、HTTP 和 `novel-Skill` 中各自复制一份。

---

## 2. 调用者与权限分级

| 调用者 | 默认权限 | 允许调用的接口类别 |
|---|---|---|
| `novel-Skill` | `query` | 只读检索、获取场景上下文（带 POV 过滤）、读取防 OOC 报告、查询状态账本余额 |
| `novel-Skill`（可选提议） | `propose` | 提交 AI 分析抽取结果、生成上下文快照、候选主张提议 |
| `novel-Studio` 模型调用 | `writer` | 调用受鉴权的模型健康预检与 chat 代理；不能据此提交知识库事实或章节 |
| 用户 CLI / 交互式管理 | `query`、`propose`、`commit` | 查询、提议、人工审核批准（accept/reject）、四级生命周期标记、合法 Retcon 声明 |
| 运维管理命令 | `admin` | 来源导入、纯文本一键全量重建（rebuild）、数据库备份与恢复 |

> **安全底线**：严禁任何普通写作请求或生成流程执行 `commit`、`purge`（物理清理）、`migrate` 或全库重置。

---

## 3. 请求通用上下文格式

所有涉及故事内容、实体与草稿的接口均必须提供或接收以下上下文：

```json
{
  "project_id": "fanfic-a",
  "work_id": "work-1",
  "world_id": "canon-au-1",
  "timeline_id": "main-line",
  "as_of_revision": 12,
  "scene_uuid": "scene_ch15_cliff_01",
  "story_time": "chapter-15-scene-2",
  "pov_character_id": "character-villain",
  "scene_type": "combat",
  "context_budget": 3500,
  "request_id": "req-client-generated-uuid"
}
```

`project_id` 缺失时：
- 仅允许查询全局公开写作技法与通用素材；
- 涉及人物、能力、关系、事件、草稿和数值的查询坚决拒绝执行，返回 `SCOPE_REQUIRED`。

---

## 4. 核心资源与接口定义

### 4.1 项目、分歧点与草稿分支
```text
GET  /v1/projects
POST /v1/projects
GET  /v1/projects/{project_id}
GET  /v1/projects/{project_id}/overlays
POST /v1/projects/{project_id}/overlays
POST /v1/projects/{project_id}/draft-branches
POST /v1/projects/{project_id}/draft-branches/{branch_id}/merge
```

### 4.2 来源与版本
```text
POST /v1/sources
GET  /v1/sources/{source_id}
GET  /v1/sources/{source_id}/revisions
POST /v1/sources/{source_id}/revisions
GET  /v1/sources/{source_id}/segments
```

### 4.3 检索与场景化上下文打包（含 POV 盲区过滤）
```text
POST /v1/retrieval/query
POST /v1/retrieval/context-pack
GET  /v1/retrieval/explain/{request_id}
```

检索请求示例（支持场景化剪枝与 POV 过滤）：
```json
{
  "query": "反派追兵在绝壁前搜索痕迹",
  "scope": {
    "project_id": "fanfic-a",
    "world_id": "canon-au-1",
    "include": ["canon", "project", "character_knowledge", "state_ledger", "style"],
    "exclude": ["other_projects", "unreviewed_proposals"]
  },
  "filters": {
    "pov_character_id": "character-villain",
    "scene_type": "combat",
    "context_budget": 3500,
    "require_evidence": true
  },
  "mode": "hybrid",
  "top_k": 8
}
```

返回精炼证据包（自动屏蔽反派不知晓的主角藏匿信息）：
```json
{
  "request_id": "req-123",
  "scene_pruning": {
    "applied_scene": "combat",
    "pov_filtered": "character-villain",
    "budget_tokens": 3500,
    "used_tokens": 2180
  },
  "results": [
    {
      "result_id": "seg-456",
      "kind": "evidence_segment",
      "text": "山崖狂风凛冽，石壁上隐约有踩踏划痕...",
      "retrieval": {"method": "fts+embedding", "score": 0.89}
    }
  ]
}
```

### 4.4 故事对象、伏笔与合法吃书（Retcon）
```text
GET  /v1/entities/{entity_id}
GET  /v1/claims/{claim_id}
GET  /v1/foreshadowing?status=planted
POST /v1/foreshadowing
POST /v1/retcon/declarations
```

声明合法 Retcon 示例：
```json
{
  "project_id": "fanfic-a",
  "effective_scene_uuid": "scene_ch80_temple",
  "overturned_claim_id": "claim-mother-deceased",
  "new_claim": "主角母亲为圣女假死隐居",
  "author_note": "作者大纲主动修订"
}
```

### 4.5 提议流控与审核（防疲劳机制）
```text
POST /v1/proposals
GET  /v1/proposals?status=pending_review
POST /v1/proposals/{proposal_id}/decision
```

### 4.6 动态状态账本（三态数值与基线锚点）
```text
POST /v1/state/query
GET  /v1/state/ledger
GET  /v1/state/snapshots/{snapshot_id}
POST /v1/state/anchors
POST /v1/checks/state
```

### 4.7 风格反馈、评测与一键回滚
```text
POST /v1/evaluations
POST /v1/style-feedback
POST /v1/style-profiles/{profile_id}/rollback
```

### 4.8 资源四级生命周期操作（防误删）
```text
GET  /v1/resources/{resource_id}/impact
POST /v1/resources/{resource_id}/lifecycle
```

### 4.9 索引维护与容灾重建
```text
GET  /v1/index/status
POST /v1/index/rebuild
POST /v1/backups
POST /v1/restore
GET  /v1/health
```

### 4.10 受鉴权模型代理

```text
POST /v1/models/health
POST /v1/models/chat
```

两条接口都要求 `writer` 或 `admin` actor。请求只接受路由选择和生成参数，不接受 API key：

- `task_type`：任务路由名，默认 `scene_drafting`；
- `provider_override`、`model_override`：可选的显式 provider/model 覆盖；
- `chat` 另接收 `system_prompt`、`user_prompt`、`temperature`、`max_tokens`，且两个 prompt 至少一个非空；
- 未声明字段会被拒绝，供应商密钥只能由 Fxi 进程环境或工作区 `.env` 提供。

`health` 返回 `{"healthy": true|false}`，只检查路由与凭据，不发起付费生成；`chat` 成功返回 `{"content": "..."}`。Fxi 进程通过 `app.state.model_gateway` 复用单例网关，因此同一进程内的 Studio 请求共享 provider 密钥池轮询游标和冷却状态。供应商请求失败统一返回 `502 MODEL_GATEWAY_ERROR`，空输出返回 `502 MODEL_GATEWAY_INVALID_OUTPUT`，响应不回显密钥、供应商错误正文或完整 prompt。

---

## 5. CLI 命令对应关系

```bash
# 项目与草稿分支
kb project list
kb project create --id fanfic-a --base canon-01 --diverge-event event_01
kb draft branch create --name branch-30-B
kb draft branch merge branch-30-B

# 检索、伏笔与 POV
kb search <query> --project fanfic-a --pov villain --scene combat --budget 3500
kb foreshadowing list --status planted
kb retcon declare --overturn claim-12 --new "圣女假死" --reason "大纲修订"

# 动态账本与基线锚点
kb state anchor --project fanfic-a --char hero --metric gold --value 3000 --scene ch50
kb state query --project fanfic-a --char hero --metrics gold,realm --as-of ch50

# 风格回滚与生命周期
kb style feedback --profile-id default --score 2 --note "对白生硬"
kb style rollback --profile-id default
kb resource lifecycle <resource-id> --action disable

# 纯文本容灾一键重建
kb rebuild --all
kb backup create
```

---

## 6. 统一错误契约

核心错误码表：
- `SCOPE_REQUIRED`：缺少项目或世界线作用域；
- `DIVERGENCE_CONFLICT`：查询命中已被分歧点阻断的原著动态情节；
- `POV_VIOLATION`：尝试在视角盲区中提取未知秘密；
- `REVIEW_REQUIRED`：未审核的高危设定禁止直接使用；
- `OOC_DETECTED`：草稿检测到严重人设崩塌（非 Retcon 声明区域）；
- `PURGE_BLOCKED`：存在依赖该节点的活跃引用，物理删除被拦截；
- `STALE_SNAPSHOT`：前置事件变更，派生快照需重新计算；
- `TOKEN_BUDGET_EXCEEDED`：检索召回内容超出预算限制；
- `MODEL_GATEWAY_ERROR`：模型供应商请求失败，HTTP 502；
- `MODEL_GATEWAY_INVALID_OUTPUT`：模型供应商返回空内容，HTTP 502。
