# 02 物理目录结构与存储落盘规范

> 状态：`PLANNED`

---

## 1. Fxi 根目录完整物理布局

为了同时承载系统源码、知识库加速索引、原著资料、外部 `novel-Skill` 沉淀的学习成果以及正文小说作品，`d:\Code\Fxi` 的物理目录树统一规划如下：

```text
d:\Code\Fxi\
├── .env                              # 本地环境变量 (API 密钥等，git 忽略)
├── .env.example                      # 环境变量示例
├── .gitignore                        # Git 忽略配置
├── pyproject.toml                    # Python 项目配置与依赖说明
├── README.md                         # 项目主入口说明
│
├── src\fxi\                          # 知识库核心 Python 源代码
│   ├── __init__.py
│   ├── core\                         # 基础配置、异常树、通用类型
│   ├── storage\                      # 纯文本 IO、SQLite 客户端、一键重建
│   ├── sources\                      # 原始底本导入、切片、场景 UUID 生成
│   ├── domain\                       # 实体、关系、事件故事对象
│   ├── claims\                       # 四层命题模型、Triage 分流、Retcon 吃书
│   ├── timeline\                     # 时空因果 DAG、区间继承、叙事顺序
│   ├── character_knowledge\          # 角色认知追踪、POV 视点防穿帮
│   ├── state_ledger\                 # 动态状态账本 (三态数值、基准锚点)
│   ├── materials_skills\             # 素材与技能沉淀管理
│   ├── index_retrieval\              # jieba FTS5、Qwen3 向量、场景剪枝
│   ├── model_gateway\                # 统一大模型网关、容错修复、哈希缓存
│   ├── api\                          # 本地 JSON REST API 服务
│   └── cli\                          # kb 命令行接口
│
├── data\                             # 衍生数据与加速索引目录 (可彻底删除并全量重建)
│   ├── manifest.sqlite               # 核心关系加速索引库 (实体/主张/账本/因果/FTS5)
│   ├── cache.sqlite                  # 大模型调用哈希响应缓存库 (LLM Cache)
│   ├── project_lexicon.txt           # 动态热导出的中文小说专有分词词典
│   └── embeddings\                   # 本地轻量级向量索引持久化目录
│
├── projects\                         # 写作作品仓库 (Text-First 单真理源)
│   └── <work-id>\                    # 具体作品目录 (如 chusheng, fanfic-a)
│       ├── work.json                 # 作品元数据定义
│       ├── state\                    # 故事全局状态
│       │   └── story-state.json      # 故事全局当前状态投影 (与 novel-Skill 兼容)
│       ├── entities\                 # 结构化实体定义 (Markdown + YAML Frontmatter)
│       │   ├── characters\           # 人物卡 (如 lin_dong.md)
│       │   ├── items\                # 物品与法宝 (如 stone_talisman.md)
│       │   └── locations\            # 地理与宗门
│       ├── game_system\              # 【游戏文专项】数值、技能树与属性面板
│       │   ├── classes.yaml          # 职业设定与晋阶树 (初级刺客 -> 暗影刺客)
│       │   ├── skills_tree.yaml      # 全书技能树与前置依赖
│       │   └── loadouts\             # 核心角色当前快捷栏 (Active Deck)
│       ├── territory\                # 【领主流专项】领地建筑与资源账本
│       │   ├── buildings.yaml        # 领地建筑等级与拓扑树
│       │   └── economy.yaml          # 资源日产耗与人口治安基准
│       ├── chapters\                 # 章节草稿与正文产物
│       │   └── ch_00051\             # 单章专属目录
│       │       ├── draft.md          # 章节小说正文 (带 YAML 场景元数据)
│       │       ├── metadata.json     # 章节生成元数据、模型参数与耗时
│       │       ├── chapter-outline.json # 章节大纲与节拍
│       │       └── state-delta.json  # 本章待确认的状态变更 (金币/战力/伤势)
│       └── branches\                 # What-if 废案与草稿分支沙箱
│           └── what_if_save_elder\   # 独立分支推演
│
├── skills\                           # novel-Skill 学习成果与写作技法库
│   └── <skill-slug>\                 # 技能专栏 (如 chusheng-author, xuanhuan-combat)
│       ├── SKILL.md                  # 技能元数据与触发边界说明
│       ├── rules.yaml                # 经过验证的高分写作机制清单
│       ├── anti_patterns.yaml        # 负向禁写教条 (在写作 Prompt 中显式规避)
│       └── examples\                 # 优选示范片段 (带原文出处引用)
│
├── materials\                        # 通用创意素材库 (与原著客观事实物理隔离)
│   ├── golden_fingers\               # 金手指与系统设定种子
│   ├── plot_tropes\                  # 经典桥段与反转套路
│   └── scene_seeds\                  # 场景描写素材库
│
├── sources\                          # 原始底本与参考小说 (不可变，只读)
│   └── <source-id>\                  # 参考原著 (如 wudongqiankun)
│       ├── source.yaml               # 原著元数据与版本哈希
│       ├── raw.txt                   # 原始文本全文
│       └── scenes\                   # 自动切分得到的语义场景切片
│
├── config\                           # 运行时配置文件
│   ├── config.yaml                   # 知识库全局基础配置 (端口、路径、阈值)
│   └── models.yaml                   # 大模型网关任务路由与提供商配置
│
├── prompts\                          # 外部化提示词模板目录 (Jinja2/Markdown)
│   ├── extraction\                   # 实体与主张抽取提示词
│   ├── ooc_check\                    # 逻辑与 OOC 审查提示词
│   ├── style\                        # 文风特征提炼提示词
│   └── summary\                      # 分卷与章节滚动摘要提示词
│
├── tests\                            # 自动化测试套件
│   ├── unit\                         # 模块单元测试
│   ├── integration\                  # 跨模块与 novel-Skill 联调测试
│   └── regression\                   # 12 个长周期最小回归测试用例
│
├── dev-docs\                         # 本开发设计与工程实现规范套件 (本文档所在目录)
└── docs\knowledge-base\              # 概念领域模型与业务需求文档
```

---

## 2. 关键文件存储落盘格式规范

### 2.1 作品元数据文件：`projects/<work-id>/work.json`
```json
{
  "work_id": "chusheng",
  "title": "初圣",
  "owner_id": "author-a",
  "genre_ids": ["xuanhuan", "cultivation"],
  "world_type": "original",
  "divergence_anchor": null,
  "created_at": "2026-09-01T10:00:00Z",
  "updated_at": "2026-09-04T12:00:00Z",
  "active_timeline": "main",
  "default_skill_slugs": ["xuanhuan-combat", "chusheng-author"]
}
```

### 2.2 章节小说正文：`projects/<work-id>/chapters/ch_00051/draft.md`
必须使用标准 Markdown 并在 Frontmatter 中注入语义场景 UUID：
```markdown
---
chapter_index: 51
title: "第51章 悬崖夺宝"
scene_uuid: "sc_9b8c7d6e_cliff_01"
story_time: "year_102_month_3_day_15_morning"
pov_character_id: "char_lin_dong"
scene_type: "combat"
token_count: 3250
created_at: "2026-09-04T14:00:00Z"
---

狂风呼啸，悬崖边的枯树剧烈摇晃。

林动握紧了袖中的黑色短刃，目光死死盯着对面的黑袍人...
```

### 2.3 待确认状态增量：`projects/<work-id>/chapters/ch_00051/state-delta.json`
记录本章正文中发生、待作者最终确认的数值与状态变动：
```json
{
  "chapter_index": 51,
  "scene_uuid": "sc_9b8c7d6e_cliff_01",
  "status": "pending_confirmation",
  "events": [
    {
      "entity_id": "char_lin_dong",
      "metric_id": "gold",
      "delta": -500,
      "reason": "购买回血丹",
      "rule_version": "v1"
    },
    {
      "entity_id": "char_lin_dong",
      "metric_id": "injuries",
      "delta": 1,
      "detail": "左臂被剑气划伤",
      "valid_until": "chapter_53"
    }
  ]
}
```

### 2.4 负面反面教条库：`skills/<skill-slug>/anti_patterns.yaml`
存储 `novel-Skill` 学习提取出的行文恶习禁令，直接作为 Negative Constraints 注入 Prompt：
```yaml
skill_slug: "xuanhuan-combat"
version: "1.2.0"
anti_patterns:
  - id: "ap_cliche_chengyu_stack"
    name: "成语密集堆砌"
    description: "连续两句动作戏使用三个以上四字成语，导致节奏拖沓，失去打击感"
    negative_prompt: "严禁在动作交锋中连续堆砌成语（如：电光石火、石破天惊、风驰电掣）。多用短句动词直陈动作过程。"
    penalty_weight: 0.8

  - id: "ap_villain_exposition_dump"
    name: "反派打斗中自报家门说废话"
    description: "生死搏杀时刻，反派突然停下长篇大论解释自己的功法弱点"
    negative_prompt: "生死交锋时刻，反派对白不得超过10个字，严禁反派主动长篇大论解释技能原理。"
    penalty_weight: 1.0
```

---

## 3. 存储隔离原则与派生清理规则

1. **持久化与派生严格隔离**：
   - 永久资产位于 `projects/`、`skills/`、`materials/`、`sources/`；
   - 临时与派生数据严格收敛于 `data/` 目录；
2. **零风险全量重建（One-Command Recovery）**：
   - 开发者可随时彻底删除 `data/manifest.sqlite`，执行 `python -m fxi.cli rebuild`，系统会在数十秒内扫描所有 Markdown 与 YAML，100% 满血复原所有表结构、因果关系、FTS5 全文索引与状态账本；
3. **缓存独立化**：
   - 模型调用缓存存放在 `data/cache.sqlite`，其损坏或被清除绝不影响知识库业务数据，仅导致首次请求重新调用 API。
