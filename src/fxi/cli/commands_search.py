"""
fxi.cli.commands_search - 全文搜索、状态查询与蝴蝶效应命令行
"""

import typer
from rich.console import Console
from rich.table import Table
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.timeline.ripple_analyzer import RippleAnalyzer

app = typer.Typer(help="检索与蝴蝶效应因果分析命令")
console = Console()


@app.command("find")
def search_text(query: str, work_id: str = typer.Option(None, "--work-id", "-w"), limit: int = 5):
    """使用 jieba+FTS5 毫秒级全文检索小说场景片段"""
    fts = ChineseFTS()
    hits = fts.search(query=query, work_id=work_id, limit=limit)

    if not hits:
        console.print("[yellow]未检索到相关内容。[/yellow]")
        return

    table = Table(title=f"全文检索结果: '{query}'")
    table.add_column("场景 UUID", style="cyan")
    table.add_column("作品", style="magenta")
    table.add_column("高亮匹配片段", style="green")

    for h in hits:
        table.add_row(h["scene_uuid"], h["work_id"], h["snippet"])

    console.print(table)


@app.command("state")
def query_state(work_id: str, entity_id: str, metric_id: str, chapter: int = 1):
    """查询实体在指定章节节点的动态数值结算"""
    calc = LedgerCalculator()
    snap = calc.calculate_balance(work_id, entity_id, metric_id, narrative_order=chapter)

    console.print(f"[bold cyan]实体状态账本结算:[/bold cyan]")
    console.print(f"- 作品: {snap.work_id}")
    console.print(f"- 实体: {snap.entity_id}")
    console.print(f"- 指标: {snap.metric_id}")
    console.print(f"- 章节: 第 {snap.narrative_order} 章")
    console.print(f"- 结余: [bold green]{snap.computed_value}[/bold green] ({snap.status.value})")


@app.command("ripple")
def check_ripple(work_id: str, canon_work_id: str, canon_event_id: str):
    """预检同人剧情沿用原著时的蝴蝶效应兼容性"""
    analyzer = RippleAnalyzer()
    status, reason, suggestions = analyzer.check_canon_compatibility(work_id, canon_work_id, canon_event_id)

    console.print(f"[bold cyan]蝴蝶效应因果相容性预检: [{canon_event_id}][/bold cyan]")
    status_color = "green" if status.value == "untouched" else ("yellow" if status.value == "mutated" else "red")
    console.print(f"- 判定状态: [{status_color}]{status.value.upper()}[/{status_color}]")
    console.print(f"- 诊断分析: {reason}")
    if suggestions:
        console.print("[bold magenta]重构推演建议:[/bold magenta]")
        for s in suggestions:
            console.print(f"  • {s}")


@app.command("ask")
def ask_question(
    question: str,
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品标识，如 zhanshen"),
    scenes: int = typer.Option(5, "--scenes", "-s", help="匹配原著场景切片上限")
):
    """【智能自然语言问答】：自动拆解实体与关键词，聚合多源证据给出权威回答"""
    from fxi.index_retrieval.query_engine import QueryEngine

    console.print(f"[bold cyan]正在解析并检索知识库...[/bold cyan]")
    engine = QueryEngine()
    res = engine.ask(work_id=work_id, question=question, top_k_scenes=scenes)

    # 显示拆解出的证据线索
    console.print(f"\n[bold yellow]🔍 意图与检索词拆解:[/bold yellow]")
    console.print(f"- 关联实体: {res.decomposition.target_entities}")
    console.print(f"- 搜索关键词: {res.decomposition.keywords}")
    console.print(f"- 命中因果事件数: {len(res.evidence.get('events', []))}")
    console.print(f"- 命中原著场景数: {len(res.evidence.get('scenes', []))}")

    # 显示回答
    console.print(f"\n[bold green]💡 知识库回答:[/bold green]")
    console.print(res.answer)

