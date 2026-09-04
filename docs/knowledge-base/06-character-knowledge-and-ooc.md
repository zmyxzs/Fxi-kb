# 06 角色认知、POV 视线盲区与 OOC 检查

## 这一部分用来做什么

它专门解决同人写作与长篇小说中最致命的问题：

> 在当前章节、当前场景中，某个角色**应该知道什么、实际相信什么、可以使用什么能力**？新写的草稿是否让角色未卜先知，提前泄露了秘密？切换到反派视点时，模型是否意外获知了主角的绝密底牌？

知识库必须将**世界客观真相、角色主观认知、读者阅读视角、作者后台全知**四者彻底分离。

---

## 认识状态模型（knowledge_state）

每条角色认知记录严格绑定：

```yaml
state_id: "ks-erin-secret-01"
character_id: "character:erin"
proposition_id: "prop-king-assassination-truth" # 关于某事件或身份的命题
project_id: "fanfic-a"
worldline_id: "canon-au-1"

# 时空有效区间 (解决状态爆炸的关键)
valid_from: "event_overhear_conversation_chap8"
valid_to: null                                  # 直到被新事件推翻前永久有效

# 认知状态
state: "suspected"                              # known | suspected | believed_false | unknown | hidden | forgotten | misled
acquired_by: "overheard"                        # witnessed | told_by | read | inferred | overheard | memory | reveal_event
evidence_ids: ["chunk-312"]                     # 偷听发生的场景切片定位
confidence: 0.7
```

### 状态枚举语义：
- `known`：已通过确凿途径得知该事实；
- `suspected`：心存怀疑，但尚未证实；
- `believed_false`：持有相反信念（被谎言欺骗或认知偏见）；
- `unknown`：完全不知情；
- `hidden`：该秘密客观存在，但对该角色处于屏蔽状态；
- `forgotten`：曾经知道，但因伤病/时光遗忘；
- `misled`：被虚假线索误导，坚信错误解释。

---

## 防状态爆炸：区间有效性与惯性继承

### 拒绝全量按章笛卡尔积快照
如果按 `50个角色 × 3000条主张 × 200个章节` 去显式存储认知状态，数据库会产生高达 3000 万行的冗余稀疏记录。

### 惯性继承（Frame Problem Solution）
系统采用**事件驱动的区间有效性**：
1. **变化才记录**：只有当发生认知改变事件（如“亲眼目睹”、“得知消息”）时，才写入一条带有 `valid_from: event_id` 的记录；
2. **惯性顺延**：在后续所有章节中，该认知状态自动顺延生效，直到出现下一个显式变更事件（如“记忆被抹去”或“发现真相”）；
3. **动态回溯**：写作检查时，系统仅需沿着当前时间点向上寻找最近一次生效的认知记录。

---

## 叙述视角盲区过滤（POV Observer Filter）

创作长篇小说经常切换叙事视点（Point of View, POV）：
- 第 10 章是**主角 POV**（主角在密林树洞里藏匿了重伤同伴和绝密钥匙）；
- 第 11 章切换为**反派追兵 POV**。

### 泄密致命伤
如果检索系统在第 11 章向 `novel-Skill` 提供上下文时，把树洞藏人的客观事实无差别返回，大模型在写反派心理活动时就会写出“反派心知肚明树洞有异样”的超感官泄密对白。

### POV 视线过滤规则
调用检索接口时传入 `pov_character_id: "character:villain"`：
1. **盲区屏蔽**：系统自动比对认知表，凡在该角色眼中处于 `unknown` 或 `hidden` 的命题，**一律从生成上下文中物理剔除**；
2. **误导注入**：如果该角色处于 `misled` 状态，系统优先向上下文注入其深信不疑的**错误线索**（如反派深信主角已经跳崖远逃），辅助 AI 写出精彩的视点误导与戏剧张力。

---

## 双重时空轴：客观因果 vs 叙事信息释放

```text
客观故事时空 (story_time) ────────► 决定角色“能不能做、知不知道”（防 OOC）
叙事阅读顺序 (narrative_order) ───► 决定读者“看到没有、何时揭秘”（防剧透/控伏笔）
```

- **插叙/倒叙场景**：当前章节写主角回忆 10 年前的情景，系统的 `story_time` 自动回溯至 10 年前的节点，角色的 `knowledge_state` 和可用能力自动退回年轻状态，而不会误用 10 年后的技能；
- **视角控制**：通过 `narrative_order` 检查伏笔是否提前对读者泄密，实现不可靠叙述与反转设计。

---

## 自动 OOC 检查规则矩阵与 Retcon 豁免

一致性检查引擎（`checks` 模块）在扫描作者新章节草稿时，自动比对以下规则：

| 检查项 | 规则逻辑 | 严重等级 |
|---|---|---|
| **秘密早泄** | 角色在 `reveal_event` 发生前，在对白或内心独白中使用了未获知秘密 | `HIGH (Error)` |
| **途径不成立** | 角色声称知道某事，但既未亲历、未被告知、也无法合理解释推断线索 | `MEDIUM (Warning)` |
| **能力超前/穿越**| 角色使用了尚未领悟、等级不足、已废弃或被封印的技能（联动 `state-ledger`）| `HIGH (Error)` |
| **物品凭空出现**| 角色使用了不在持有列表、或已被掠夺/摧毁的法宝装备 | `HIGH (Error)` |
| **时空不可能** | 角色在不可能的时间跨度内跨越了地理上无法到达的距离 | `MEDIUM (Warning)` |
| **情感关系突变**| 角色在没有经历缓释事件的情况下，突然对死敌产生无理由信任 | `LOW (Note)` |
| **AU 设定越界** | 引用了被当前项目 overlay 明确推翻的原著后续事件 | `HIGH (Error)` |

> **Retcon 合法吃书豁免**：若某处设定反转已被标记为作者的 `retcon_declaration`（如圣女假死），检查引擎自动放行，判定为合法的戏剧性反转，绝不报虚假 Error 干扰作者创作。

---

## 检查报告契约（输出规范）

```json
{
  "check_id": "chk-draft-ch15-001",
  "project_id": "fanfic-a",
  "scene_ref": "draft-01/chapter-15/scene-02",
  "pov_character": "character:villain",
  "status": "issues_found",
  "issues": [
    {
      "severity": "high",
      "type": "premature_knowledge_leak",
      "subject": "character:protagonist",
      "message": "主角在对白第 18 行直接称呼艾琳为'七公主'，但该身份揭示事件（event_reveal_royal）预计在第 20 章才发生",
      "conflicting_assertions": ["prop-erin-identity-princess"],
      "evidence_refs": ["ks-protagonist-erin-identity"],
      "suggested_fix": "修改称呼为'流浪商人'或提前插入身份怀疑线索"
    }
  ]
}
```