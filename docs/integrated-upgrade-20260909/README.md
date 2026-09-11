# Fxi 集成升级计划入口

> 状态：`CURRENT` + `PLANNED`
>
> - `CURRENT`：本文件只保存集成升级导航与 Fxi 侧范围说明；当前代码、测试和公开入口的现实边界以本仓现行文档为准。
> - `PLANNED`：被导航的集成升级主计划仍为 `PROPOSED` / 待确认实施（核对日期：2026-09-10），不代表跨仓真实闭环已经完成。

以下条目均为仓库外 sibling 工作区 `novel-Studio` 的证据引用，不是本仓可审计的 Markdown 链接。本仓无法独立审计这些文件的内容、版本、提交或当前状态；这里不臆造 URL 或提交号。如需核对，必须取得指定文件及其明确版本。

- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/README.md`（统一入口）
- 外部工作区证据：`novel-Studio/.codex/plan/integrated-upgrade-20260909.md`（实施主计划）
- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/02-contracts.md`（公共架构与 C1—C9）
- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/03-fxi-work-packages.md`（Fxi W10—W21）
- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/06-advanced-work-packages.md`（高级知识包 W22—W27 与模型/扩展包）
- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/07-acceptance.md`（真实闭环验收）
- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/08-agent-dispatch.md`（两个主 agent 与 Luna 子任务提示词）
- 外部工作区证据：`novel-Studio/docs/integrated-upgrade-20260909/work-packages.json`（50 包机器分派表）

Fxi负责来源、证据、候选治理、采纳、批准知识版本、公开查询和原子知识提交；已有SQLite持久化组合根/OAG-lite/Wiki先验证复用，不重新建设。W26属于Studio，不能因出现在高级知识包文件中就在本仓实现。

确认实施后只按统一计划派单；共享Schema/router/CLI/registry/持久化接线由本仓主agent串行修改。先M0真实能力核对与契约冻结，再M1首章闭环；未经用户批准不得推进知识提交，不能用测试批准替代真实确认。

如并列Studio仓库不在当前机器，先取得指定计划文件及其版本，不自行复制旧计划猜测最新契约。本轮只保存工程文档，无业务导入、抽取、生成或提交。
