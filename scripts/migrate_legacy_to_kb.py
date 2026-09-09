"""Deprecated one-off migration helpers.

The maintained import/extract workflows are the only supported production
entries.  This module remains importable for an explicitly supervised legacy
migration, but its historical command-line driver is disabled so it cannot
silently select workspace paths, works, or overwrite style projections.
Removal target: 2026-12.
"""

import json
import time
from pathlib import Path
from typing import Optional
import yaml
from rich.console import Console

from fxi.storage.sqlite_client import DatabaseClient
from fxi.materials_skills.style_canonical import CanonicalRuleMapper

console = Console()


def migrate_source_yaml(db: DatabaseClient, yaml_path: Path, work_id: str) -> int:
    """将庞大的 source.yaml 解析并入库至 source_chapters 表"""
    if not yaml_path.exists():
        console.print(f"[yellow]文件不存在，跳过: {yaml_path}[/yellow]")
        return 0

    console.print(f"正在读取并解析: [cyan]{yaml_path}[/cyan] ({yaml_path.stat().st_size // 1024} KB)...")
    t0 = time.time()
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        console.print(f"[red]解析失败，格式非字典: {yaml_path}[/red]")
        return 0

    chapters = data.get("chapters", [])
    if not isinstance(chapters, list):
        console.print(f"[red]未找到 chapters 列表[/red]")
        return 0

    records = []
    for item in chapters:
        ch_idx = item.get("index") or item.get("chapter_index")
        if ch_idx is None:
            continue
        records.append({
            "chapter_index": int(ch_idx),
            "chapter_title": item.get("title") or f"第{ch_idx}章",
            "char_count": item.get("word_count") or item.get("char_count") or 0,
            "sha256": item.get("sha256") or item.get("hash") or "",
            "file_path": item.get("path") or item.get("file_path") or f"chapters/ch{ch_idx:03d}.md",
            "is_analyzed": False,
        })

    inserted = db.upsert_source_chapters(work_id, records)
    elapsed = time.time() - t0
    console.print(f"[bold green]✓ 成功迁移 {inserted} 章节至 source_chapters (耗时: {elapsed:.2f}s)[/bold green]")
    return inserted


def migrate_style_package(
    db: DatabaseClient,
    package_path: Path,
    profile_path: Optional[Path],
    work_id: str,
    author: str,
) -> tuple[int, int]:
    """
    将 1.8 万行的 candidate_style_package.json 规则进行语义归一与聚类去重，沉淀入库
    """
    if not package_path.exists():
        console.print(f"[yellow]风格包不存在: {package_path}[/yellow]")
        return 0, 0

    orig_size = package_path.stat().st_size
    console.print(f"正在分析并归一风格包: [cyan]{package_path}[/cyan] ({orig_size // 1024} KB)...")
    t0 = time.time()

    with open(package_path, "r", encoding="utf-8") as f:
        pkg = json.load(f)

    raw_rules = pkg.get("rules", [])
    orig_rule_count = len(raw_rules)

    # 1. 清理已有并重新执行语义归一化与证据指针入库
    with db.transaction() as cur:
        cur.execute("DELETE FROM style_evidences WHERE work_id = ?", (work_id,))
        cur.execute("DELETE FROM style_rules WHERE work_id = ?", (work_id,))

    rule_ids_created = set()
    evidence_count = 0
    for r in raw_rules:
        r_id = r.get("rule_id", "")
        cat = r.get("category", "syntax")
        method = r.get("method", "")
        anti = r.get("anti_pattern", "")
        supp_chapters = r.get("supporting_chapters", [1])

        canonical = CanonicalRuleMapper.map_to_canonical(
            raw_key=r_id,
            category=cat,
            instruction=method,
            anti_pattern=anti,
            scene_scope="ALL",
        )

        for ch in supp_chapters:
            rule_entry = {
                "canonical_key": canonical["canonical_key"],
                "category": canonical["category"],
                "scene_scope": canonical["scene_scope"],
                "instruction": canonical["instruction"],
                "anti_pattern": canonical["anti_pattern"],
                "chapter_index": ch,
            }
            db_rule_id = db.upsert_style_rule(work_id, rule_entry)
            rule_ids_created.add(db_rule_id)

            # 写入证据指针
            for ev in r.get("evidence", []):
                quote = ev.get("quote") or ev.get("excerpt_hash", "")[:16]
                db.add_style_evidence(
                    rule_id=db_rule_id,
                    work_id=work_id,
                    chapter_index=ch,
                    quote=quote,
                    offset_start=ev.get("start_char"),
                    offset_end=ev.get("end_char"),
                )
                evidence_count += 1

    # 2. 迁移数理画像
    if profile_path and profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_data = json.load(f)
        db.save_style_profile(
            work_id=work_id,
            author=author,
            metrics=profile_data,
            lexicon_features={},
        )

    # 3. 标记已学习章节为 is_analyzed=1
    training_range = pkg.get("training_range", {})
    start_ch = training_range.get("start", 1)
    end_ch = training_range.get("end", 50)
    for ch in range(start_ch, end_ch + 1):
        db.upsert_source_chapters(work_id, [{"chapter_index": ch, "is_analyzed": True}])

    elapsed = time.time() - t0
    dedup_count = len(rule_ids_created)
    console.print(
        f"[bold green]✓ 风格规则迁移完成！原始规则: {orig_rule_count} 条 -> 归一白金法则: {dedup_count} 条 "
        f"(证据指针: {evidence_count} 个, 耗时: {elapsed:.2f}s)[/bold green]"
    )

    # 4. 生成紧凑版 JSON (替代原先 1.8 万行单体文件，解决用户使用痛点)
    compact_path = package_path.parent / "candidate_style_package_compact.json"
    compact_payload = dict(pkg)
    compact_payload["examples"] = pkg.get("examples", [])[:5]  # 只保留少量代表性例句
    compact_payload["rules"] = raw_rules[:dedup_count]
    with open(compact_path, "w", encoding="utf-8") as f:
        json.dump(compact_payload, f, ensure_ascii=False, indent=2)
    compact_size = compact_path.stat().st_size
    console.print(f"[bold cyan]✓ 生成紧凑版候选包: {compact_path.name} ({compact_size // 1024} KB, 压缩比: {(1 - compact_size / orig_size) * 100:.1f}%)[/bold cyan]")

    return orig_rule_count, dedup_count


def main():
    raise SystemExit(
        "migrate_legacy_to_kb.py 已弃用（计划 2026-12 移除）；"
        "请使用 project import/extract，并由管理员显式调用迁移函数。"
    )


if __name__ == "__main__":
    main()
