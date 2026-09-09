"""
fxi.tools.bootstrap_project - 显式来源的项目冷启动基线沉淀工具

读取前序章节与规划资产，在 Fxi 中沉淀为不可变快照、实体基线、连续性台账与 knowledge-v0 初始头指针。
调用方必须显式提供章节范围；不再隐式假定某部作品或固定章节。
"""

import argparse
import json
from pathlib import Path
import re
import secrets
from typing import Any, Iterable, Optional
import yaml

from fxi.api.registry import WorkRegistry
from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.core.exceptions import CorruptedDataError
from fxi.domain.entities import EntityManager
from fxi.sources.evidence_store import EvidenceStore
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
from fxi.storage.versioned_store import SourceDocumentInput
from fxi.timeline.continuity import ContinuityManager


def _safe_id(val: str) -> str:
    if not val or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", val):
        raise ValueError(f"非法标识符: {val}")
    return val


def _read_json_safe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CorruptedDataError(f"规划文件不可读或不是合法 JSON: {path}") from exc


def bootstrap_work(
    config: FxiConfig,
    work_id: str,
    source_root: Path,
    *,
    source_id: Optional[str] = None,
    chapter_indices: Iterable[int] = (),
    author_id: str = "default_author",
) -> dict[str, Any]:
    """将指定目录下的前序章节冷启动为 Fxi knowledge-v0 基线。"""
    work_id = _safe_id(work_id)
    source_id = _safe_id(source_id or work_id)
    indices = tuple(sorted(int(i) for i in chapter_indices))
    if not indices or any(i < 1 for i in indices):
        raise ValueError("chapter_indices 必须为正整数列表")

    db_client = DatabaseClient(config.sqlite_path)
    entity_mgr = EntityManager(config)
    continuity_mgr = ContinuityManager(config)
    evidence_store = EvidenceStore(config.sources_dir)

    # 1. 确保目录与 work.yaml
    work_dir = config.projects_dir / work_id
    work_dir.mkdir(parents=True, exist_ok=True)
    work_yaml_path = work_dir / "work.yaml"
    if not work_yaml_path.is_file():
        work_yaml_path.write_text(
            yaml.dump(
                {
                    "work_id": work_id,
                    "title": work_id,
                    "author": author_id,
                    "continuity_rules": [],
                    "ooc_rules": [],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

    # 2. 准备 sources 目录
    target_source_dir = config.sources_dir / source_id
    target_chapters_dir = target_source_dir / "chapters"
    target_chapters_dir.mkdir(parents=True, exist_ok=True)

    extracted_entities: dict[str, dict[str, Any]] = {}
    doc_inputs: list[SourceDocumentInput] = []
    combined_content = []

    # 3. 扫描并收集章节
    for idx in indices:
        candidate_paths = [
            source_root / "chapters" / f"ch_{idx:02d}" / f"chapter_{idx:02d}.md",
            source_root / "chapters" / f"ch_{idx:02d}" / f"chapter_{idx}.md",
            source_root / f"ch_{idx:02d}" / f"chapter_{idx:02d}.md",
            source_root / f"ch_{idx:02d}" / f"chapter_{idx}.md",
            source_root / f"ch_{idx}" / f"chapter_{idx}.md",
            source_root / f"chapter_{idx:02d}.md",
            source_root / f"chapter_{idx}.md",
        ]
        chapter_file = next((p for p in candidate_paths if p.is_file()), None)
        if not chapter_file:
            raise FileNotFoundError(f"未找到第 {idx} 章的正文文件，检索路径: {candidate_paths}")

        text = chapter_file.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"第 {idx} 章正文内容为空")
        combined_content.append(text)

        # 写入 sources/<source_id>/chapters/ch{idx:03d}.md
        doc_rel_path = f"chapters/ch{idx:03d}.md"
        dest_file = target_source_dir / doc_rel_path
        dest_file.write_text(text, encoding="utf-8")

        doc_inputs.append(
            SourceDocumentInput.from_file(
                dest_file,
                document_id=f"chapter-{idx:02d}",
                chapter_index=idx,
                relative_path=doc_rel_path,
            )
        )

        # 读取可能存在的 chapter_plan.json
        plan_candidates = [
            chapter_file.parent / "chapter_plan.json",
            source_root / "chapters" / f"ch_{idx:02d}" / "chapter_plan.json",
            source_root / f"ch_{idx:02d}" / "chapter_plan.json",
        ]
        plan_path = next((p for p in plan_candidates if p.is_file()), None)
        plan_data = _read_json_safe(plan_path) if plan_path else {}

        # 抽取实体与连续性
        active_chars: list[str] = []
        # 缺少规划资产时只能记录“未知”，不能把默认文案写成故事事实。
        loc_name = ""
        ending_situation = ""
        scenes = plan_data.get("scenes", [])
        for sc in scenes if isinstance(scenes, list) else []:
            if not isinstance(sc, dict):
                continue
            fl = sc.get("fact_lock", {})
            chars = fl.get("allowed_characters", [])
            for c in chars if isinstance(chars, list) else []:
                c_str = str(c).strip()
                if c_str and c_str not in active_chars:
                    active_chars.append(c_str)
                if c_str and c_str not in extracted_entities:
                    extracted_entities[f"char_{c_str}"] = {
                        "entity_id": f"char_{c_str}",
                        "name": c_str,
                        "category": "character",
                    }
            loc = fl.get("location_id")
            if loc:
                loc_str = str(loc).strip()
                loc_name = loc_str
                if f"loc_{loc_str}" not in extracted_entities:
                    extracted_entities[f"loc_{loc_str}"] = {
                        "entity_id": f"loc_{loc_str}",
                        "name": loc_str,
                        "category": "location",
                    }
            props = fl.get("allowed_props", [])
            for pr in props if isinstance(props, list) else []:
                pr_str = str(pr).strip()
                if pr_str and f"item_{pr_str}" not in extracted_entities:
                    extracted_entities[f"item_{pr_str}"] = {
                        "entity_id": f"item_{pr_str}",
                        "name": pr_str,
                        "category": "item",
                    }

        beats = plan_data.get("chapter_beats", [])
        if beats and isinstance(beats, list) and isinstance(beats[-1], dict):
            ending_situation = str(beats[-1].get("content") or beats[-1].get("result") or ending_situation)

        tail_snippet = text.strip()[-300:] if len(text.strip()) > 300 else text.strip()

        # 写入 SQLite chapter_continuity
        continuity_mgr.record_chapter(
            work_id=work_id,
            chapter_index=idx,
            title=f"第{idx}章",
            ending_location=loc_name,
            active_characters=active_chars,
            ending_situation=ending_situation,
            unresolved_hooks=[],
            tail_snippet=tail_snippet,
        )

    # 4. 计算不可变来源版本并写入 source.yaml
    source_version = sha256_hex("\n---\n".join(combined_content))
    source_yaml_path = target_source_dir / "source.yaml"
    source_yaml_path.write_text(
        yaml.dump({"source_id": source_id, "version": source_version, "sha256": source_version}),
        encoding="utf-8",
    )

    # 5. 注册作品与来源至数据库
    with db_client.transaction() as cur:
        ensure_work(cur, work_id, owner_id=author_id)
    registry = WorkRegistry.from_database_client(db_client)
    registry.register_source(
        work_id=work_id,
        source_id=source_id,
        source_dir=source_id,
        source_version=source_version,
    )

    # 6. 生成不可变快照 (EvidenceStore + v2_source_snapshots)
    snapshot = evidence_store.create_snapshot(
        source_id=source_id,
        documents=doc_inputs,
        version=source_version,
        metadata={"source_version": source_version},
    )

    snapshot_id = f"snap_{secrets.token_hex(8)}"
    docs_json = json.dumps(
        [
            {
                "document_id": doc.document_id,
                "chapter_index": doc.chapter_index,
                "relative_path": doc.relative_path,
                "content_hash": doc.content_hash,
                "char_count": doc.char_count,
            }
            for doc in snapshot.documents
        ],
        ensure_ascii=False,
    )
    with db_client.transaction() as cur:
        cur.execute(
            """INSERT INTO v2_source_snapshots
            (snapshot_id, work_id, source_id, version, manifest_hash, documents_json, object_root, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(work_id, source_id, idempotency_key) DO UPDATE SET
                version = excluded.version,
                manifest_hash = excluded.manifest_hash,
                documents_json = excluded.documents_json,
                object_root = excluded.object_root""",
            (
                snapshot_id,
                work_id,
                source_id,
                source_version,
                snapshot.manifest_hash,
                docs_json,
                f"objects/{source_id}/{source_version}",
                f"bootstrap_{source_version[:16]}",
            ),
        )

        # 7. 设置初始工作区头指针为 knowledge-v0
        cur.execute(
            """INSERT INTO v2_work_heads
            (work_id, knowledge_version, chapter_version, updated_at)
            VALUES (?, 'knowledge-v0', ?, datetime('now'))
            ON CONFLICT(work_id) DO UPDATE SET
                knowledge_version = excluded.knowledge_version,
                chapter_version = excluded.chapter_version,
                updated_at = excluded.updated_at""",
            (work_id, f"chapter-v{indices[-1]}"),
        )

    # 8. 写入基线实体
    for ent in extracted_entities.values():
        entity_mgr.upsert_entity(
            work_id=work_id,
            entity_id=ent["entity_id"],
            name=ent["name"],
            category=ent["category"],
            is_unique=True,
            attributes={"baseline_version": "knowledge-v0"},
        )

    return {
        "status": "success",
        "work_id": work_id,
        "source_id": source_id,
        "source_version": source_version,
        "knowledge_version": "knowledge-v0",
        "chapter_version": f"chapter-v{indices[-1]}",
        "chapter_indices": list(indices),
        "entities_count": len(extracted_entities),
        "snapshot_id": snapshot_id,
    }


def main():
    parser = argparse.ArgumentParser(description="网文项目基线冷启动工具")
    parser.add_argument("--work-id", required=True, help="作品标识符")
    parser.add_argument("--source-root", required=True, type=Path, help="前序章源文件根目录")
    parser.add_argument("--chapters", nargs="+", type=int, required=True, help="待沉淀的章节序号")
    args = parser.parse_args()

    cfg = load_config()
    res = bootstrap_work(cfg, args.work_id, args.source_root, chapter_indices=args.chapters)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
