# 知识库与 novel-Skill 的文笔风格协作机制

## 结论

建议知识库保存文笔风格相关的**可复用知识、正负向范例与风格档案**，由 `novel-Skill` 在实际写作时调用。这样比把所有风格分析写死在提示词中更容易复用、版本化、评测与回滚。

职责边界绝对明确：
```text
知识库：保存、版本化和召回风格知识、正向范例、负向禁写范例（Anti-Pattern）与试用沙盒
novel-Skill：理解当前写作任务、按场景类型选择资料、组装 Prompt 上下文、生成与反思
```

---

## 知识库应该保存什么

### 1. 通用写作知识
- 视角、时态、叙事距离；
- 节奏控制、场景推进、信息释放；
- 对话潜台词、动作白描、心理与环境烘托；
- 冲突升级、伏笔铺设与回收技巧。

### 2. 风格档案（Style Profile）与沙盒试用（Canary）
- 描述某部作品、某位大师或项目专属的语言特征（如短句偏好、情绪克制、冷幽默）；
- **沙盒试用机制（Canary Period）**：新提取或修改的风格档案默认处于 `canary` 状态，仅在作者测试时召回；经 3 次以上正向评分（`rating >= 4`）后才晋升为 `active` 正式档案，彻底防范“学废了污染全库”。

### 3. 正向标注范例与负向禁写范例（Anti-Pattern）
- **正向范例（Positive Exemplar）**：可定位的经典片段（如“克制的告别场景”），说明其优秀句式与节奏；
- **负向禁写范例（Negative Anti-Pattern）**：作者最反感的糟糕段落（如油腻网络流行语、机械复读“倒吸凉气”、浮夸堆砌）。评测表明，**负向约束对大语言模型的抑制效果远超单向正向提示**。

---

## novel-Skill 调用风格上下文规范

`novel-Skill` 发起请求时，必须带上当前场景类型：

```json
{
  "project_id": "fanfic-a",
  "scene_uuid": "scene_ch03_negotiation",
  "query": "角色刚隐瞒身份，第一人称，克制紧张的对话场景",
  "scene_type": "dialogue",
  "needs": ["voice", "dialogue_subtext", "anti_patterns"],
  "style_profile": "project-default",
  "max_results": 5
}
```

知识库返回带出处的风格包：

```json
{
  "style_profile": {
    "id": "project-default",
    "version": 3,
    "status": "active",
    "rules": ["情绪优先通过动作和停顿表现", "避免心理独白直接解释情绪"]
  },
  "positive_exemplars": [
    {
      "source_id": "user-example-04",
      "location": "vol-1/chapter-2/lines 45-52",
      "tags": ["克制", "停顿", "潜台词"],
      "text": "他放下了茶盏，瓷盖与碗沿碰出一声脆响，却没再看她一眼..."
    }
  ],
  "negative_anti_patterns": [
    {
      "pattern_id": "anti-cringe-01",
      "rule": "严禁出现现代口语梗或连续使用'嘴角泛起冷笑'",
      "bad_example": "林动嘴角泛起一丝冷笑：'无语子，真下头。'"
    }
  ]
}
```

---

## 生成后的反馈回流与一键回滚

```text
风格检索 → novel-Skill 生成正文 → 作者审阅打分
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
     好评 (Rating 4~5)                     差评 (Rating 1~2)
            │                                     │
    候选范例转正入库                     1. 标记当前范例为 anti_pattern
                                         2. 一键执行 kb style rollback
                                         3. 恢复上一版本风格档案
```

- **风格回滚命令**：`kb style rollback --profile-id default`，一秒切断劣质规则生效，杜绝大模型近亲繁殖与文风恶化。
