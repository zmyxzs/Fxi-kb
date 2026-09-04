"""
fxi.cli.commands_project - 作品与实体管理命令
"""

import typer
from rich.console import Console
from rich.table import Table
from fxi.domain.entities import EntityManager
from fxi.storage.sqlite_client import DatabaseClient
from fxi.core.config import load_config

app = typer.Typer(help="作品与实体查询命令")
console = Console()


@app.command("list")
def list_projects():
    """列出库中登记的所有作品"""
    cfg = load_config()
    client = DatabaseClient(cfg.sqlite_path)

    table = Table(title="作品列表")
    table.add_column("Work ID", style="cyan")
    table.add_column("书名", style="green")
    table.add_column("作者", style="yellow")
    table.add_column("同人分歧点", style="magenta")

    with client.get_connection() as conn:
        cur = conn.execute("SELECT work_id, title, owner_id, divergence_anchor FROM works")
        for row in cur.fetchall():
            table.add_row(
                row["work_id"],
                row["title"],
                row["owner_id"],
                row["divergence_anchor"] or "无 (原创主干)"
            )

    console.print(table)


@app.command("entities")
def list_entities(work_id: str, category: str = typer.Option(None, "--category", "-c")):
    """查看某部作品下的故事实体"""
    mgr = EntityManager()
    entities = mgr.list_entities(work_id, category=category)

    table = Table(title=f"作品 [{work_id}] 故事实体列表")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="green")
    table.add_column("类别", style="yellow")
    table.add_column("孤品/量产", style="magenta")

    for e in entities:
        table.add_row(
            e["entity_id"],
            e["name"],
            e["category"],
            "唯一孤品" if e["is_unique"] else "通用量产"
        )

    console.print(table)


@app.command("import")
def import_source(
    path: str = typer.Argument(..., help="章节文件目录或单文件路径"),
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品 ID，如 zhanshen"),
    title: str = typer.Option(..., "--title", "-t", help="作品标题，如 我在精神病院学斩神"),
    limit: int = typer.Option(None, "--limit", "-n", help="最大导入章节数，如 20")
):
    """导入原著章节目录或底本，切分场景并建立 FTS5 全文索引"""
    from pathlib import Path
    from fxi.sources.importer import SourceImporter

    importer = SourceImporter()
    p = Path(path)
    if not p.exists():
        console.print(f"[bold red]错误：路径不存在: {path}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"[cyan]开始导入原著: {title} (ID: {work_id})...[/cyan]")
    if p.is_dir():
        manifest = importer.import_chapters_dir(p, source_id=work_id, title=title, max_chapters=limit)
    else:
        manifest = importer.import_file(p, source_id=work_id, title=title)

    console.print(f"[bold green]✔ 导入成功！[/bold green]")
    console.print(f"  • 章节数: {manifest.total_chapters}")
    console.print(f"  • 总字数: {manifest.total_chars}")
    console.print(f"  • 场景切片数: {manifest.total_scenes}")
    console.print(f"  • 底本哈希: {manifest.sha256[:12]}...")
