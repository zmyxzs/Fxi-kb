"""
fxi.cli.commands_project - 作品与实体管理命令
"""

from pathlib import Path
import time
from typing import Any, Optional
import yaml
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from fxi.domain.entities import EntityManager
from fxi.storage.backup import BackupManager
from fxi.storage.sqlite_client import DatabaseClient
from fxi.core.config import FxiConfig, load_config
from fxi.core.identifiers import validate_work_id


class ExtractionBindingError(RuntimeError):
    """来源或受控抽取提交参数无法组成完整绑定。"""


def _resolve_extraction_binding(
    config: FxiConfig,
    work_id: str,
    *,
    source_id: Optional[str],
    evaluation_ref: Optional[str],
    submitted_by: Optional[str],
    chapter_start: Optional[int] = None,
    chapter_limit: Optional[int] = None,
) -> dict[str, Any]:
    """Resolve the immutable source binding used by official extract commands."""

    try:
        resolved_work_id = validate_work_id(work_id)
    except Exception as exc:
        raise ExtractionBindingError(f"非法 work_id: {work_id!r}") from exc
    if not isinstance(evaluation_ref, str) or not evaluation_ref.strip():
        raise ExtractionBindingError("必须显式提供 --evaluation-ref")
    if not isinstance(submitted_by, str) or not submitted_by.strip():
        raise ExtractionBindingError("必须显式提供 --submitted-by")
    if chapter_start is not None and chapter_start < 1:
        raise ExtractionBindingError("起始章节必须是正整数")
    if chapter_limit is not None and chapter_limit < 1:
        raise ExtractionBindingError("章节上限必须是正整数")

    from fxi.api.registry import WorkRegistry

    registry = WorkRegistry(DatabaseClient(config.sqlite_path))
    try:
        binding = registry.resolve(resolved_work_id, source_id)
    except Exception as exc:
        code = getattr(exc, "code", "SOURCE_BINDING_FAILED")
        raise ExtractionBindingError(f"来源范围解析失败 [{code}]: {exc}") from exc
    source_root = config.sources_dir.resolve()
    registered_dir = Path(binding.source_dir) if binding.source_dir else source_root / binding.source_id
    source_dir = registered_dir if registered_dir.is_absolute() else source_root / registered_dir
    source_dir = source_dir.resolve()
    if source_dir == source_root or source_root not in source_dir.parents:
        raise ExtractionBindingError("注册来源目录越出 sources 根目录")
    manifest_path = source_dir / "source.yaml"
    if not manifest_path.is_file():
        raise ExtractionBindingError(f"来源清单不存在: {manifest_path.name}")
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ExtractionBindingError("来源清单不可读") from exc
    if not isinstance(manifest, dict):
        raise ExtractionBindingError("来源清单必须是对象")
    manifest_source_id = manifest.get("source_id")
    if manifest_source_id != binding.source_id:
        raise ExtractionBindingError("来源清单 source_id 与注册绑定不一致")
    resolved_version = str(manifest.get("version") or manifest.get("sha256") or "").strip()
    if not resolved_version:
        raise ExtractionBindingError("来源清单缺少不可变版本")
    if binding.source_version and binding.source_version != resolved_version:
        raise ExtractionBindingError("来源清单版本与注册绑定不一致")
    input_hash = str(manifest.get("sha256") or "").strip()
    if not input_hash:
        raise ExtractionBindingError("来源清单缺少输入哈希")

    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise ExtractionBindingError("来源清单缺少 documents 元数据")
    selected: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            raise ExtractionBindingError("来源清单 documents 包含非法项")
        try:
            chapter_index = int(document["chapter_index"])
            document_id = str(document["document_id"])
            char_count = int(document["char_count"])
            excerpt_hash = str(document["content_hash"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExtractionBindingError("来源清单文档元数据不完整") from exc
        if chapter_start is not None and chapter_index < chapter_start:
            continue
        if chapter_limit is not None and chapter_start is not None and chapter_index >= chapter_start + chapter_limit:
            continue
        if chapter_limit is not None and chapter_start is None and len(selected) >= chapter_limit:
            break
        selected.append(
            {
                "source_id": binding.source_id,
                "source_version": resolved_version,
                "document_id": document_id,
                "start_char": 0,
                "end_char": char_count,
                "excerpt_hash": excerpt_hash,
            }
        )
    if not selected:
        raise ExtractionBindingError("指定章节范围没有可验证证据")
    return {
        "source_id": binding.source_id,
        "source_version": resolved_version,
        "input_hash": input_hash,
        "evidence_refs": selected,
        "evaluation_ref": evaluation_ref.strip(),
        "submitted_by": submitted_by.strip(),
    }

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


@app.command("abilities")
def get_abilities(
    work_id: str = typer.Argument(..., help="作品 ID"),
    character: str = typer.Argument(..., help="角色名称或 ID"),
    chapter: Optional[int] = typer.Option(None, "--chapter", "-c", help="时间锚点章节（如 268 表示第268章时已掌握的能力）"),
):
    """查询指定角色在特定章节（或全篇）生效的超凡能力与技能体系"""
    mgr = EntityManager()
    abilities = mgr.get_entity_abilities(work_id, character, chapter=chapter)
    if not abilities:
        console.print(f"[yellow]未查询到角色 [{character}] 在作品 [{work_id}]{f' (第{chapter}章)' if chapter is not None else ''} 的超凡能力。[/yellow]")
        return

    title = f"角色 [{character}] 超凡能力与技能清单"
    if chapter is not None:
        title += f"（时间锚点：第 {chapter} 章）"
    table = Table(title=title)
    table.add_column("能力名称", style="cyan", no_wrap=True)
    table.add_column("体系类别", style="yellow", no_wrap=True)
    table.add_column("序列号", style="magenta", justify="center")
    table.add_column("生效章节", style="blue", justify="center")
    table.add_column("能力效果与设定说明", style="green")

    for ab in abilities:
        valid_range = f"第 {ab['valid_from_chapter']} 章起"
        if ab.get("valid_to_chapter"):
            valid_range += f" 至第 {ab['valid_to_chapter']} 章"
        table.add_row(
            ab["ability_name"],
            ab["category"],
            ab["sequence_num"] or "-",
            valid_range,
            ab["effect_description"],
        )

    console.print(table)


@app.command("mutations")
def list_mutations_cmd(
    work_id: str = typer.Argument(..., help="作品 ID"),
    chapter: Optional[int] = typer.Option(None, "--chapter", "-c", help="查询该章节及之前生效的变动"),
    character: Optional[str] = typer.Option(None, "--character", "-p", help="筛选指定受影响角色"),
):
    """查看同人作品中立项登记的所有剧情变动、蝴蝶效应与提前觉醒台账"""
    from fxi.domain.mutation_ledger import MutationLedger

    ledger = MutationLedger()
    mutations = ledger.list_mutations(work_id, chapter=chapter, entity_name_or_id=character, status="active")
    if not mutations:
        console.print(f"[yellow]作品 [{work_id}] 暂无登记的同人因果变动记录{f' (截至第{chapter}章)' if chapter is not None else ''}。[/yellow]")
        return

    title = f"作品 [{work_id}] 同人因果变动账本 (Mutation Ledger)"
    if chapter is not None:
        title += f"（生效范围：截至第 {chapter} 章）"
    table = Table(title=title)
    table.add_column("变动标识", style="cyan", no_wrap=True)
    table.add_column("触发章节", style="magenta", justify="center")
    table.add_column("受影响角色", style="yellow", no_wrap=True)
    table.add_column("变动类型", style="blue")
    table.add_column("变动目标", style="green", no_wrap=True)
    table.add_column("原著章节", style="white", justify="center")
    table.add_column("因果来源事件 (Cause Event)", style="white")

    for m in mutations:
        canon_ch = f"第 {m.original_canon_chapter} 章" if m.original_canon_chapter else "-"
        table.add_row(
            m.mutation_id,
            f"第 {m.trigger_chapter} 章",
            m.entity_id,
            m.mutation_type,
            m.target_name,
            canon_ch,
            m.cause_event,
        )

    console.print(table)


@app.command("add-mutation")
def add_mutation_cmd(
    work_id: str = typer.Argument(..., help="作品 ID"),
    chapter: int = typer.Option(..., "--chapter", "-c", help="触发该变动的同人章节序号"),
    character: str = typer.Option(..., "--character", "-p", help="受影响角色"),
    mutation_type: str = typer.Option("ability_grant", "--type", "-t", help="变动类型 (ability_grant | ability_modify | ability_suppress)"),
    target: str = typer.Option(..., "--target", help="变动目标名称"),
    cause: str = typer.Option(..., "--cause", help="因果来源事件说明"),
    canon_chapter: Optional[int] = typer.Option(None, "--canon-chapter", help="原著原本解锁章节（可选）"),
):
    """为同人作品立项登记一条因果变动事件（立账）"""
    from fxi.domain.mutation_ledger import MutationLedger

    ledger = MutationLedger()
    mutation = ledger.record_mutation(
        work_id=work_id,
        trigger_chapter=chapter,
        cause_event=cause,
        entity_name_or_id=character,
        mutation_type=mutation_type,
        target_name=target,
        original_canon_chapter=canon_chapter,
    )
    console.print(f"[bold green]✓ 成功立项同人因果变动：[/bold green][cyan]{mutation.mutation_id}[/cyan]")
    console.print(f"  角色: {character} | 触发章节: 第 {chapter} 章 | 目标: {target} | 因果: {cause}")


@app.command("effective-state")
def show_effective_state(
    work_id: str = typer.Argument(..., help="作品 ID"),
    character: str = typer.Argument(..., help="角色名称"),
    chapter: int = typer.Option(..., "--chapter", "-c", help="时间锚点章节（如 12）"),
    base_work: Optional[str] = typer.Option(None, "--base-work", help="原著底座作品 ID"),
):
    """查询同人作品在特定章节叠加蝴蝶效应后的真实有效超凡能力集 (EffectiveState)"""
    mgr = EntityManager()
    state = mgr.get_effective_state(work_id, character, chapter=chapter, base_work_id=base_work)
    abilities = state["effective_abilities"]
    if not abilities:
        console.print(f"[yellow]未查询到角色 [{character}] 在第 {chapter} 章的有效能力。[/yellow]")
        return

    title = f"角色 [{state['name']}] 第 {chapter} 章真实有效能力集 (Effective State)"
    table = Table(title=title)
    table.add_column("能力名称", style="cyan", no_wrap=True)
    table.add_column("类别", style="yellow", no_wrap=True)
    table.add_column("状态来源", style="magenta")
    table.add_column("因果来源 / 解锁渊源", style="green")
    table.add_column("能力效果说明", style="white")

    for ab in abilities:
        if ab.get("is_mutated"):
            status_style = "[bold magenta]★ 同人变动[/bold magenta]"
            origin_desc = f"[bold green]{ab.get('origin_label', '')}[/bold green]"
        else:
            status_style = "[blue]原著基线[/blue]"
            origin_desc = ab.get("origin_label", "")

        table.add_row(
            ab["ability_name"],
            ab["category"],
            status_style,
            origin_desc,
            ab.get("effect_description", "")[:60],
        )

    console.print(table)
    console.print(f"统计：原著基准能力 [blue]{state['canon_abilities_count']}[/blue] 项 | 叠加同人变动 [magenta]{state['mutations_applied_count']}[/magenta] 项 | 最终有效能力 [bold green]{len(abilities)}[/bold green] 项")


@app.command("style")
def show_style(
    work_id: str = typer.Argument(..., help="作品 ID"),
):
    """查看某部作品已提炼的作者文风画像、对白攻防、叙事步频与负向禁令"""
    import yaml
    cfg = load_config()
    style_dir = cfg.projects_dir / work_id / "style"
    if not style_dir.is_dir():
        console.print(f"[yellow]作品 [{work_id}] 尚未提炼文笔风格档案 (目录不存在: {style_dir})[/yellow]")
        return

    profile_file = style_dir / "style_profile.yaml"
    playbook_file = style_dir / "craft_playbook.yaml"
    anti_file = style_dir / "anti_patterns.yaml"

    if not profile_file.is_file() and not playbook_file.is_file():
        console.print(f"[yellow]作品 [{work_id}] 暂无文风文件。[/yellow]")
        return

    profile = yaml.safe_load(profile_file.read_text(encoding="utf-8")) if profile_file.is_file() else {}
    playbook = yaml.safe_load(playbook_file.read_text(encoding="utf-8")) if playbook_file.is_file() else {}
    anti = yaml.safe_load(anti_file.read_text(encoding="utf-8")) if anti_file.is_file() else {}

    author = playbook.get("author_name") or "原著作者"
    tagline = playbook.get("style_tagline") or "未定义"

    console.print(Panel(
        f"[bold cyan]作者:[/bold cyan] {author}\n"
        f"[bold cyan]文风定位:[/bold cyan] [italic yellow]{tagline}[/italic yellow]",
        title=f"【{work_id}】作者文风与技法画像总览",
        border_style="green",
    ))

    # 1. 叙事节奏与分镜步频
    cadence = profile.get("narrative_cadence", {})
    if cadence:
        t_cad = Table(title="一、 叙事节奏与镜头步频 (Narrative Cadence)", show_header=True)
        t_cad.add_column("维度", style="cyan", width=18)
        t_cad.add_column("规约准则", style="green")
        for k, v in cadence.items():
            if isinstance(v, (str, int, float)):
                t_cad.add_row(str(k), str(v))
        console.print(t_cad)

    # 2. 对白攻防公式
    dialogue = playbook.get("dialogue_dynamics", {})
    if dialogue:
        formula = dialogue.get("formula", "")
        rules = dialogue.get("rules", [])
        content = f"[bold yellow]四步攻防公式:[/bold yellow] {formula}\n\n[bold yellow]对白法则:[/bold yellow]\n"
        content += "\n".join([f"• {r}" for r in rules[:4]])
        console.print(Panel(content, title="二、 对白攻防与脱敏法则 (Dialogue Dynamics)", border_style="cyan"))

    # 3. 具象比喻偏好
    metaphors = profile.get("metaphors", {})
    examples = metaphors.get("examples", [])
    if examples:
        t_meta = Table(title="三、 具象比喻示范 (Preferred Metaphors)", show_header=True)
        t_meta.add_column("原著特色比喻标本", style="yellow")
        for ex in examples[:5]:
            t_meta.add_row(str(ex))
        console.print(t_meta)

    # 4. 负向禁令与 AI 惯性词绝对封杀
    banned_ai = profile.get("lexicon_governance", {}).get("universal_ai_cliches_banned", [])
    banned_genre = profile.get("lexicon_governance", {}).get("genre_anachronisms_banned", [])
    t_ban = Table(title="四、 严格负向禁写与词汇治理 (Prohibited Lexicon)", show_header=True)
    t_ban.add_column("封杀类别", style="red", width=22)
    t_ban.add_column("禁止清单 (Prompt 严禁出现 & 审稿自动打回)", style="white")
    if banned_ai:
        t_ban.add_row("通用 AI 油腻词/伪具体", "、".join(banned_ai[:12]) + " 等")
    if banned_genre:
        t_ban.add_row("世界观跨界错位词", "、".join(banned_genre[:12]) + " 等")
    console.print(t_ban)


@app.command("audit-style")
def audit_style(
    work_id: str = typer.Argument(..., help="作品 ID"),
):
    """审计作品文风画像与原著底本的一致性，自动检测'判断错误/误伤原著词'与'大模型虚构幻觉'"""
    import yaml
    from fxi.index_retrieval.jieba_fts import ChineseFTS
    cfg = load_config()
    style_dir = cfg.projects_dir / work_id / "style"
    profile_file = style_dir / "style_profile.yaml"
    if not profile_file.is_file():
        console.print(f"[yellow]未找到作品 [{work_id}] 的 style_profile.yaml[/yellow]")
        return

    profile = yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
    fts = ChineseFTS(cfg)

    console.print(f"[cyan]正在对作品 [[bold]{work_id}[/bold]] 的文风规约执行原著底本逆向交叉审计...[/cyan]\n")

    # 1. 审计禁词表：检查是否有“禁止词在原著中高频出现”（学习判断错误）
    lexicon = profile.get("lexicon_governance", {})
    banned_genre = lexicon.get("genre_anachronisms_banned", [])
    banned_ai = lexicon.get("universal_ai_cliches_banned", [])
    all_banned = banned_genre + banned_ai

    t_audit_ban = Table(title="【审计项 1】禁止词有效性校验 (检测是否误伤原著词汇)")
    t_audit_ban.add_column("检查禁止词", style="cyan", width=18)
    t_audit_ban.add_column("原著命中数", style="magenta", justify="center", width=12)
    t_audit_ban.add_column("审计判定", style="green")

    conflict_count = 0
    clean_count = 0
    for word in all_banned:
        hits = fts.search(word, work_id=work_id, limit=20)
        hit_count = len(hits)
        if hit_count >= 10:
            conflict_count += 1
            t_audit_ban.add_row(
                word,
                f"[bold red]{hit_count}+[/bold red]",
                "[bold red]❌ 判定错误/误伤原著！该词在原著中高频出现，不得列为禁词！[/bold red]"
            )
        elif hit_count > 0:
            clean_count += 1
            t_audit_ban.add_row(
                word,
                f"[yellow]{hit_count}[/yellow]",
                "[yellow]⚠ 低频出现（偶发/戏谑），作为禁词合规[/yellow]"
            )
        else:
            clean_count += 1
            t_audit_ban.add_row(
                word,
                "0",
                "[green]✔ 原著零出现，禁词判定精准[/green]"
            )
    console.print(t_audit_ban)

    # 2. 审计比喻范例：检查是否虚构（是否真实来自原著底本）
    metaphors = profile.get("metaphors", {}).get("examples", [])
    if metaphors:
        t_meta_audit = Table(title="\n【审计项 2】特色比喻真实性校验 (检测大模型是否虚构幻觉)")
        t_meta_audit.add_column("比喻特征词", style="cyan", width=24)
        t_meta_audit.add_column("底本溯源", style="green")
        for m in metaphors[:5]:
            # 范例来自作品级抽取结果；审计不能用某一作品的示例词猜关键词。
            # 直接使用抽取范例让 FTS 自己分词，未命中时仍保留可诊断的原文。
            kw = str(m).strip()
            hits = fts.search(kw, work_id=work_id, limit=3)
            if hits:
                t_meta_audit.add_row(m[:35] + "...", f"[green]✔ 底本已溯源 (命中 {len(hits)} 处原著场景)[/green]")
            else:
                t_meta_audit.add_row(m[:35] + "...", "[yellow]⚠ 未在原著正文完全匹配，属于概括式范例[/yellow]")
        console.print(t_meta_audit)

    console.print(f"\n[bold green]✔ 审计完成！[/bold green] 合规禁词: {clean_count} 条，冲突误伤: {conflict_count} 条。")


@app.command("import")
def import_source(
    path: str = typer.Argument(..., help="章节文件目录或单文件路径"),
    work_id: str = typer.Option(..., "--work-id", "-w", help="作品 ID"),
    title: str = typer.Option(..., "--title", "-t", help="作品标题"),
    limit: int = typer.Option(None, "--limit", "-n", help="最大导入章节数")
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


@app.command("extract")
def extract_work(
    work_id: str = typer.Argument(..., help="需要自动解析入库的作品 ID"),
    chapters: int = typer.Option(20, "--chapters", "-n", help="分析的前 N 章"),
    source_id: Optional[str] = typer.Option(None, "--source-id", help="来源 ID；多来源作品必须显式指定"),
    evaluation_ref: Optional[str] = typer.Option(None, "--evaluation-ref", help="受控评测结果引用"),
    submitted_by: Optional[str] = typer.Option(None, "--submitted-by", help="受控提交身份"),
):
    """使用配置的大模型自动解析作品要素、人物相态与同人分歧点 (POD)"""
    from fxi.sources.auto_extractor import UniversalAutoExtractor
    console.print(f"[cyan]正在启动大模型通用要素抽取器分析作品 [{work_id}] (前 {chapters} 章)...[/cyan]")
    extractor = UniversalAutoExtractor()
    try:
        binding = _resolve_extraction_binding(
            extractor.config,
            work_id,
            source_id=source_id,
            evaluation_ref=evaluation_ref,
            submitted_by=submitted_by,
            chapter_limit=chapters,
        )
        report = extractor.extract_and_ingest(
            work_id=work_id,
            sample_chapters=chapters,
            **binding,
        )
    except Exception as e:
        console.print(f"[bold red]抽取失败: {e}[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold green]✔ 通用解析完成，候选包已保存，等待人工审批；未写入正式知识！[/bold green]")
    console.print(f"  • 作品: {report['title']} ({report['work_id']})")
    console.print(f"  • 角色数: {len(report['characters'])}")
    for c in report['characters']:
        phases_str = f"{len(c['phases'])} 个阶段" if c['phases'] else "单阶段"
        console.print(f"    - [cyan]{c['name']}[/cyan] (相态: {phases_str})")
    console.print(f"  • 道具数: {len(report['items'])}")
    console.print(f"  • 因果事件数: {len(report['causal_events'])}")
    for ev in report['causal_events']:
        pod_mark = f" [bold magenta]🌟 同人分歧点 (POD): {ev['pod_analysis']}[/bold magenta]" if ev['is_pod'] else ""
        console.print(f"    - Order {ev['order']}: {ev['summary']}{pod_mark}")

    if report.get("voices"):
        console.print(f"  • [bold cyan]角色声线与台词档案 (Voice Profiles): {len(report['voices'])} 个[/bold cyan]")
        for v in report["voices"]:
            console.print(f"    - [green]{v['name']}[/green]: {v['voice_profile'].get('tone', '')}")

    if report.get("relationships"):
        console.print(f"  • [bold magenta]人际关系与深层张力 (Relationships & Tensions): {len(report['relationships'])} 组[/bold magenta]")
        for r in report["relationships"]:
            pair_str = " <-> ".join(r.get("pair", []))
            console.print(f"    - {pair_str} ({r.get('dynamic', '')}): [yellow]{r.get('tension', '')[:60]}...[/yellow]")

    if report.get("style_profile"):
        console.print("  • [bold blue]作者文风与具象比喻档案: 已包含在候选包中，待审批[/bold blue]")

    if report.get("continuity"):
        console.print(f"  • [bold green]章节连续性台账 (Continuity Ledger): {len(report['continuity'])} 章已包含在候选包中[/bold green]")
        for cnt in report["continuity"]:
            console.print(f"    - 第 {cnt['chapter_index']} 章末: 地点[{cnt['ending_location']}] 态势[{cnt['ending_situation'][:50]}...]")


@app.command("extract-all")
def extract_all_work(
    work_id: str = typer.Argument(..., help="需要全量分批解析的作品 ID"),
    batch_size: int = typer.Option(20, "--batch-size", help="每批章节数，默认 20"),
    provider: str = typer.Option("luna_local", "--provider", help="模型提供方"),
    model: str = typer.Option("gpt-5.6-luna", "--model", help="模型名称"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="是否从上次进度继续"),
    concurrency: int = typer.Option(4, "--concurrency", "-c", help="并发批次处理数，默认 4"),
    start: Optional[int] = typer.Option(None, "--start", "-s", help="起始章节号（从 1 开始，如 301）"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="最大处理章节数，如 300"),
    auto_ingest: bool = typer.Option(False, "--auto-ingest/--no-auto-ingest", help="仅对已审批候选尝试正式入库"),
    source_id: Optional[str] = typer.Option(None, "--source-id", help="来源 ID；多来源作品必须显式指定"),
    evaluation_ref: Optional[str] = typer.Option(None, "--evaluation-ref", help="受控评测结果引用"),
    submitted_by: Optional[str] = typer.Option(None, "--submitted-by", help="受控提交身份"),
):
    """使用指定模型分批抽取同人文所需结构化知识，支持多并发提速与实时进度仪表盘。"""
    from fxi.sources.batch_extractor import LunaBatchExtractor

    if start and limit:
        scope_desc = f"第 {start} 章起，共 {limit} 章"
    elif start:
        scope_desc = f"第 {start} 章起至全本完"
    elif limit:
        scope_desc = f"前 {limit} 章"
    else:
        scope_desc = "全书全量"

    console.print(
        f"[cyan]开始分批解析作品 [{work_id}] ({scope_desc})，模型 [{provider}/{model}]，"
        f"批大小 {batch_size}，并发数 {concurrency}，不抽取文风...[/cyan]"
    )
    extractor = LunaBatchExtractor()

    try:
        binding = _resolve_extraction_binding(
            extractor.config,
            work_id,
            source_id=source_id,
            evaluation_ref=evaluation_ref,
            submitted_by=submitted_by,
            chapter_start=start,
            chapter_limit=limit,
        )
    except Exception as exc:
        console.print(f"[bold red]抽取范围绑定失败: {exc}[/bold red]")
        raise typer.Exit(code=1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}[/bold cyan]"),
        BarColumn(bar_width=24, style="grey37", complete_style="green"),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TextColumn("• [dim]耗时:[/dim]"),
        TimeElapsedColumn(),
        TextColumn("• [dim]预估剩余:[/dim]"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task(f"分批解析 [{work_id}]", total=None)

        def on_progress(completed_count: int, total_batches: int, ch_range: tuple[int, int], counts: dict[str, int], event: str):
            if progress.tasks[task_id].total is None or progress.tasks[task_id].total != total_batches:
                progress.update(task_id, total=total_batches)
            progress.update(task_id, completed=completed_count)
            if event == "batch_done":
                s_ch, e_ch = ch_range
                progress.console.print(
                    f"  [bold green]✔[/bold green] 批次完成 [{completed_count}/{total_batches}] (第 {s_ch}~{e_ch} 章): "
                    f"+{counts.get('characters', 0)} 人物, +{counts.get('items', 0)} 道具, "
                    f"+{counts.get('continuity', 0)} 连续性, +{counts.get('events', 0)} 事件"
                )

        try:
            report = extractor.extract_all_batched(
                work_id=work_id,
                batch_size=batch_size,
                provider_override=provider,
                model_override=model,
                resume=resume,
                max_concurrency=concurrency,
                start_chapter=start,
                max_chapters=limit,
                on_progress=on_progress,
                **binding,
            )
        except Exception as exc:
            console.print(f"[bold red]抽取失败，已保留已完成批次: {exc}[/bold red]")
            raise typer.Exit(code=1)

    console.print("[bold green]✔ 分批解析完成！[/bold green]")
    console.print(f"  • 作品: {report.title} ({report.work_id})")
    console.print(f"  • 批次: {report.completed_batches}/{report.total_batches}")
    console.print(f"  • 人物候选: {report.characters}")
    console.print(f"  • 道具候选: {report.items}")
    console.print(f"  • 因果事件: {report.events}")
    console.print(f"  • 人物关系: {report.relationships}")
    console.print(f"  • 章节连续性: {report.continuity}")

    if auto_ingest:
        console.print("\n[cyan]正在检查候选审批状态；只有已审批候选才允许正式入库...[/cyan]")
        try:
            ingest_report = extractor.ingest_candidates_to_formal(work_id)
        except Exception as exc:
            console.print(f"[bold red]正式入库失败: {exc}[/bold red]")
            raise typer.Exit(code=1)
        if ingest_report.get("status") != "SUCCESS" or not ingest_report.get("formal_knowledge_written", False):
            console.print(
                f"[bold yellow]未执行正式入库: {ingest_report.get('message', '候选未通过审批或入库失败')}[/bold yellow]"
            )
            raise typer.Exit(code=2)
        console.print("[bold green]✔ 正式入库完成！[/bold green]")
        console.print(f"  • 角色卡: {ingest_report.get('characters', 0)} 个 (Markdown frontmatter + Trinity)")
        console.print(f"  • 性格相态: {ingest_report.get('phases', 0)} 个 (SQLite entity_phases)")
        console.print(f"  • 道具卡: {ingest_report.get('items', 0)} 个 (Markdown frontmatter)")
        console.print(f"  • 人际关系: {ingest_report.get('relationships', 0)} 组 (relationships.yaml)")
        console.print(f"  • 超凡能力: {ingest_report.get('abilities', 0)} 条已索引入库 (SQLite entity_abilities)")
        console.print(f"  • 连续性台账: {ingest_report.get('continuity', 0)} 章已沉淀 SQLite")
        console.print(f"  • 因果事件: {ingest_report.get('causal_events', 0)} 个已注册 DAG")


@app.command("ingest-candidates")
def ingest_candidates(
    work_id: str = typer.Argument(..., help="作品 ID"),
):
    """将 CandidateStore 中的批量抽取候选规整聚合并写入正式 Markdown 实体与 SQLite"""
    from fxi.sources.batch_extractor import LunaBatchExtractor

    extractor = LunaBatchExtractor()
    console.print(f"[cyan]正在为作品 [[bold]{work_id}[/bold]] 聚合候选并正式入库...[/cyan]")
    try:
        report = extractor.ingest_candidates_to_formal(work_id)
        if report.get("status") != "SUCCESS" or not report.get("formal_knowledge_written", False):
            console.print(
                f"[bold yellow]未执行正式入库: {report.get('message', '候选未通过审批或入库失败')}[/bold yellow]"
            )
            raise typer.Exit(code=2)
        console.print(f"[bold green]✔ 正式入库成功！[/bold green]")
        console.print(f"  • 角色卡: {report.get('characters', 0)} 个")
        console.print(f"  • 性格相态: {report.get('phases', 0)} 个")
        console.print(f"  • 超凡能力: {report.get('abilities', 0)} 条")
        console.print(f"  • 道具卡: {report.get('items', 0)} 个")
        console.print(f"  • 人际关系: {report.get('relationships', 0)} 组")
        console.print(f"  • 连续性台账: {report.get('continuity', 0)} 章")
        console.print(f"  • 因果事件: {report.get('causal_events', 0)} 个")
    except Exception as exc:
        console.print(f"[bold red]入库失败: {exc}[/bold red]")
        raise typer.Exit(code=1)


class ClearParsingError(RuntimeError):
    """清理已建立备份但后续步骤失败，必须向调用方暴露恢复位置。"""

    def __init__(self, message: str, backup_dir: Path):
        super().__init__(message)
        self.backup_dir = backup_dir


_CLEAR_TABLES = (
    "entity_abilities",
    "entity_phases",
    "entity_relations",
    "entities",
    "causal_links",
    "causal_events",
    "chapter_continuity",
    "fts_scenes",
)


def _safe_work_path(root: Path, work_id: str, *parts: str) -> Path:
    """拼接并验证工作区内路径，拒绝路径穿越和绝对片段。"""
    root_resolved = root.resolve()
    candidate = (root_resolved / work_id / Path(*parts)).resolve()
    if candidate == root_resolved or root_resolved not in candidate.parents:
        raise ValueError(f"路径越出受控根目录: {candidate}")
    return candidate


def _clear_counts(config: FxiConfig, work_id: str) -> dict[str, int]:
    client = DatabaseClient(config.sqlite_path)
    with client.get_connection() as conn:
        return {
            table: int(
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE work_id = ?", (work_id,)).fetchone()[0]
            )
            for table in _CLEAR_TABLES
        }


def clear_parsing_work(
    config: FxiConfig,
    work_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """清理单个作品的派生解析事实，并保留可校验的完整快照。"""
    validated_work_id = validate_work_id(work_id)
    raw_dir = _safe_work_path(config.sources_dir, validated_work_id, "chapters")
    style_dir = _safe_work_path(config.projects_dir, validated_work_id, "style")
    entities_dir = _safe_work_path(config.projects_dir, validated_work_id, "entities")
    state_file = _safe_work_path(config.data_dir, validated_work_id + ".luna-extraction.json")
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"作品 [{validated_work_id}] 的章节目录不存在: {raw_dir}")

    counts = _clear_counts(config, validated_work_id)
    plan = {
        "status": "DRY_RUN" if dry_run else "PLANNED",
        "work_id": validated_work_id,
        "raw_count": sum(1 for item in raw_dir.rglob("*") if item.is_file()),
        "style_count": sum(1 for item in style_dir.rglob("*") if item.is_file()) if style_dir.is_dir() else 0,
        "counts": counts,
        "entities_dir": str(entities_dir),
    }
    if dry_run:
        return plan

    backup_root = _safe_work_path(config.data_dir, "backups")
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = _safe_work_path(
        config.data_dir,
        "backups",
        f"{validated_work_id}_pre_clear_{timestamp}_{time.time_ns()}",
    )
    try:
        backup_dir = BackupManager(config).create_snapshot(backup_dir)
    except Exception as exc:
        raise RuntimeError(f"清理前备份失败，未执行任何清理: {backup_dir}") from exc

    try:
        db_client = DatabaseClient(config.sqlite_path)
        deleted_counts: dict[str, int] = {}
        with db_client.transaction() as cur:
            for table in _CLEAR_TABLES:
                result = cur.execute(f"DELETE FROM {table} WHERE work_id = ?", (validated_work_id,))
                deleted_counts[table] = result.rowcount

        cleaned_files = 0
        if entities_dir.is_dir():
            for path in sorted(entities_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                    cleaned_files += 1
        if state_file.is_file() or state_file.is_symlink():
            state_file.unlink()

        return {
            "status": "SUCCESS",
            "work_id": validated_work_id,
            "backup_dir": str(backup_dir),
            "deleted_counts": deleted_counts,
            "cleaned_files": cleaned_files,
            "raw_count": plan["raw_count"],
            "style_count": plan["style_count"],
        }
    except Exception as exc:
        raise ClearParsingError(
            f"作品 [{validated_work_id}] 清理失败；请使用备份恢复: {backup_dir}",
            backup_dir,
        ) from exc


@app.command("clear-parsing")
def clear_parsing_command(
    work_id: str = typer.Argument(..., help="需要清空作品解析事实的作品 ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接执行清空"),
):
    """安全清空解析事实；原文、文风和完整快照均受保护。"""
    cfg = load_config()
    try:
        plan = clear_parsing_work(cfg, work_id, dry_run=True)
    except (ValueError, FileNotFoundError, OSError) as exc:
        console.print(f"[bold red]清理前检查失败: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]✔ 原文底本受保护：{plan['raw_count']} 个文件[/green]")
    console.print(f"[green]✔ 文风学习资产受保护：{plan['style_count']} 个文件[/green]")
    if not yes and not typer.confirm(
        f"确定要清空作品 [{plan['work_id']}] 的解析事实吗？原文与文风资产将保留。"
    ):
        console.print("[yellow]操作已取消[/yellow]")
        raise typer.Exit(code=0)

    try:
        report = clear_parsing_work(cfg, plan["work_id"])
    except ClearParsingError as exc:
        console.print(f"[bold red]清理失败: {exc}[/bold red]")
        console.print(f"[yellow]可恢复备份: {exc.backup_dir}[/yellow]")
        raise typer.Exit(code=1) from exc

    console.print(f"[bold green]✔ 作品 [{report['work_id']}] 解析事实安全清空完成！[/bold green]")
    console.print(f"  • 可恢复备份: {report['backup_dir']}")
    for table, count in report["deleted_counts"].items():
        console.print(f"  • SQLite/FTS {table}: 清理 {count} 条记录")
    console.print(f"  • 实体文件: 清理 {report['cleaned_files']} 个卡片")
