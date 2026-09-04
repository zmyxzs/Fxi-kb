"""
fxi.cli.commands_ops - 运维与重建命令
"""

import typer
from rich.console import Console
from rich.table import Table
from fxi.storage.backup import BackupManager
from fxi.storage.rebuild import Rebuilder
from fxi.model_gateway.cost_tracker import CostTracker

app = typer.Typer(help="知识库运维与索引重建命令")
console = Console()


@app.command("rebuild")
def rebuild_all():
    """从纯文本 Markdown/YAML 一键全量重建 SQLite 与 FTS5 索引"""
    console.print("[bold yellow]正在扫描 Markdown/YAML 并全量重建知识库索引...[/bold yellow]")
    rebuilder = Rebuilder()
    report = rebuilder.rebuild_all()

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

    console.print(table)
    if report.warnings:
        for w in report.warnings:
            console.print(f"[red]告警: {w}[/red]")
    console.print("[bold green]✔ 重建完成！派生数据库与全文检索已满血恢复。[/bold green]")


@app.command("backup")
def backup():
    """执行数据库热快照与纯文本归档"""
    console.print("[yellow]正在创建快照备份...[/yellow]")
    bm = BackupManager()
    path = bm.create_snapshot()
    console.print(f"[bold green]✔ 快照已保存至: {path}[/bold green]")


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
