# 07 分步实施路线图与可落地开发计划

---

## 1. 总体研发推进次序

研发推进严格遵守：**“先筑牢纯文本底层，再做结构化状态，接着做时空与防穿帮流控，然后实现账本与模型网关，最后打通 novel-Skill 联调与端到端闭环”**。

```text
Step 0: 环境准备、规范契约冻结与 novel-Skill 结构对齐
   ↓
Step 1: 纯文本第一公民底座与原文库 MVP (Text-First + jieba FTS5 + 一键全量重建)
   ↓
Step 2: 故事世界模型、命题四层、分歧点 POD 与语义场景 UUID (Entities + POD + Scene UUID)
   ↓
Step 3: 角色认知追踪、POV 视点防穿帮与 OOC 扫描器 (Knowledge + POV Filter + OOC)
   ↓
Step 4: 动态状态账本、三态基准锚点与统一模型网关 (State Ledger + Model Gateway)
   ↓
Step 5: 场景化上下文剪枝、本地 API/CLI 与 novel-Skill 全链路联调 (End-to-End Integration)
```

---

## 2. 阶段化任务包与交付清单

### 阶段 0：基础环境、环境契约与 novel-Skill 对齐（准备期）
- **开发目标**：搭建 Python 工程脚手架，冻结与 `novel-Skill` 的共享契约。
- **产出代码文件**：
  - `pyproject.toml`（配置 Python 3.11+, jieba, pydantic, fastapi, uvicorn, click 等）；
  - `src/fxi/core/config.py` 与 `src/fxi/core/exceptions.py`；
  - `src/fxi/core/types.py` 与 `src/fxi/core/constants.py`；
  - `config/config.yaml` 基础配置。
- **验证与验收标准**：
  - 执行 `pip install -e .` 安装成功；
  - 跑通 `pytest tests/unit/test_config.py`，配置参数读取正常。

---

### 阶段 1：纯文本第一公民与存储底座（MVP-0）
- **开发目标**：实现带 YAML Frontmatter 的 Markdown 读写、SQLite WAL 模式连接池、jieba 分词与 `kb rebuild` 一键重建。
- **产出代码文件**：
  - `src/fxi/storage/text_io.py`（原子写、UTF-8 读写）；
  - `src/fxi/storage/sqlite_client.py`（WAL 模式与事务管理）；
  - `src/fxi/index_retrieval/jieba_fts.py`（FTS5 中文全文索引）；
  - `src/fxi/index_retrieval/project_lexicon.py`（专有词典导出）；
  - `src/fxi/sources/importer.py` 与 `segmenter.py`（文本切片）；
  - `src/fxi/storage/rebuild.py`（**一键重建核心实现**）。
- **验证与验收标准**：
  - 导入一部 txt 小说，全文 FTS5 搜索修仙生僻词 100% 命中，返回毫秒级行号；
  - 手动删除 `data/manifest.sqlite`，执行重建命令后数据库 100% 满血恢复。

---

### 阶段 2：故事实体、四层主张、POD 分歧点与场景 UUID
- **开发目标**：实现人物卡实体、可信度模型、同人分歧点阻断与解耦物理章号的场景 UUID。
- **产出代码文件**：
  - `src/fxi/sources/scene_id.py`（确定性语义场景 UUID 生成器）；
  - `src/fxi/domain/entities.py`, `relations.py`, `events.py`；
  - `src/fxi/claims/models.py`, `triage.py`, `retcon.py`, `lifecycle.py`；
  - `src/fxi/timeline/dag.py` 与 `src/fxi/timeline/pod_filter.py`。
- **验证与验收标准**：
  - 多项目测试用例：项目 A、B 同名角色互不串库；
  - 同人分歧点用例：分歧点后原著动态死亡事件被阻断，静态法则被正确继承；
  - 合法吃书用例：登记 `retcon` 修正声明后，旧主张被安全替代。

---

### 阶段 3：角色认知、POV 视点防穿帮与自动 OOC 检查
- **开发目标**：实现时空区间继承（解决 Frame Problem）、视点盲区过滤与 OOC 报告。
- **产出代码文件**：
  - `src/fxi/timeline/intervals.py`（区间有效性校验）；
  - `src/fxi/character_knowledge/knowledge_tracker.py`；
  - `src/fxi/character_knowledge/pov_filter.py`（**POV 视点防穿帮物理过滤**）；
  - `src/fxi/character_knowledge/ooc_checker.py`（草稿扫描引擎）。
- **验证与验收标准**：
  - POV 盲区用例：以反派视点请求写作上下文，主角未公开底牌被 100% 物理剥离；
  - 防 OOC 用例：角色说出尚未获知的秘密时，系统准确定位行号并报错告警。

---

### 阶段 4：动态状态账本、游戏文与领主流专项引擎、统一大模型网关
- **开发目标**：实现三态数值账本、游戏属性公式与技能冷却校验、领地宏观经济结算、参数阶梯投影以及全库统一的 LLM 调用网关。
- **产出代码文件**：
  - `src/fxi/state_ledger/definitions.py`, `events.py`, `calculator.py`, `anchor.py`；
  - `src/fxi/game_engine/attributes.py`, `skills.py`, `projection.py`, `combat_verifier.py`, `passive_radar.py`；
  - `src/fxi/territory/resources.py`, `buildings.py`, `population.py`, `macro_tags.py`；
  - `src/fxi/materials_skills/skill_store.py`, `anti_patterns.py`, `distillation_receiver.py`；
  - `src/fxi/model_gateway/gateway.py`, `router.py`, `providers.py`, `repair.py`, `cache.py`, `cost_tracker.py`；
  - `config/models.yaml` 与外部提示词模板 `prompts/`。
- **验证与验收标准**：
  - 半途接入用例：第 50 章设定基准 3000 金币后买药消费 500，结余 2500，前 50 章显示 `UNMEASURED` 且不报透支错误；
  - 技能冷却与蓝耗用例：大招冷却中再次释放被规则引擎准确拦截，蓝量扣除 100% 准确；
  - 领地经济与标签用例：领地存粮告罄自动生成 `[粮饷危急]` 宏观标签，拒绝透支征兵；
  - 阶梯投影用例：百种技能的游戏主角在战斗场景中参数面板压缩至 450 Tokens 内，大纲遇剧毒环境时沉睡的抗毒被动被雷达精准召回；
  - 网关容错用例：模型返回带 markdown 与多余逗号的脏 JSON，自动修复后成功解析并强校验通过；
  - 缓存用例：相同请求二次调用耗时 < 5ms，费用为 0 元。

---

### 阶段 5：场景剪枝、本地 API/CLI 与 novel-Skill 联调
- **开发目标**：实现 2500~4000 tokens 场景化剪枝、本地 REST 服务与 CLI 工具，完成与 `novel-Skill` 的双向闭环。
- **产出代码文件**：
  - `src/fxi/index_retrieval/context_pruner.py` 与 `local_embedding.py`；
  - `src/fxi/api/server.py`, `router_v1.py`, `contracts.py`；
  - `src/fxi/cli/main.py`, `commands_project.py`, `commands_search.py`, `commands_ops.py`；
  - 自动化回归测试 `tests/regression/test_19_canonical_cases.py`。
- **验证与验收标准**：
  - 启动 `fxi-server`（端口 8765），在 `novel-Skill` 中发起一次实际写作测试，正确组装上下文并完成正文生成；
  - 生成的正文草稿 `draft.md` 与状态增量 `state-delta.json` 正确落盘进 `Fxi/projects/`；
  - 19 个长周期最小回归矩阵用例 100% 通过（`pytest tests/regression/` 全部 PASS）。
