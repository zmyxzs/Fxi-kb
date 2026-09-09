"""Deprecated one-off migration to ``chapter_continuity``.

Use the normal continuity write path for new data.  This helper remains until
2026-12 only for an explicitly selected work and legacy file.
"""

import json
import logging
import warnings
from typing import Optional
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient, ensure_work

logger = logging.getLogger("fxi.migrate_continuity")


def migrate_work_continuity(
    work_id: str,
    config: Optional[FxiConfig] = None,
    backup: bool = True,
) -> int:
    """迁移指定作品的 continuity_ledger.yaml 到 SQLite chapter_continuity 表中"""
    warnings.warn(
        "migrate_continuity_ledger 已弃用，将在 2026-12 移除；请使用 ContinuityManager 写入路径。",
        DeprecationWarning,
        stacklevel=2,
    )
    cfg = config or load_config()
    yaml_path = cfg.projects_dir / work_id / "timeline" / "continuity_ledger.yaml"
    if not yaml_path.is_file():
        logger.info(f"作品 [{work_id}] 无 continuity_ledger.yaml，跳过")
        return 0

    try:
        content = yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        logger.error(f"读取 [{yaml_path}] 失败: {exc}")
        raise RuntimeError(f"读取遗留连续性台账失败: {yaml_path}") from exc

    chapters = data.get("chapters", {})
    if not isinstance(chapters, dict) or not chapters:
        logger.info(f"作品 [{work_id}] 台账无章节记录")
        return 0

    db_client = DatabaseClient(cfg.sqlite_path)
    count = 0
    with db_client.transaction() as cur:
        ensure_work(cur, work_id)
        for ch_idx, item in chapters.items():
            if not isinstance(item, dict):
                continue
            idx = int(item.get("chapter_index", ch_idx))
            cur.execute(
                """
                INSERT INTO chapter_continuity
                (work_id, chapter_index, title, ending_location, active_characters_json,
                 ending_situation, unresolved_hooks_json, tail_snippet, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(work_id, chapter_index) DO UPDATE SET
                    title = excluded.title,
                    ending_location = excluded.ending_location,
                    active_characters_json = excluded.active_characters_json,
                    ending_situation = excluded.ending_situation,
                    unresolved_hooks_json = excluded.unresolved_hooks_json,
                    tail_snippet = excluded.tail_snippet,
                    created_at = excluded.created_at
                """,
                (
                    work_id,
                    idx,
                    item.get("title", f"第{idx}章"),
                    item.get("ending_location", "未标明"),
                    json.dumps(item.get("active_characters", []), ensure_ascii=False),
                    item.get("ending_situation", ""),
                    json.dumps(item.get("unresolved_hooks", []), ensure_ascii=False),
                    item.get("tail_snippet", ""),
                ),
            )
            count += 1

    logger.info(f"作品 [{work_id}] 成功导入 {count} 章连续性数据到 SQLite")

    if backup and count > 0:
        bak_path = yaml_path.with_suffix(".yaml.bak")
        try:
            yaml_path.rename(bak_path)
            logger.info(f"已备份并归档原文件至: {bak_path}")
        except OSError as exc:
            logger.warning(f"重命名备份原文件失败: {exc}")
            raise RuntimeError(f"遗留连续性台账已写入但备份失败: {yaml_path}") from exc

    return count


def migrate_all_works(config: Optional[FxiConfig] = None) -> dict[str, int]:
    cfg = config or load_config()
    results: dict[str, int] = {}
    if not cfg.projects_dir.is_dir():
        return results

    for item in cfg.projects_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            work_id = item.name
            cnt = migrate_work_continuity(work_id, cfg, backup=True)
            results[work_id] = cnt
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = migrate_all_works()
    print(f"连续性台账迁移完成: {results}")
