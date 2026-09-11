# 09 CLI、证据包与 novel-Skill 集成原则

> 状态：`CURRENT + PLANNED`
## 这一部分用来做什么

它定义外部调用方（如作者本地 CLI、自动化脚本或专门的写作技能 `novel-Skill`）与知识库之间的交互协议与职责边界：

- 当前查询属于哪个项目与世界线；
- 目标意图是查原文正文、故事世界状态、角色认知、动态账本余额还是创意素材；
- 查询应该截止到哪个草稿版本或故事因果节点；
- 限制上下文 Token 预算并指定场景类型（战斗/对白/过渡）以实现自动化上下文剪枝。

---

## 核心职责划分铁律

```text
知识库（Fxi，本项目）职责：
  保存版本、提供证据、维护状态账本、隔离世界线、执行防 OOC 检查与上下文剪枝
  统一拥有 provider 配置、模型路由、密钥池轮换、冷却、缓存与成本记录
  同时服务内部数据管道，并通过受 writer/admin 鉴权的模型代理供外部写作客户端调用

外部写作系统（如 novel-Skill，其他项目）职责：
  作为独立的外部调用方，负责小说正文起草、情节推演、行文编排与小说文本生成
  拥有提示词、预算、请求记录与输出校验；默认通过 Fxi REST 代理调用模型，不复制 provider/密钥轮换逻辑
```

> **只读保护与架构解耦**：
> 1. `novel-Skill` 在写作生成循环中默认仅具备 `query` 只读权限，绝对禁止直接向知识库正式表中插入未经确认的数据；新增设定必须通过结构化 `propose` 提议接口进入待审队列。
> 2. `POST /v1/models/health` 与 `POST /v1/models/chat` 只授予模型代理能力，要求 `writer` 或 `admin` actor，不授予知识库 `commit` 权限。正文生成逻辑仍在外部项目运行，但 provider/key/rotation 状态由 Fxi 单例网关统一拥有。
> 3. 外部调用方的 actor token 只从自身环境变量（Studio 默认 `FXI_API_TOKEN`）读取；provider API key 只存在 Fxi 的进程环境或工作区 `.env`。Fxi 请求失败必须显式暴露，禁止自动回退到外部项目的直连模型。

---

## 目标 CLI 入口（规划中）

```bash
kb project list
kb import <path> --project project_A --mode index-only
kb search <query> --project project_A --type canon-content --scene combat
kb state <entity> --project project_A --as-of chapter_03 --metrics gold,realm
kb knowledge <character> --project project_A --as-of chapter_03
kb check-ooc <draft-path> --project project_A --as-of chapter_03
kb material search <query> --project project_A
kb retrieve <query> --project project_A --type canon-fact --budget 3500 --format json
kb claim list --project project_A --status disputed
kb proposal review <proposal-id> --decision accept
kb rebuild --all
kb export <target> --project project_A
```

---

## 查询请求上下文规范

```yaml
query: "角色拔剑迎战，法力全开"
project_id: "fanfic-a"
worldline_id: "canon-au-1"
query_type: "world-state"           # canon-content | canon-fact | world-state | character-knowledge | ooc-check | material
as_of_revision: 12
story_time: "chapter-15-scene-2"
scene_type: "combat"                # combat | dialogue | transition | exposition
context_budget: 3500                # Token 预算
entity_ids: ["character-lin_dong"]
source_ids: ["canon-original-01"]
limit: 8
```

`query_type` 必须明确声明。缺失 `project_id` 时，系统直接返回 `PROJECT_SCOPE_REQUIRED`。

---

## 精炼证据包（Context Evidence Pack）返回规范

```yaml
query: "..."
project_id: "fanfic-a"
scope:
  applied_worldline: "canon-au-1"
  post_divergence_filtered: true    # 已自动过滤分歧点后原著动态事实
evidence:
  - source_id: "canon-original-01"
    source_version_id: "sv-hash-001"
    locator: "vol-2/chapter-12/lines-140-155"
    quote_or_text: "..."
    relevance: 0.92
entities:
  - id: "character-lin_dong"
    active_attributes:
      cultivation_realm: "元丹境大圆满"
state_ledger_balances:
  "character-lin_dong":
    gold: 120
    hp_status: "left_shoulder_injured"
character_knowledge:
  - character: "character-lin_dong"
    secret: "prop-king-murder"
    state: "unknown"                 # 明确标示该角色当前不知道该秘密
conflicts: []
warnings: []
trace_id: "trace-2026-09-04-001"
```

系统拒绝返回没有出处凭证的自然语言生成幻觉，返回内容必须能够被调用方分拆组装进不同的 Prompt 插槽。

---

## 错误契约约定

- `PROJECT_SCOPE_REQUIRED`：缺少项目作用域；
- `PROJECT_NOT_FOUND`：项目不存在；
- `DIVERGENCE_CONFLICT`：查询命中已被分歧点阻断的原著动态情节；
- `TOKEN_BUDGET_EXCEEDED`：检索召回内容超出预算限制；
- `INDEX_OUT_OF_DATE`：索引过期需重建；
- `NO_EVIDENCE`：资料库内无依据，不可编造；
- `CONFLICTING_ASSERTIONS`：存在未决冲突断言；
- `KNOWLEDGE_STATE_UNKNOWN`：无法断定角色当时的认知；
- `OOC_DETECTED`：草稿扫描触发人设崩塌警报。

---

## 相关专题

- 详细 API 字段与 HTTP 映射：[09-api-contract.md](09-api-contract.md)
- 风格档案协作机制：[09a-novel-skill-style-integration.md](09a-novel-skill-style-integration.md)
- 动态状态与生命周期扩展：[14a-api-extension-notes.md](14a-api-extension-notes.md)
