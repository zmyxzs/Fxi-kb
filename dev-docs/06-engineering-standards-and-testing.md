# 06 工程规范、错误处理与测试验收体系

---

## 1. 运行纪律与跨平台规范（Windows / PowerShell）

鉴于开发环境为 Windows 10/11，命令行默认运行在 PowerShell 下，全系统开发必须严格遵守以下工程规范：

1. **UTF-8 强制编码准则**：
   - 所有 Python 源码文件、Markdown 实体卡、YAML 配置文件必须强制保存为无 BOM 的 `UTF-8` 编码；
   - 文本读写操作必须显式指定 `open(path, "r", encoding="utf-8")`，严禁依赖 Windows 默认的 `GBK` 导致乱码污染知识库。
2. **终端与路径引用规范**：
   - 包含空格或中文的路径（如 `D:\Code\Fxi\projects\初圣\`）在终端命令与 Python 代码中必须使用双引号或 `pathlib.Path` 进行安全包裹；
   - 跨平台路径一律采用 `pathlib.Path`，禁止手动字符串斜杠拼接（`\` vs `/`）。
3. **严禁悬挂式交互命令**：
   - 自动化测试与 CI 脚本中，严禁调用需用户手动输入确认的悬挂命令（如不带 `-y` 的安装、无定向输入的 REPL、分页器 `less`/`more` 等）。

---

## 2. 异常体系与错误处理红线

### 2.1 异常继承树结构

```text
FxiError (基础抽象异常)
├── StorageError (文件IO、EBUSY占用、YAML解析损坏)
├── ValidationError (Pydantic Schema 校验失败、非法字段)
├── DomainConflictError
│   ├── OOCConflictError (认知早泄、时空因果穿越)
│   └── LedgerOverdraftError (EXPLICIT 模式下余额透支)
├── GatewayError
│   ├── ProviderTimeoutError (API 响应超时)
│   ├── RateLimitExceededError (供应商 429 限流)
│   └── JSONRepairFailedError (模型输出畸变且 1-Shot 修复失败)
└── NotFoundError (实体、作品、场景 UUID 不存在)
```

### 2.2 错误处理四大工程红线（绝对禁止）
1. **严禁空 catch**：禁止出现 `except: pass` 或 `except Exception: return None` 悄悄吞没异常，导致系统在脏数据状态下静默运行；
2. **严禁无上限盲目重试**：网络请求重试必须设定上限（最大 3 次）并采用**带抖动的指数退避（Exponential Backoff with Jitter）**；
3. **严禁在日志中记录敏感密钥**：输出异常或审计日志时，API Key、Token 必须通过 `secret_env` 自动进行掩码脱敏（如 `sk-****abcd`）；
4. **强校验边界**：所有从大模型网关返回的数据，必须通过 Pydantic 校验成功后才允许进入数据库或业务流程。

---

## 3. 12 个长周期最小回归测试矩阵（CI 必跑）

在运行 `pytest tests/regression/` 时，以下 12 个长篇小说写作极端场景必须 100% 自动化通过：

| 用例 ID | 测试名称 | 验证场景与断言标准 | 对应的核心模块 |
|:---:|:---|:---|:---:|
| **REG-01** | 同名角色绝对隔离 | 项目 A 与项目 B 均有名为“林动”的角色，查询项目 A 时断言结果中 0 包含项目 B 的宗门设定。 | `project`, `domain` |
| **REG-02** | 同人分歧点蝴蝶效应 | 原著第 12 章配角战死，同人作品在第 10 章救下该配角；查询第 15 章时断言原著葬礼情节被 POD 阻断切除。 | `timeline.pod_filter` |
| **REG-03** | 动态账本剧情删改重算 | 第 2 章得 200 金币，第 3 章花 50，第 4 章花 80；中途删掉第 3 章消费，断言第 4 章余额自动从 170 重新结算为 220。 | `state_ledger.calculator` |
| **REG-04** | 防 OOC 秘密早泄拦截 | 角色 A 在第 8 章得知真凶，在第 5 章对白中说出凶手名字，断言 `OOCChecker` 触发 `OOCConflictError` 并定位行号。 | `character_knowledge` |
| **REG-05** | 谎言与真相共存 | 角色 B 在第 3 章对主角说谎欺诈，第 10 章揭露事实；断言知识库同时保存该欺诈事件与客观真理，互不覆盖。 | `claims.models` |
| **REG-06** | 纯文本一键全量重建 | 手动物理删除 `data/manifest.sqlite`，执行 `kb rebuild`；断言所有实体卡、关系图谱与 FTS5 索引 100% 满血恢复。 | `storage.rebuild` |
| **REG-07** | 文风规则一键熔断回滚 | 某条风格规则标记为 `disable`；断言后续场景检索时该规则立即消失，历史快照保持不变。 | `materials_skills` |
| **REG-08** | 中文专有词典切词精准 | 输入罕见修仙法宝名（如“九天玄冰破煞符”）；断言 FTS5 作为完整词条命中，未被破坏性拆分为单字。 | `index_retrieval.jieba_fts` |
| **REG-09** | POV 视点盲区防穿帮 | 以反派 B 视点请求写作上下文；断言主角在上个场景刚习得的隐藏秘法被物理剔除，模型绝不获取透视情报。 | `character_knowledge.pov_filter` |
| **REG-10** | 合法吃书修正生效 | 作者登记 `retcon` 修正主角旧法宝属性；断言 OOC 扫描引擎放行冲突并不再报警，输出圆场过渡建议。 | `claims.retcon` |
| **REG-11** | 半途接入基准锚点 | 前 50 章未统计战力（`UNMEASURED`），第 51 章设定基准 3000 并消耗 500；断言结余 2500 且前 50 章不报透支错误。 | `state_ledger.anchor` |
| **REG-12** | 场景 UUID 章节重排抗脆性 | 在第 5 章前加更一章导致全书物理章号整体后移；断言基于 `scene_uuid` 绑定的伏笔与账本关联丝滑保持。 | `sources.scene_id` |
| **REG-13** | 角色性格随时间黑化切换 | 主角在第 30 章遭遇变故心性黑化；第 20 章检索断言返回隐忍退让性格，第 40 章检索断言返回冷酷果断性格。 | `domain.phases` |
| **REG-14** | 量产通用物品独立消耗 | 林动与吴云人手一个“下品储物袋”；林动的储物袋在战斗中损毁，断言吴云的储物袋依然完好，互不串扰。 | `domain.items` |
| **REG-15** | 唯一神兵流转防穿帮 | 主角在第 50 章将“青峰剑”赠送给师弟；在第 60 章检索主角装备时，断言该剑已不在主角持有清单中。 | `domain.ownership` |
| **REG-16** | 死亡读档与时间循环隔离 | 主角在 Day 3 阵亡回档至 Day 1：断言物理世界配角起死回生、金币复原，但主角保留已知真凶记忆；因果 DAG 保持单调递增不成环，同人多轮次推演绝不串回原著。 | `timeline.reversion` |
| **REG-17** | 游戏技能冷却与蓝耗拦截 | 主角在第 10 章消耗 80 蓝量释放冷却为 3 步的大招；第 11 章蓝量剩余 20 再次释放时，断言 `SkillTreeEngine` 触发 `SkillCastIllegalError` 拦截（蓝量透支+冷却未好）。 | `game_engine.skills` |
| **REG-18** | 领地资源透支与断粮预警 | 领地存粮仅能维持 3 天，作者试图下令征召 500 名重步兵；断言规则引擎拦截透支升级，并自动生成 `粮饷危机` 宏观语义告警标签注入上下文。 | `territory.resources`, `territory.macro_tags` |
| **REG-19** | 阶梯投影防爆与被动雷达 | 主角掌握 120 个技能，在战斗场景中调用 `TieredParameterProjector`；断言投影面板严控在 450 Tokens 内；当大纲出现剧毒沼泽时，断言沉睡 30 章的【初级毒抗】被雷达精准激活召回。 | `game_engine.projection`, `game_engine.passive_radar` |

---

## 4. 自动化测试套件目录与执行命令

```text
tests/
├── conftest.py                   # Pytest 全局 Fixture (测试内存数据库、模拟工作区)
├── unit/                         # 单元测试 (纯逻辑计算)
│   ├── test_calculator.py        # 账本加减计算
│   ├── test_repair.py            # json_repair 容错补全
│   ├── test_scene_id.py          # 场景 UUID 稳定性
│   └── test_intervals.py         # 时间区间计算
├── integration/                  # 跨模块集成测试
│   ├── test_fts_retrieval.py     # 中文全文检索测试
│   ├── test_model_gateway.py     # 网关路由与哈希缓存
│   └── test_novel_skill_sync.py  # 与 novel-Skill 契约对齐测试
└── regression/                   # 12 个长周期最小回归矩阵
    └── test_12_canonical_cases.py
```

### 本地测试执行命令：
```powershell
# 运行全部单元测试
pytest tests/unit/ -v

# 运行 12 个长篇核心回归测试
pytest tests/regression/ -v -s

# 检查测试覆盖率 (目标核心模块覆盖率 >= 85%)
pytest --cov=src/fxi --cov-report=term-missing
```
