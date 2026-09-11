# API 扩展备注：动态状态、风格反馈与生命周期

> 状态：`PLANNED`
本文是 [09-api-contract.md](09-api-contract.md) 的补充，记录后期场景需要的接口，不改变现有 v1 的基本读写边界。

## 动态状态、基准锚点与统计

```text
POST /v1/state/query
GET  /v1/state/ledger
POST /v1/state/anchor
GET  /v1/state/snapshots/{snapshot_id}
POST /v1/checks/state
```

请求至少包含：

```json
{
  "project_id": "fanfic-a",
  "entity_id": "character-hero",
  "story_time": "chapter-03-end",
  "scene_uuid": "sc-9b8c7d6e",
  "pov_character_id": "character-villain",
  "metrics": ["gold", "power"],
  "timeline_id": "main-line",
  "explain": true
}
```

返回值必须体现三态数值模型（`EXPLICIT` / `UNMEASURED` / `NOT_APPLICABLE`）：
- 未统计历史区间返回 `status: "UNMEASURED"`，不报透支错误；
- 登记基准锚点（`POST /v1/state/anchor`）后，从该时间点向后合法结算精确值（`EXPLICIT`）；
- 必须说明来源、规则版本和计算依据。余额或战力不能通过普通 API 直接覆盖，变动必须记录为不可变事件或待审核提议。

## 合法吃书与追溯性修正 (Retcon)

```text
POST /v1/retcon/declare
GET  /v1/retcon/history
```

请求载荷声明作者主动吃书意图：
```json
{
  "project_id": "project-main",
  "superseded_claim_id": "claim-linggen-01",
  "new_claim_id": "claim-linggen-god-02",
  "effective_narrative_order": 50,
  "author_note": "第50章正式揭秘主角灵根为九品神级，旧文第一章为伪装",
  "suppress_ooc_warnings": true
}
```
声明后，OOC 扫描引擎放行冲突并不再报警，同时在上下文装配中自动附带吃书圆场过渡建议。

## 伏笔与暗线生命周期跟踪

```text
POST /v1/foreshadowing/query
POST /v1/foreshadowing/link
```

支持按角色、状态（`planted`/`hinted`/`resolved`/`abandoned`）查询待回收的伏笔种子，并在正文完结或回收时关联解决场景的 `scene_uuid`。

## 草稿分支管理与走向试写

```text
POST /v1/draft-branches/create
GET  /v1/draft-branches/{branch_id}/diff
POST /v1/draft-branches/{branch_id}/merge
```

支持在独立分支沙箱推演角色生死与情节分支，其内部事件和主张与主线完全隔离，验证成功后可选择性合入。

## 视点防穿帮检索过滤 (POV Filter)

在所有检索接口（`POST /v1/retrieval/query`、`POST /v1/context/assemble`）中：
- 传入 `pov_character_id`（如 `char_villain`）；
- 服务端强制校验观察者在当前叙事节点的知情范围；
- 凡主角底牌或未公开线索，在组装进入 Prompt 前物理裁剪剔除，严防视角越权。

## 风格反馈和评测

```text
POST /v1/evaluations
GET  /v1/evaluations/{evaluation_id}
POST /v1/style-feedback
POST /v1/style-profiles/{profile_id}/lifecycle
```

反馈至少关联：

- 生成上下文快照；
- 风格档案 revision；
- 使用的模型和提示词版本；
- 用户评分、标签和备注；
- 支持将劣化风格规则一键置为 `disable` 或将优化规则发布为 `canary` 分流版本。

反馈默认不会自动改变风格档案，升级为正式规则必须走 proposal/commit 流程。

## 生命周期和影响分析

```text
GET  /v1/resources/{resource_id}/impact
POST /v1/resources/{resource_id}/lifecycle
```

生命周期操作包括：

```text
exclude   不参与当前查询
disable   暂停使用但保留历史
supersede 被新版本替代
purge     永久删除
```

执行 `purge` 前必须生成影响分析，明确处理主张、项目、提议、范例和索引引用。普通写作调用不能执行 `purge`。

## 兼容性和能力发现

```text
GET /v1/capabilities
GET /v1/schema
GET /v1/migrations
GET /v1/snapshots/{snapshot_id}
```

调用方可以发现当前服务是否支持状态账本、风格评测、embedding 版本并存和某种资料类型，而不必猜测版本。未知字段和未知类型应尽量保留，不能静默丢弃。
