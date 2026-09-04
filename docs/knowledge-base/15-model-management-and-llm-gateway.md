# 15 模型管理与统一大模型网关（Model Gateway）

## 1. 架构定位与职责边界

### 1.1 明确系统边界：知识库 vs 外部写作项目
在整体技术布局中，必须严格划清系统职责边界：
- **外部写作项目（如 `novel-Skill` 或作者写作客户端）**：负责正文构思、大纲推演、行文起草、角色对话扩写以及调用大模型生成正文小说。这是**独立的外部消费端**。
- **当前系统（`Fxi`）是纯粹的知识库（Knowledge Base）**：负责为外部写作项目提供客观、可靠、经过验证的世界观事实、时空因果、动态状态账本、防 OOC 边界与素材检索支撑。

### 1.2 知识库内部为何必须需要“模型管理网关”
虽然知识库不负责“写小说”，但知识库内部有大量需要利用大模型能力的**数据理解、归纳与审查管道**：
1. **原始资料抽取（`claims`）**：从导入的 txt/md 原著或历史草稿中抽取实体、属性、事件与因果；
2. **逻辑与防 OOC 审核（`character-knowledge`）**：深度审查时间线一致性、角色知情区间与合法性；
3. **文风特征与反面规则提取（`materials-style`）**：分析参考文本中的语言学特征与写作反面教条（anti-patterns）；
4. **长篇分级滚动摘要（`document`）**：为导入的长篇资料自动生成“分卷大纲-章节梗概-场景切片”三级记忆链；
5. **文本向量嵌入（`index`）**：将切片与素材转化为向量。

如果缺乏统一的模型管理层，知识库的每个模块都会重复手写 HTTP 请求、重试、超时、密钥读取、Token 统计与 JSON 解析代码。一旦某个 API 供应商限流、下线或推出更便宜的新模型，全库代码都必须推倒翻改。

**统一大模型网关（`model-gateway`）的目标**：作为知识库内部的基础设施服务，提供“配置驱动、任务分级路由、自动容错重试、结构化修复与缓存记账”的统一调用管道。业务模块仅需一行代码调用 `gateway.complete(task="extraction", ...)`，底层细节完全透明。

---

## 2. 统一网关核心架构

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       知识库内部业务调用层 (零样板代码)                       │
│  • claims 实体主张抽取       • character-knowledge 防 OOC 审查               │
│  • materials 文风特征提取    • document 分卷/章节滚动摘要生成                  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 统一方法: gateway.complete(task, input, schema)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    知识库模型管理网关 (model-gateway)                         │
│                                                                             │
│  1. 提示词注册表 (Prompt Registry): 模板独立于代码，支持版本化与热更新            │
│  2. 调用哈希缓存 (LLM Cache): sha256(model + prompt + text) -> 0 成本命中    │
│  3. 任务分级路由器 (Task Router): 抽取用廉价快模型，审查用深度推理，向量用本地 GPU  │
│  4. 故障断路与备用切换 (Failover): 主力超时/429 自动无缝降级到备选模型            │
│  5. 结构化输出修复管道 (Output Repair): Pydantic 校验 + json_repair 容错     │
│  6. 审计与费用记账本 (Cost Ledger): 统计每次维护知识库的 Token 与人民币消耗       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ 统一抽象适配器 (Provider Adapters)
                                       ▼
┌───────────────────────────┬───────────────────────────┬─────────────────────┐
│    OpenAI 兼容协议适配     │      Google GenAI 适配    │    本地模型适配层    │
│ (DeepSeek / Qwen / Moon)  │   (Gemini 2.5 Flash/Pro)  │ (Ollama / RTX 2060) │
└───────────────────────────┴───────────────────────────┴─────────────────────┘
```

---

## 3. 任务驱动的分级模型路由（Task-Based Routing）

知识库内部不同的数据处理任务，对模型能力与调用成本的诉求截然不同。系统通过根目录的 `config/models.yaml` 进行声明式映射，业务代码绝不硬编码模型名称：

```yaml
# config/models.yaml - 知识库模型路由配置
providers:
  deepseek:
    type: openai_compatible
    base_url: "https://api.deepseek.com/v1"
    api_key_env: "DEEPSEEK_API_KEY"
    timeout_seconds: 60
    max_retries: 3

  gemini:
    type: google_genai
    api_key_env: "GEMINI_API_KEY"
    timeout_seconds: 45
    max_retries: 3

  local_gpu:
    type: local_embedding
    device: "cuda:0" # RTX 2060 6GB 显存红线保护
    model_path: "models/Qwen3-Embedding-0.6B"

# 知识库内部任务分级路由
task_routing:
  # 1. 实体与关系抽取：高频大量文本，要求便宜、并发好、格式遵守度高
  extraction:
    primary:
      provider: deepseek
      model: "deepseek-chat" # DeepSeek-V3
      temperature: 0.1
    fallback:
      provider: gemini
      model: "gemini-2.5-flash"
      temperature: 0.1

  # 2. 逻辑与防 OOC 校验：强逻辑推理，必须识破因果矛盾与时间穿越
  ooc_verification:
    primary:
      provider: deepseek
      model: "deepseek-reasoner" # DeepSeek-R1 (深度推理链)
      temperature: 0.0
    fallback:
      provider: gemini
      model: "gemini-2.5-pro"
      temperature: 0.0

  # 3. 文风与反面教条分析：要求对修辞手法、用词习惯、叙事节奏有敏锐的分析能力
  style_analysis:
    primary:
      provider: deepseek
      model: "deepseek-chat"
      temperature: 0.2
    fallback:
      provider: gemini
      model: "gemini-2.5-flash"

  # 4. 长篇滚动分卷/章节摘要：要求超长上下文窗口、超低成本
  summarization:
    primary:
      provider: gemini
      model: "gemini-2.5-flash"
      temperature: 0.2
    fallback:
      provider: deepseek
      model: "deepseek-chat"

  # 5. 语义向量化：保护隐私，断网可用，常驻本地
  embedding:
    primary:
      provider: local_gpu
      model: "Qwen3-Embedding-0.6B"
```

---

## 4. 结构化输出容错与自动修复管道（Structured Output Pipeline）

知识库入库严禁非结构化的散漫文本。实体卡、关系三元组、时间账本事件均需要高精度的 JSON。然而大语言模型常有输出缺陷（包含 markdown 代码标记、遗漏闭合括号、多余逗号等）。

网关内建**“三道防线”**结构化输出解析管道：

```text
模型原始字符串输出
       │
       ▼
【第一道防线：Markdown 围栏剥离与基础清洗】
正则提取 ```json ... ``` 块，过滤前后废话解释语
       │
       ▼
【第二道防线：json_repair 容错补全】
利用 json_repair 算法自动修补尾部逗号、未转义引号及括号缺失
       │
       ▼
【第三道防线：Pydantic Schema 强校验】
验证字段类型、枚举值范围与非空约束
       │
       ├─ 成功 ──► 交付业务层直接使用
       │
       └─ 失败 ──► 发起 1 次 1-Shot 自动修复重试（将报错原因与脏 JSON 发回模型修正）
```

业务调用层只需传入 Pydantic 模型类即可获得安全类型保障：
```python
# 业务代码调用示例：极其纯粹，零样板代码
extracted_claims = await gateway.extract(
    task="extraction",
    input_text=chapter_text,
    schema=ExtractedClaimsResponse
)
```

---

## 5. 调用哈希缓存与成本记账（LLM Cache & Cost Ledger）

### 5.1 响应哈希缓存（零费用复用）
在调整切片逻辑或重构知识库时，作者可能会反复执行分析。如果每次都调用付费 API，会造成大量不必要的开销。
- **缓存键计算**：
  $$ \text{CacheKey} = \text{SHA256}(\text{model} + \text{prompt\_template\_version} + \text{input\_text} + \text{temperature}) $$
- **缓存存储**：持久化保存在本地 SQLite `llm_cache` 表；
- **收益**：相同文本在未修改提示词时，再次执行抽取耗时从 8 秒降至 2 毫秒，API 成本为 0 元。支持 CLI 命令 `kb cache clear` 手动清除。

### 5.2 知识库维护成本记账本（Cost Ledger）
网关自动捕获每次调用的 `prompt_tokens` 与 `completion_tokens`，记录于 `manifest.sqlite` 的 `api_usage_logs` 表：
- 支持查询：`kb cost --project fanfic-a`；
- 输出详报：本月知识库导入分析共消耗多少 Token、折合人民币多少元、缓存命中率是多少。彻底打破使用成本的黑盒。

---

## 6. 提示词模板外部化（Prompt Registry）

**铁律：禁止在 Python 业务代码中硬编码长篇提示词字符串。**

所有提示词模板必须剥离到项目 `prompts/` 目录下，按任务分层管理：
```text
prompts/
├── extraction/
│   ├── entity_claims_v1.md
│   └── entity_claims_v2.md
├── ooc_check/
│   └── contradiction_scan_v1.md
├── style/
│   └── pattern_mining_v1.md
└── summary/
    ├── chapter_summary_v1.md
    └── volume_summary_v1.md
```

- **模板语法**：采用标准 Jinja2 模板格式；
- **热更新能力**：作者调优抽取或审查提示词时，只需用记事本修改对应 `.md` 文件，无需重启服务或触碰任何 Python 源代码；
- **可复现审计**：每次存入知识库的分析记录均记录所用的 `prompt_id` 与 `prompt_version`，确保多年后依然能回溯当时结论的推导背景。

---

## 7. 离线模式与优雅降级（Offline Graceful Degradation）

面对突发断网、云端服务商 503 故障或欠费封禁，知识库必须保证创作者本地工作的绝对连续性：

| 知识库功能 | 联网状态（正常模式） | 断网/API 故障状态（离线降级模式） |
|---|---|---|
| **jieba + FTS5 全文搜索** | 本地 CPU 处理（<10ms） | **100% 满血可用**（本地纯计算） |
| **动态状态账本重算** | 本地 CPU 处理（<5ms） | **100% 满血可用**（本地纯计算） |
| **向量检索与语义切片** | 本地 RTX 2060 GPU 加速 | **100% 满血可用**（Qwen3-Embedding 本地驻留） |
| **角色认知与 OOC 静态检查** | 基于本地 DAG 区间校验 | **100% 满血可用**（规则引擎纯本地） |
| **大模型深度抽取与摘要** | 正常调用云端 API | **自动排队进 `offline_tasks.queue`**，本地提示“网络不可用，已暂存待办”，恢复后后台静默补全 |

---

## 8. 与外部写作系统（novel-Skill）的交互契约

外部写作端与知识库交互时，**知识库的 Model Gateway 仅用于满足知识库自身的分析与检视需求**：
1. 外部写作系统向知识库发起 `POST /v1/context/assemble`（请求场景写作上下文）；
2. 知识库内部调用 FTS5、本地 Embedding 和状态账本，必要时调用网关生成章节摘要，完成上下文剪枝（控制在 2500~4000 tokens）；
3. 知识库将组装好的纯净上下文与负面约束清单通过 JSON 返回给外部写作端；
4. **外部写作端（如 novel-Skill）使用外部项目自己的模型配置和客户端去生成小说正文**，两者在进程、配置与代码实现上彻底解耦。
