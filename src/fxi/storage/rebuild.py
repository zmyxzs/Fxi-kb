"""
fxi.storage.rebuild - 纯文本单真理源一键全量重建引擎
"""

import json
import sqlite3
import time
from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence
from typing import Any, Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.core.canonical import sha256_hex
from fxi.ops.migration_report import MigrationReport
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
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
    cleared_tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "PASSED"
    selectors: tuple[str, ...] = ()
    projection_manifests: tuple[Mapping[str, Any], ...] = ()
    blockers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    migration_report: MigrationReport | None = None

    @property
    def success(self) -> bool:
        """重建完整成功的判断：无告警且无未捕获错误。"""
        return self.status == "PASSED" and not self.warnings and not self.blockers and not self.errors

    @property
    def report_hash(self) -> str:
        """Return a deterministic digest without making the report a fact source."""

        return sha256_hex(
            {
                "elapsed_seconds": self.elapsed_seconds,
                "works_count": self.works_count,
                "entities_count": self.entities_count,
                "phases_count": self.phases_count,
                "chapters_count": self.chapters_count,
                "skills_count": self.skills_count,
                "sources_count": self.sources_count,
                "cleared_tables": self.cleared_tables,
                "warnings": self.warnings,
                "status": self.status,
                "selectors": self.selectors,
                "projection_manifests": self.projection_manifests,
                "blockers": self.blockers,
                "errors": self.errors,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "works_count": self.works_count,
            "entities_count": self.entities_count,
            "phases_count": self.phases_count,
            "chapters_count": self.chapters_count,
            "skills_count": self.skills_count,
            "sources_count": self.sources_count,
            "cleared_tables": list(self.cleared_tables),
            "warnings": list(self.warnings),
            "status": self.status,
            "selectors": list(self.selectors),
            "projection_manifests": [dict(item) for item in self.projection_manifests],
            "blockers": list(self.blockers),
            "errors": list(self.errors),
            "report_hash": self.report_hash,
            "migration_report": self.migration_report.to_dict() if self.migration_report else None,
        }


class Rebuilder:
    """从纯文本重建 SQLite 派生索引的一键全量构建器"""

    _PRESERVED_ROOT_TABLES = frozenset({"authors", "works"})
    # v2 snapshots, proposals and commits are immutable workflow records, not
    # text-derived projections.  Rebuild must never erase their audit trail.
    _REBUILDABLE_TABLES = frozenset(
        {"entities", "entity_phases", "entity_relations", "fts_scenes"}
    )
    # These projections have no complete replay contract in the immutable
    # commit records yet.  Clearing them would turn a rebuild into silent data
    # loss, while retaining them after a source refresh could expose stale
    # facts.  Rebuild therefore fails closed before touching any table when
    # one of them contains data.
    _NON_REPLAYABLE_PROJECTION_TABLES = frozenset(
        {
            "causal_events",
            "causal_links",
            "state_metrics",
            "state_events",
            "state_snapshots",
            "character_known_claims",
            "chapter_continuity",
        }
    )

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.fts = ChineseFTS(self.config)
        self.lexicon_mgr = LexiconManager(self.config)

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @classmethod
    def _list_rebuild_tables(cls, cur: sqlite3.Cursor) -> list[str]:
        """List real application tables while excluding SQLite shadow tables."""
        try:
            rows = cur.execute("PRAGMA table_list").fetchall()
            return [
                row[1]
                for row in rows
                if row[0] == "main"
                and row[2] in {"table", "virtual"}
                and not row[1].startswith("sqlite_")
            ]
        except sqlite3.OperationalError:
            # Fallback for older SQLite versions without PRAGMA table_list.
            rows = cur.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            virtual_roots = {
                row[0]
                for row in rows
                if (row[1] or "").lstrip().upper().startswith("CREATE VIRTUAL TABLE")
            }
            return [
                row[0]
                for row in rows
                if not any(row[0].startswith(f"{root}_") for root in virtual_roots)
            ]

    @classmethod
    def _deletion_order(cls, cur: sqlite3.Cursor, tables: list[str]) -> list[str]:
        """Return child-before-parent order derived from the live foreign keys."""
        candidates = set(tables) - cls._PRESERVED_ROOT_TABLES
        dependencies = {table: set() for table in candidates}
        for table in candidates:
            pragma_name = cls._quote_identifier(table)
            for row in cur.execute(f"PRAGMA foreign_key_list({pragma_name})").fetchall():
                parent = row[2]
                if parent in candidates and parent != table:
                    dependencies[table].add(parent)

        order: list[str] = []
        while dependencies:
            ready = sorted(table for table, parents in dependencies.items() if not parents)
            if not ready:
                cyclic = ", ".join(sorted(dependencies))
                raise RuntimeError(f"重建无法按外键逆序清理，检测到依赖循环: {cyclic}")
            order.extend(ready)
            for table in ready:
                dependencies.pop(table)
            for parents in dependencies.values():
                parents.difference_update(ready)
        # Kahn's pass above removes parents before their children; reverse it for DELETE.
        return list(reversed(order))

    def _clear_rebuild_tables(self, cur: sqlite3.Cursor, report: RebuildReport) -> None:
        tables = [
            table
            for table in self._list_rebuild_tables(cur)
            if table in self._REBUILDABLE_TABLES
        ]
        for table in self._deletion_order(cur, tables):
            try:
                cur.execute(f"DELETE FROM {self._quote_identifier(table)}")
            except sqlite3.Error as exc:
                raise RuntimeError(f"重建无法清理业务表 {table}: {exc}") from exc
            report.cleared_tables.append(table)

    def _find_non_replayable_projections(self, cur: sqlite3.Cursor) -> list[str]:
        """Return populated projections that this rebuild cannot replay safely."""

        available = set(self._list_rebuild_tables(cur))
        blockers: list[str] = []
        for table in sorted(self._NON_REPLAYABLE_PROJECTION_TABLES & available):
            try:
                count = int(
                    cur.execute(
                        f"SELECT COUNT(*) FROM {self._quote_identifier(table)}"
                    ).fetchone()[0]
                )
            except sqlite3.Error as exc:
                raise RuntimeError(f"重建无法检查派生表 {table}: {exc}") from exc
            if count:
                blockers.append(f"{table}({count})")
        return blockers

    def rebuild_all(self) -> RebuildReport:
        """执行完整重建流程"""
        start_time = time.time()
        report = RebuildReport(elapsed_seconds=0.0)

        # 1. 在任何清理前拒绝不可安全重放的投影，避免留下陈旧表面数据。
        with self.db_client.transaction() as cur:
            blockers = self._find_non_replayable_projections(cur)
            if blockers:
                report.status = "BLOCKED"
                report.blockers.extend(blockers)
                report.warnings.append(
                    "重建已拒绝：存在无法安全重放的派生投影 "
                    + ", ".join(blockers)
                    + "；数据库未清理"
                )
                report.elapsed_seconds = round(time.time() - start_time, 3)
                report.migration_report = MigrationReport(
                    operation="legacy-text-rebuild",
                    status="BLOCKED",
                    blockers=tuple(blockers),
                    warnings=tuple(report.warnings),
                )
                return report

            # 清理所有可重建业务表；作者和作品根记录保留，供文本实体重新挂接。
            self._clear_rebuild_tables(cur, report)

        # 2. 扫描 projects/
        if self.config.projects_dir.is_dir():
            for work_dir in self.config.projects_dir.iterdir():
                if not work_dir.is_dir():
                    continue

                work_id = work_dir.name
                owner_id = "default_author"
                work_json_file = work_dir / "work.json"
                if work_json_file.is_file():
                    try:
                        work_data = json.loads(work_json_file.read_text(encoding="utf-8"))
                        work_id = work_data.get("work_id", work_dir.name)
                        owner_id = work_data.get("owner_id", "default_author")

                        with self.db_client.transaction() as cur:
                            cur.execute(
                                """INSERT INTO authors (owner_id, slug, display_name, created_at)
                                VALUES (?, ?, ?, ?)
                                ON CONFLICT(owner_id) DO UPDATE SET
                                    slug = excluded.slug,
                                    display_name = excluded.display_name,
                                    created_at = excluded.created_at""",
                                (owner_id, owner_id, owner_id, "2026-01-01T00:00:00Z")
                            )
                            cur.execute(
                                """
                                INSERT INTO works
                                (work_id, owner_id, slug, title, genre_ids_json, divergence_anchor, created_at, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(work_id) DO UPDATE SET
                                    owner_id = excluded.owner_id,
                                    slug = excluded.slug,
                                    title = excluded.title,
                                    genre_ids_json = excluded.genre_ids_json,
                                    divergence_anchor = excluded.divergence_anchor,
                                    created_at = excluded.created_at,
                                    updated_at = excluded.updated_at
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
                    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError, sqlite3.Error) as e:
                        report.warnings.append(f"解析 work.json 失败 {work_json_file}: {e}")
                else:
                    try:
                        with self.db_client.transaction() as cur:
                            ensure_work(cur, work_id, owner_id)
                    except sqlite3.Error as exc:
                        report.warnings.append(f"注册作品失败 {work_dir}: {exc}")

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
                                    INSERT INTO entities
                                    (entity_id, work_id, category, is_unique, name, aliases_json, attributes_yaml, file_path, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ON CONFLICT(work_id, entity_id) DO UPDATE SET
                                        category = excluded.category,
                                        is_unique = excluded.is_unique,
                                        name = excluded.name,
                                        aliases_json = excluded.aliases_json,
                                        attributes_yaml = excluded.attributes_yaml,
                                        file_path = excluded.file_path,
                                        updated_at = excluded.updated_at
                                    """,
                                    (
                                        ent_id,
                                        work_id,
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
                                        INSERT INTO entity_phases
                                        (phase_id, work_id, entity_id, phase_name, valid_from_order, valid_to_order, traits_json, anti_behaviors_json, tone_examples_json, updated_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                        ON CONFLICT(work_id, entity_id, phase_id) DO UPDATE SET
                                            phase_name = excluded.phase_name,
                                            valid_from_order = excluded.valid_from_order,
                                            valid_to_order = excluded.valid_to_order,
                                            traits_json = excluded.traits_json,
                                            anti_behaviors_json = excluded.anti_behaviors_json,
                                            tone_examples_json = excluded.tone_examples_json,
                                            updated_at = excluded.updated_at
                                        """,
                                        (
                                            phase_id,
                                            work_id,
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
                        except (OSError, UnicodeError, yaml.YAMLError, ValueError, sqlite3.Error) as e:
                            report.warnings.append(f"解析实体文件失败 {ent_file}: {e}")

                # 同步 relationships.yaml 至 entity_relations
                try:
                    from fxi.domain.relations import RelationManager
                    rm = RelationManager(self.config)
                    rels = rm.load_relationships(work_id)
                    if rels:
                        rm.batch_sync_to_sqlite(work_id, rels)
                except (OSError, UnicodeError, yaml.YAMLError, sqlite3.Error) as e:
                    report.warnings.append(f"同步关系文件失败: {e}")

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
                                scene_uuid = meta.get("scene_uuid", f"sc_{work_id}_{ch_dir.name}")
                                ch_idx = meta.get("chapter_index", 1)
                                self.fts.index_scene(scene_uuid, work_id, ch_idx, body)
                                report.chapters_count += 1
                            except (OSError, UnicodeError, yaml.YAMLError, ValueError, sqlite3.Error) as e:
                                report.warnings.append(f"索引章节失败 {draft_file}: {e}")

        # 3. 扫描 skills/
        if self.config.skills_dir.is_dir():
            for sk_dir in self.config.skills_dir.iterdir():
                if sk_dir.is_dir():
                    report.skills_count += 1

        # 4. 扫描 sources/
        if self.config.sources_dir.is_dir():
            try:
                source_dirs = sorted(
                    self.config.sources_dir.iterdir(), key=lambda path: path.name
                )
            except OSError as exc:
                report.warnings.append(f"扫描来源目录失败 {self.config.sources_dir}: {exc}")
                source_dirs = []

            for src_dir in source_dirs:
                if not src_dir.is_dir() or src_dir.name == "objects":
                    continue
                scenes_dir = src_dir / "scenes"
                try:
                    scene_files = (
                        sorted(scenes_dir.glob("*.md"), key=lambda path: path.name)
                        if scenes_dir.is_dir()
                        else []
                    )
                except OSError as exc:
                    report.warnings.append(f"扫描来源场景目录失败 {scenes_dir}: {exc}")
                    scene_files = []

                for sc_file in scene_files:
                    try:
                        content = sc_file.read_text(encoding="utf-8")
                        self.fts.index_scene(sc_file.stem, src_dir.name, 1, content)
                    except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
                        report.warnings.append(f"索引来源场景失败 {sc_file}: {exc}")
                report.sources_count += 1

        # 5. 导出并更新专用词表
        try:
            self.lexicon_mgr.export_lexicon()
        except (OSError, UnicodeError, sqlite3.Error) as e:
            report.warnings.append(f"词表导出告警: {e}")

        report.elapsed_seconds = round(time.time() - start_time, 3)
        if report.warnings:
            report.status = "INCOMPLETE"
        report.migration_report = MigrationReport(
            operation="legacy-text-rebuild",
            status=report.status,
            applied=tuple(report.cleared_tables),
            blockers=tuple(report.blockers),
            warnings=tuple(report.warnings),
            errors=tuple(report.errors),
        )
        return report


class RebuildService:
    """Rebuild selected disposable projections behind one fail-closed boundary.

    ``ProjectionRunner`` is the authority-neutral v3 path. When it is not
    supplied, only the legacy lexical rebuild is available and it is delegated
    to ``Rebuilder``; no other selector is silently treated as implemented.
    """

    _LEGACY_SELECTORS = frozenset({"fts", "legacy-text"})

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        *,
        projection_runner: Any = None,
        rebuilder: Rebuilder | None = None,
    ) -> None:
        self.config = config or load_config()
        self.projection_runner = projection_runner
        self.rebuilder = rebuilder or Rebuilder(self.config)

    @staticmethod
    def _selector_parts(value: Any) -> tuple[str, str | None] | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            return None
        for separator in ("@", ":"):
            if separator in value:
                projection, version = value.split(separator, 1)
                if not projection or not version or separator in version:
                    return None
                return projection, version
        return value, None

    @staticmethod
    def _report_status(*, errors: Sequence[str], blockers: Sequence[str], warnings: Sequence[str]) -> str:
        if blockers:
            return "BLOCKED"
        if errors:
            return "FAILED"
        if warnings:
            return "INCOMPLETE"
        return "PASSED"

    def _make_report(
        self,
        started: float,
        selectors: tuple[str, ...],
        *,
        manifests: Sequence[Mapping[str, Any]] = (),
        applied: Sequence[str] = (),
        warnings: Sequence[str] = (),
        blockers: Sequence[str] = (),
        errors: Sequence[str] = (),
    ) -> RebuildReport:
        warning_values = list(warnings)
        blocker_values = list(blockers)
        error_values = list(errors)
        status = self._report_status(
            errors=error_values,
            blockers=blocker_values,
            warnings=warning_values,
        )
        report = RebuildReport(
            elapsed_seconds=round(time.time() - started, 3),
            cleared_tables=list(applied),
            warnings=warning_values,
            status=status,
            selectors=selectors,
            projection_manifests=tuple(dict(item) for item in manifests),
            blockers=blocker_values,
            errors=error_values,
        )
        report.migration_report = MigrationReport(
            operation="projection-rebuild",
            status=status,
            applied=tuple(applied),
            blockers=tuple(blocker_values),
            warnings=tuple(warning_values),
            errors=tuple(error_values),
        )
        return report

    def rebuild(self, selectors: Sequence[str]) -> RebuildReport:
        """Rebuild exactly the requested projection selectors.

        A selector may be ``projection`` (all known KnowledgeVersions) or
        ``projection@knowledge-version``. Empty input means the explicit
        lexical baseline only, preserving the old CLI's no-argument behavior.
        """

        started = time.time()
        if isinstance(selectors, (str, bytes)):
            return self._make_report(
                started,
                (),
                errors=("INVALID_SCHEMA: selectors must be a sequence",),
            )
        raw_selectors = tuple(selectors)
        if not raw_selectors:
            raw_selectors = ("fts",)
        parsed: list[tuple[str, str | None]] = []
        invalid: list[str] = []
        for value in raw_selectors:
            item = self._selector_parts(value)
            if item is None:
                invalid.append(f"INVALID_SCHEMA: invalid projection selector {value!r}")
            else:
                parsed.append(item)
        if invalid:
            return self._make_report(started, tuple(str(item) for item in raw_selectors), errors=invalid)

        runner = self.projection_runner
        if runner is None:
            unsupported = [projection for projection, _ in parsed if projection not in self._LEGACY_SELECTORS]
            if unsupported:
                return self._make_report(
                    started,
                    tuple(str(item) for item in raw_selectors),
                    errors=tuple(
                        f"CAPABILITY_UNSUPPORTED: projection provider is not configured: {projection}"
                        for projection in unsupported
                    ),
                )
            legacy = self.rebuilder.rebuild_all()
            legacy.selectors = tuple(str(item) for item in raw_selectors)
            legacy.elapsed_seconds = round(time.time() - started, 3)
            return legacy

        providers = getattr(runner, "providers", {})
        knowledge_versions = getattr(runner, "knowledge_versions", {})
        items = getattr(knowledge_versions, "items", None)
        version_values = tuple(
            value for _, value in sorted(items(), key=lambda item: str(item[0]))
        ) if callable(items) else ()
        if not version_values:
            return self._make_report(
                started,
                tuple(str(item) for item in raw_selectors),
                blockers=("KNOWLEDGE_VERSION_REQUIRED: no registered KnowledgeVersion",),
            )

        expanded: list[tuple[str, Any]] = []
        errors: list[str] = []
        for projection, requested_version in parsed:
            projection_ids = tuple(sorted(providers)) if projection == "all" else (projection,)
            for projection_id in projection_ids:
                if projection_id not in providers:
                    errors.append(f"NOT_FOUND: unknown projection provider: {projection_id}")
                    continue
                selected_versions = version_values
                if requested_version is not None:
                    selected_versions = tuple(
                        value
                        for value in version_values
                        if str(getattr(value, "knowledge_version", "")) == requested_version
                    )
                    if not selected_versions:
                        errors.append(f"NOT_FOUND: unknown KnowledgeVersion: {requested_version}")
                        continue
                expanded.extend((projection_id, value) for value in selected_versions)

        manifests: list[Mapping[str, Any]] = []
        warnings: list[str] = []
        for projection_id, version in expanded:
            manifest = runner.rebuild(projection_id, version)
            payload = manifest.model_dump(mode="json") if hasattr(manifest, "model_dump") else dict(manifest)
            manifests.append(payload)
            if payload.get("status") != "BUILT":
                code = str(payload.get("error_code") or "PROJECTION_FAILED")
                warnings.append(f"{projection_id}:{code}")
        return self._make_report(
            started,
            tuple(str(item) for item in raw_selectors),
            manifests=manifests,
            warnings=warnings,
            errors=errors,
        )


__all__ = ["RebuildReport", "RebuildService", "Rebuilder"]
