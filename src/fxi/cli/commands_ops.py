"""
fxi.cli.commands_ops - 运维与重建命令
"""

import json
import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
from fxi.core.config import load_config
from fxi.ops.recovery_probe import RecoveryProbe
from fxi.ops.retire_manifest import RetireManifest
from fxi.storage.backup import BackupManager
from fxi.storage.rebuild import RebuildService, Rebuilder
from fxi.model_gateway.cost_tracker import CostTracker

app = typer.Typer(help="知识库运维与索引重建命令")
console = Console()


@app.command("rebuild")
def rebuild_all(
    selectors: list[str] | None = typer.Option(
        None,
        "--selector",
        "-s",
        help="只重建指定投影；可重复传入，默认执行旧版 lexical rebuild。",
    ),
):
    """从纯文本 Markdown/YAML 一键全量重建 SQLite 与 FTS5 索引"""
    console.print("[bold yellow]正在扫描 Markdown/YAML 并全量重建知识库索引...[/bold yellow]")
    # Direct Python callers (including legacy tests) do not pass Typer's
    # OptionInfo wrapper; treat it as the declared default rather than an
    # iterable selector list.
    if not isinstance(selectors, (list, tuple)):
        selectors = None
    if selectors:
        report = RebuildService(rebuilder=Rebuilder()).rebuild(tuple(selectors))
    else:
        report = Rebuilder().rebuild_all()

    table = Table(title="知识库全量重建报告")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")

    table.add_row("耗时", f"{report.elapsed_seconds} 秒")
    table.add_row("作品数", str(report.works_count))
    table.add_row("实体数", str(report.entities_count))
    table.add_row("性格阶段数", str(report.phases_count))
    table.add_row("章节数", str(report.chapters_count))
    table.add_row("技能包数", str(report.skills_count))
    table.add_row("参考底本数", str(report.sources_count))
    table.add_row("状态", report.status)
    if report.selectors:
        table.add_row("选择器", ", ".join(report.selectors))

    console.print(table)
    if report.warnings or report.blockers or report.errors:
        for w in report.warnings:
            console.print(f"[red]告警: {w}[/red]")
        for blocker in report.blockers:
            console.print(f"[red]阻塞: {blocker}[/red]")
        for error in report.errors:
            console.print(f"[red]错误: {error}[/red]")
        raise typer.Exit(code=1)
    console.print("[bold green]✔ 重建完成！派生数据库与全文检索已满血恢复。[/bold green]")


@app.command("backup")
def backup():
    """执行数据库热快照与纯文本归档"""
    console.print("[yellow]正在创建快照备份...[/yellow]")
    bm = BackupManager()
    path = bm.create_snapshot()
    console.print(f"[bold green]✔ 快照已保存至: {path}[/bold green]")


@app.command("restore")
def restore(
    snapshot_dir: Path = typer.Argument(..., help="已校验的快照目录"),
    target_dir: Path = typer.Argument(..., help="不存在的隔离恢复目标目录"),
):
    """校验快照并恢复到隔离目录；不会覆盖当前工作区。"""
    try:
        restored = BackupManager(load_config()).restore_snapshot(snapshot_dir, target_dir)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        console.print(f"[bold red]恢复失败: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[bold green]✔ 快照已恢复至隔离目录: {restored}[/bold green]")


@app.command("recovery-probe")
def recovery_probe(
    backup_dir: Path = typer.Argument(..., help="已校验的备份目录"),
    target_dir: Path = typer.Argument(..., help="恢复后的隔离目录"),
) -> None:
    """只读比较备份与恢复目录；失败时返回非零退出码。"""

    report = RecoveryProbe().verify(backup_dir, target_dir)
    console.print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    if not report.success:
        raise typer.Exit(code=1)


@app.command("retire-dry-run")
def retire_dry_run(
    root: Path = typer.Argument(..., help="只读检查的运行时根目录"),
    targets: list[Path] = typer.Option(..., "--target", "-t", help="明确允许退役的相对路径，可重复传入"),
) -> None:
    """生成 hash 绑定的退役计划，不执行任何删除。"""

    plan = RetireManifest(targets=targets).dry_run(root)
    console.print(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True))
    if plan.status == "BLOCKED":
        raise typer.Exit(code=1)


@app.command("cost")
def cost_summary():
    """查看知识库维护消耗的 Token 与费用统计"""
    tracker = CostTracker()
    summary = tracker.get_summary()

    table = Table(title="大模型调用费用审计")
    table.add_column("项目", style="cyan")
    table.add_column("统计", style="magenta")

    table.add_row("调用总次数", str(summary["total_calls"]))
    table.add_row("Prompt Tokens", str(summary["total_prompt_tokens"]))
    table.add_row("Completion Tokens", str(summary["total_completion_tokens"]))
    table.add_row("折合费用 (CNY)", f"￥{summary['total_cost_cny']}")

    console.print(table)
