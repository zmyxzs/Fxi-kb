"""
fxi.cli.commands_search - 全文搜索、状态查询与蝴蝶效应命令行
"""

import typer
from typing import Optional, Any
from rich.console import Console
from rich.table import Table
from fxi.index_retrieval.jieba_fts import ChineseFTS

from fxi.state_ledger.calculator import LedgerCalculator
from fxi.timeline.ripple_analyzer import RippleAnalyzer

app = typer.Typer(help="检索与蝴蝶效应因果分析命令")
console = Console()


@app.command("find")
def search_text(
    query: str,
    work_id: str = typer.Option(None, "--work-id", "-w"),
    limit: int = 5,
    source_id: Optional[str] = typer.Option(None, "--source-id"),
    source_version: Optional[str] = typer.Option(None, "--source-version"),
):
    """使用 jieba+FTS5 毫秒级全文检索小说场景片段"""
    fts = ChineseFTS()
    try:
        hits = fts.search(
            query=query,
            work_id=work_id,
            limit=limit,
            source_id=source_id,
            source_version=source_version,
        )
    except ValueError as exc:
        console.print(f"[bold red]检索范围无效: {exc}[/bold red]")
        raise typer.Exit(code=1)

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
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品标识"),
    scenes: int = typer.Option(5, "--scenes", "-s", help="匹配原著场景切片上限"),
    source_id: Optional[str] = typer.Option(None, "--source-id"),
    source_version: Optional[str] = typer.Option(None, "--source-version"),
    knowledge_version: Optional[str] = typer.Option(None, "--knowledge-version"),
    narrative_order: Optional[int] = typer.Option(None, "--narrative-order"),
    timeline_id: Optional[str] = typer.Option(None, "--timeline-id"),
    divergence_narrative_order: Optional[int] = typer.Option(None, "--divergence-narrative-order"),
):
    """【智能自然语言问答】：自动拆解实体与关键词，聚合多源证据给出权威回答"""
    from fxi.index_retrieval.query_engine import QueryEngine

    console.print(f"[bold cyan]正在解析并检索知识库...[/bold cyan]")
    engine = QueryEngine()
    res = engine.ask(
        work_id=work_id,
        question=question,
        top_k_scenes=scenes,
        source_id=source_id,
        source_version=source_version,
        knowledge_version=knowledge_version,
        narrative_order=narrative_order,
        timeline_id=timeline_id,
        divergence_narrative_order=divergence_narrative_order,
    )

    # 显示拆解出的证据线索
    console.print(f"\n[bold yellow]🔍 意图与检索词拆解:[/bold yellow]")
    console.print(f"- 关联实体: {res.decomposition.target_entities}")
    console.print(f"- 搜索关键词: {res.decomposition.keywords}")
    console.print(f"- 能力信号: {[signal.capability for signal in res.decomposition.signals]}")
    console.print(f"- 结果状态: {res.evidence.get('result_status', 'UNKNOWN')}")
    console.print(f"- 命中因果事件数: {len(res.evidence.get('events', []))}")
    console.print(f"- 命中原著场景数: {len(res.evidence.get('scenes', []))}")

    # 显示回答
    console.print(f"\n[bold green]💡 知识库回答:[/bold green]")
    console.print(res.answer)


@app.command("timeline")
def query_timeline(
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品标识"),
    entity: Optional[str] = typer.Option(None, "--entity", "-e", help="按实体名或关键词筛选"),
    start: int = typer.Option(1, "--start", "-s", help="起始叙事章节"),
    end: int = typer.Option(9999, "--end", help="结束叙事章节"),
    limit: int = typer.Option(25, "--limit", "-l", help="最多显示事件条数")
):
    """查询因果时间线（支持实体/关键词过滤与章节区间检索）"""
    from fxi.storage.sqlite_client import DatabaseClient
    from fxi.core.config import load_config
    cfg = load_config()
    client = DatabaseClient(cfg.sqlite_path)

    query = "SELECT event_id, narrative_order, physical_time, summary, status FROM causal_events WHERE work_id = ? AND narrative_order BETWEEN ? AND ?"
    params: list[Any] = [work_id, start, end]
    if entity:
        query += " AND summary LIKE ?"
        params.append(f"%{entity}%")
    query += " ORDER BY narrative_order ASC LIMIT ?"
    params.append(limit)

    with client.get_connection() as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        console.print("[yellow]未找到符合条件的时间线事件。[/yellow]")
        return

    table = Table(title=f"因果时间线 [{work_id}] (第 {start}~{end} 章)")
    table.add_column("章节/序位", style="cyan", width=10)
    table.add_column("事件 ID", style="magenta", width=24)
    table.add_column("物理时间/时标", style="dim", width=22)
    table.add_column("事件摘要", style="green")

    for r in rows:
        table.add_row(str(r["narrative_order"]), r["event_id"], r["physical_time"] or "-", r["summary"])
    console.print(table)


@app.command("relations")
def query_relations(
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品标识"),
    entity: Optional[str] = typer.Option(None, "--entity", "-e", help="实体名称或 ID"),
    tension: bool = typer.Option(False, "--tension", "-t", help="优先展示高张力对抗关系")
):
    """查询角色人际关系与深层戏剧张力"""
    from fxi.domain.relations import RelationManager
    from fxi.core.config import load_config
    rm = RelationManager(load_config())
    rels = rm.load_relationships(work_id)

    if entity:
        rels = [
            r for r in rels
            if entity in r.get("character_a", "") or entity in r.get("character_b", "") or any(entity in p for p in r.get("pair", []))
        ]

    if tension:
        rels = [r for r in rels if r.get("tension") and len(r.get("tension")) > 5]

    if not rels:
        console.print("[yellow]未检索到匹配的角色关系记录。[/yellow]")
        return

    table = Table(title=f"角色人际关系与戏剧张力 [{work_id}]")
    table.add_column("角色对", style="cyan", width=24)
    table.add_column("关系动力", style="magenta", width=18)
    table.add_column("深层戏剧张力 / 互动逻辑", style="yellow")

    for r in rels:
        pair = r.get("pair") or [r.get("character_a", ""), r.get("character_b", "")]
        pair_str = " <-> ".join(pair)
        table.add_row(pair_str, r.get("dynamic") or "暂无", r.get("tension") or "暂无")
    console.print(table)



@app.command("items")
def query_items(
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品标识"),
    item: Optional[str] = typer.Option(None, "--item", "-i", help="按物品名称/ID筛选"),
    owner: Optional[str] = typer.Option(None, "--owner", "-o", help="按持有者筛选"),
    history: bool = typer.Option(False, "--history", "-H", help="显示流转历史台账"),
    limit: int = typer.Option(25, "--limit", "-l", help="最多显示条数")
):
    """查询物品档案及持有流转"""
    import yaml
    from fxi.storage.sqlite_client import DatabaseClient
    from fxi.core.config import load_config
    cfg = load_config()
    client = DatabaseClient(cfg.sqlite_path)

    if history:
        query = """
        SELECT e.name AS item_name, o.item_ref_id, o.from_owner_id, o.to_owner_id, o.transfer_type, o.narrative_order, o.reason
        FROM item_ownership_events o
        LEFT JOIN entities e ON o.work_id = e.work_id AND o.item_ref_id = e.entity_id
        WHERE o.work_id = ?
        """
        params: list[Any] = [work_id]
        if owner:
            query += " AND (o.from_owner_id LIKE ? OR o.to_owner_id LIKE ?)"
            params.extend([f"%{owner}%", f"%{owner}%"])
        if item:
            query += " AND (o.item_ref_id LIKE ? OR e.name LIKE ?)"
            params.extend([f"%{item}%", f"%{item}%"])
        query += " ORDER BY o.narrative_order ASC LIMIT ?"
        params.append(limit)

        with client.get_connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()

        if not rows:
            console.print("[yellow]未检索到物品持有流转记录。可去除 -H 查看物品属性列表。[/yellow]")
            return

        table = Table(title=f"物品流转台账 [{work_id}]")
        table.add_column("章节", style="cyan", width=8)
        table.add_column("物品名称/ID", style="magenta", width=22)
        table.add_column("流转类型", style="blue", width=12)
        table.add_column("原持有者 -> 现持有者", style="green", width=30)
        table.add_column("事由 / 来源背景", style="yellow")

        for r in rows:
            item_name = r["item_name"] or r["item_ref_id"]
            flow = f"{r['from_owner_id'] or '初始'} -> {r['to_owner_id'] or '无'}"
            table.add_row(str(r["narrative_order"]), item_name, r["transfer_type"], flow, r["reason"] or "-")
        console.print(table)
        return

    # 默认展示物品实体清单与属性
    query = "SELECT entity_id, name, attributes_yaml FROM entities WHERE work_id = ? AND category = 'item'"
    params = [work_id]
    if item:
        query += " AND (name LIKE ? OR entity_id LIKE ?)"
        params.extend([f"%{item}%", f"%{item}%"])
    query += " ORDER BY entity_id ASC LIMIT ?"
    params.append(limit)

    with client.get_connection() as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()

    if not rows:
        console.print("[yellow]未检索到匹配的物品实体。[/yellow]")
        return

    table = Table(title=f"物品档案列表 [{work_id}] (共展示 {len(rows)} 件)")
    table.add_column("物品 ID", style="cyan", width=26)
    table.add_column("物品名称", style="magenta", width=22)
    table.add_column("功能设定 / 属性", style="green")

    for r in rows:
        attrs = yaml.safe_load(r["attributes_yaml"] or "{}") or {}
        func_desc = attrs.get("function") or attrs.get("description") or str(attrs) if attrs else "暂无特殊属性"
        table.add_row(r["entity_id"], r["name"], str(func_desc))
    console.print(table)


@app.command("skills")
def query_skills(
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品标识"),
    entity: Optional[str] = typer.Option(None, "--entity", "-e", help="按角色名筛选")
):
    """查询角色能力与技能树"""
    import yaml
    from fxi.storage.sqlite_client import DatabaseClient
    from fxi.core.config import load_config
    cfg = load_config()
    client = DatabaseClient(cfg.sqlite_path)

    # 1. 优先查 character_skills / skills_tree
    query = """
    SELECT e.name AS character_name, cs.entity_id, cs.skill_id, COALESCE(st.name, cs.skill_id) AS skill_name, COALESCE(st.tier, 1) AS tier, COALESCE(st.skill_type, 'active') AS skill_type, COALESCE(st.description, '') AS description
    FROM character_skills cs
    LEFT JOIN entities e ON cs.work_id = e.work_id AND cs.entity_id = e.entity_id
    LEFT JOIN skills_tree st ON cs.work_id = st.work_id AND cs.skill_id = st.skill_id
    WHERE cs.work_id = ?
    """
    params: list[Any] = [work_id]
    if entity:
        query += " AND (cs.entity_id LIKE ? OR e.name LIKE ?)"
        params.extend([f"%{entity}%", f"%{entity}%"])
    query += " ORDER BY cs.entity_id, st.tier ASC"

    with client.get_connection() as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()

    if rows:
        table = Table(title=f"角色能力与技能树 [{work_id}]")
        table.add_column("角色", style="cyan", width=14)
        table.add_column("技能名称", style="magenta", width=20)
        table.add_column("阶位/类型", style="blue", width=14)
        table.add_column("描述 / 设定效果", style="green")

        for r in rows:
            c_name = r["character_name"] or r["entity_id"]
            tier_str = f"Tier {r['tier']} ({r['skill_type']})"
            table.add_row(c_name, r["skill_name"], tier_str, r["description"] or "-")
        console.print(table)
        return

    # 2. 回退模式：若独立技能树暂无单独挂载，直接从角色实体属性中提取境界与能力
    q_char = "SELECT entity_id, name, attributes_yaml FROM entities WHERE work_id = ? AND category = 'character'"
    p_char = [work_id]
    if entity:
        q_char += " AND (name LIKE ? OR entity_id LIKE ?)"
        p_char.extend([f"%{entity}%", f"%{entity}%"])
    q_char += " LIMIT 20"

    with client.get_connection() as conn:
        cur = conn.execute(q_char, p_char)
        c_rows = cur.fetchall()

    if not c_rows:
        console.print("[yellow]未检索到角色能力记录。[/yellow]")
        return

    table = Table(title=f"角色境界与能力档案 [{work_id}]")
    table.add_column("角色", style="cyan", width=14)
    table.add_column("境界 / 力量层级", style="magenta", width=35)
    table.add_column("身份与核心设定", style="green")

    for r in c_rows:
        attrs = yaml.safe_load(r["attributes_yaml"] or "{}") or {}
        realm = attrs.get("realm", "未标明")
        identity = attrs.get("identity", "暂无")
        table.add_row(r["name"], str(realm), str(identity))
    console.print(table)
