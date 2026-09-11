# 04 主张、证据、身份与认识状态

> 状态：`PLANNED`
## 这一部分用来做什么

它负责回答：

- 这条信息从哪里来、证据是什么；
- 是原文明确、角色相信、读者猜测、粉丝考据还是项目私有 AU 设定；
- 这条信息是否可能是谎言、误解或已被后文揭秘推翻；
- 作者中途主动修改世界底牌（合法吃书 Retcon）时系统如何正确处理；
- 冲突主张如何在不互相覆盖的前提下并存与追溯。

> 它不保存人物、物品、能力等实体清单；那些实体对象属于 [03-story-world-model.md](03-story-world-model.md)。

---

## 四层认识论模型（Epistemic Model）

```text
proposition (归一化命题：关于世界的客观断言，如“艾琳与七公主是同一人”)
  └── assertion (某来源或项目提出的具体陈述，带适用范围与有效时间)
        ├── judgement (人工、规则或某次 AI 对断言的支持/反驳/不确定判断)
        └── evidence (原文片段 chunk 定位、作者笔记或其他出处凭证)
```

- **proposition**：去重规范化后的抽象命题；
- **assertion**：某次具体的陈述（可来自于原著第 3 章某角色的台词、同人文的 AU 设定或读者的考据）；
- **judgement**：某次分析给出的结论标签与置信度；
- **evidence**：不可更改的原文切片与位置凭证。

---

## 来源性质（status）与真假状态（truth_status）解耦

系统严禁使用单一布尔值 `is_true`，必须区分两条轴：

### 1. 来源性质（`status`）
- `canon_explicit`：原著正文明确写出；
- `canon_inferred`：根据原著多处伏笔线索推导得出；
- `character_belief`：角色主观相信，但在故事宇宙中不一定客观成立；
- `reader_guess`：读者或考据派推测；
- `fanon`：同人圈通用约定俗成二次设定；
- `project_defined`：当前项目明确指定的独有设定；
- `retconned`：被作者通过合法追溯性修正（Retcon）推翻的前设；
- `disputed`：存在多方矛盾争议，尚未盖棺定论；
- `superseded`：已被后文官方填坑或项目新设定覆盖替代；
- `retracted`：已撤回或被推翻的旧假设。

### 2. 世界客观真假（`truth_status`）
- `true`：在当前世界线中客观成立；
- `false`：客观不成立；
- `in_story_lie`：故事内部某个角色的谎言欺骗；
- `unknown`：当前信息不足，保持存疑。

---

## 作者合法“吃书”（Retcon 追溯性修正事件）

长篇创作中，作者经常在写到后期时为了重大反转或修复大纲，主动推翻前期设定（例如：前期设定“主角母亲病故”，第 80 章推翻为“母亲实为神宗圣女，假死隐居”）。

传统知识库会将其无情判定为“前后矛盾的严重逻辑 Bug”。本系统为此引入 **Retcon 事件机制**：

```yaml
event_id: "retcon_mother_identity_ch80"
event_type: "retcon_declaration"
effective_scene: "scene_ch80_sacred_temple"
overturned_claims: ["claim-mother-deceased-illness"]
new_claim: "claim-mother-saintess-alive"
author_note: "作者主动大纲修订：推翻前文病故陈述，确立圣女假死主张"
preserve_historical_text: true
```

### 处理规则：
1. **旧文本保持不可变**：前 79 章正文文本原样保留，历史模式下可查“当时主角认知中母亲已死”；
2. **后文真理平滑切换**：自第 80 章起，检索系统向写作端提供“母亲为在世圣女”的最新事实；
3. **消除虚假冲突报警**：检查引擎识别到 `retcon_declaration` 后，自动压制该处逻辑冲突报警，判定为合法的创作性反转。

---

## 角色谎言与误解的保留机制

“角色 X 对角色 Y 说主角已经阵亡”是一个客观发生过的**对话行为（`dialogue`）**：
- 该陈述记录为 `assertion`，其 `truth_status: in_story_lie`；
- 关联 `speaker_id: X`、`audience_id: Y`、`speaker_knowledge_state: knows_protagonist_alive`；
- 关联后文揭示事件 `reveal_event: event_protagonist_returns`。

这样系统既能检索出“X 曾对 Y 说过主角已死”（用于分析角色心机与动机），又不会误把主角状态判定为死亡（用于战力与行动检查）。

---

## 提议流控与审核防疲劳（Triage）

面对长篇小说的大量分析输出，系统采用分流处理：
- **低风险引用自动标记**：由确定性文本解析出的原文事实引用（如“第 5 章角色 A 对角色 B 说了某话”），在置信度高且不冲突时，直接进入 `auto_accepted` 状态，免除人工逐条点击确认；
- **高危冲突阻塞审核**：涉及修改已有角色关系、推翻原著事件、新增 AU 规则的提议，必须保留在 `pending_review` 队列，等待作者在 CLI 中集中审批。

---

## 四级虚拟墓碑与软删除（Tombstone Lifecycle）

对旧主张与错误分析的操作分为四级，绝不执行底层硬删除：
- `exclude`：主张依然有效，仅在当前写作查询中排除；
- `disable / archive`：建立虚拟墓碑，关联关系降为 `dormant`（休眠），保证旧文回溯不崩；
- `supersede`：建立 `superseded_by: new_claim_id` 关系，历史追溯可查；
- `purge`：彻底物理清理（必须强制执行影响分析，确认无任何活跃引用与快照依赖）。
