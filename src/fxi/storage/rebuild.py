"""
fxi.storage.rebuild - 纯文本单真理源一键全量重建引擎
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient, MANIFEST_SCHEMA_DDL
from fxi.storage.text_io import read_markdown_frontmatter
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.index_retrieval.project_lexicon import LexiconManager


@dataclass
class RebuildReport:
    elapsed_seconds: float
    works_count: int = 0
    entities_count: int = 0
    phases_count: int = 0
    chapters_count: int = 0
    skills_count: int = 0
    sources_count: int = 0
    warnings: list[str] = field(default_factory=list)


class Rebuilder:
    """从纯文本重建 SQLite 派生索引的一键全量构建器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.fts = ChineseFTS(self.config)
        self.lexicon_mgr = LexiconManager(self.config)

    def rebuild_all(self) -> RebuildReport:
        """执行完整重建流程"""
        start_time = time.time()
        report = RebuildReport(elapsed_seconds=0.0)

        # 1. 彻底重新初始化 Schema
        with self.db_client.transaction() as cur:
            # 清空主要派生表
            tables = [
                "entities", "entity_phases", "entity_relations",
                "item_prototypes", "item_instances", "item_ownership_events",
                "character_attributes", "skills_tree", "character_skills",
                "territory_ledgers", "territory_resources", "territory_buildings",
                "foreshadowing", "fts_scenes", "causal_events", "causal_links",
                "state_events", "state_snapshots"
            ]
            for t in tables:
                try:
                    cur.execute(f"DELETE FROM {t};")
                except Exception:
                    pass

        # 2. 扫描 projects/
        if self.config.projects_dir.is_dir():
            for work_dir in self.config.projects_dir.iterdir():
                if not work_dir.is_dir():
                    continue

                work_json_file = work_dir / "work.json"
                if work_json_file.is_file():
                    try:
                        work_data = json.loads(work_json_file.read_text(encoding="utf-8"))
                        work_id = work_data.get("work_id", work_dir.name)
                        owner_id = work_data.get("owner_id", "default_author")

                        with self.db_client.transaction() as cur:
                            cur.execute(
                                "INSERT OR REPLACE INTO authors (owner_id, slug, display_name, created_at) VALUES (?, ?, ?, ?)",
                                (owner_id, owner_id, owner_id, "2026-01-01T00:00:00Z")
                            )
                            cur.execute(
                                """
                                INSERT OR REPLACE INTO works
                                (work_id, owner_id, slug, title, genre_ids_json, divergence_anchor, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    work_id,
                                    owner_id,
                                    work_data.get("slug", work_id),
                                    work_data.get("title", work_id),
                                    json.dumps(work_data.get("genre_ids", [])),
                                    work_data.get("divergence_anchor"),
                                    work_data.get("created_at", "2026-01-01T00:00:00Z"),
                                    work_data.get("updated_at", "2026-01-01T00:00:00Z"),
                                )
                            )
                        report.works_count += 1
                    except Exception as e:
                        report.warnings.append(f"解析 work.json 失败 {work_json_file}: {e}")

                # 扫描 entities/
                entities_dir = work_dir / "entities"
                if entities_dir.is_dir():
                    for ent_file in entities_dir.rglob("*.md"):
                        try:
                            meta, body = read_markdown_frontmatter(ent_file)
                            ent_id = meta.get("entity_id", ent_file.stem)
                            category = meta.get("category", "character")
                            is_unique = 1 if meta.get("is_unique", True) else 0
                            name = meta.get("name", ent_file.stem)
                            aliases = json.dumps(meta.get("aliases", []))

                            with self.db_client.transaction() as cur:
                                cur.execute(
                                    """
                                    INSERT OR REPLACE INTO entities
                                    (entity_id, work_id, category, is_unique, name, aliases_json, attributes_yaml, file_path, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        ent_id,
                                        work_dir.name,
                                        category,
                                        is_unique,
                                        name,
                                        aliases,
                                        yaml.dump(meta.get("attributes", {}), allow_unicode=True),
                                        str(ent_file.relative_to(self.config.workspace_root)),
                                        time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    )
                                )

                                # 检查是否有性格阶段定义
                                phases = meta.get("phases", [])
                                for ph in phases:
                                    phase_id = ph.get("phase_id", "default")
                                    cur.execute(
                                        """
                                        INSERT OR REPLACE INTO entity_phases
                                        (phase_id, work_id, entity_id, phase_name, valid_from_order, valid_to_order, traits_json, anti_behaviors_json, tone_examples_json, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                        """,
                                        (
                                            phase_id,
                                            work_dir.name,
                                            ent_id,
                                            ph.get("phase_name", phase_id),
                                            ph.get("valid_from_order", 0),
                                            ph.get("valid_to_order"),
                                            json.dumps(ph.get("traits", [])),
                                            json.dumps(ph.get("anti_behaviors", [])),
                                            json.dumps(ph.get("tone_examples", [])),
                                            time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        )
                                    )
                                    report.phases_count += 1

                            report.entities_count += 1
                        except Exception as e:
                            report.warnings.append(f"解析实体文件失败 {ent_file}: {e}")

                # 扫描 chapters/
                chapters_dir = work_dir / "chapters"
                if chapters_dir.is_dir():
                    for ch_dir in chapters_dir.iterdir():
                        if not ch_dir.is_dir():
                            continue
                        draft_file = ch_dir / "draft.md"
                        if draft_file.is_file():
                            try:
                                meta, body = read_markdown_frontmatter(draft_file)
                                scene_uuid = meta.get("scene_uuid", f"sc_{work_dir.name}_{ch_dir.name}")
                                ch_idx = meta.get("chapter_index", 1)
                                self.fts.index_scene(scene_uuid, work_dir.name, ch_idx, body)
                                report.chapters_count += 1
                            except Exception as e:
                                report.warnings.append(f"索引章节失败 {draft_file}: {e}")

        # 3. 扫描 skills/
        if self.config.skills_dir.is_dir():
            for sk_dir in self.config.skills_dir.iterdir():
                if sk_dir.is_dir():
                    report.skills_count += 1

        # 4. 扫描 sources/
        if self.config.sources_dir.is_dir():
            for src_dir in self.config.sources_dir.iterdir():
                if not src_dir.is_dir():
                    continue
                scenes_dir = src_dir / "scenes"
                if scenes_dir.is_dir():
                    for sc_file in scenes_dir.glob("*.md"):
                        content = sc_file.read_text(encoding="utf-8")
                        self.fts.index_scene(sc_file.stem, src_dir.name, 1, content)
                report.sources_count += 1

        # 5. 导出并更新专用词表
        try:
            self.lexicon_mgr.export_lexicon()
        except Exception as e:
            report.warnings.append(f"词表导出告警: {e}")

        report.elapsed_seconds = round(time.time() - start_time, 3)
        return report
