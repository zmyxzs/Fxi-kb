"""
Tests for ContinuityManager SQLite storage and migration.
"""

import time
from pathlib import Path

import pytest
import yaml

from fxi.core.config import FxiConfig
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
from fxi.timeline.continuity import ContinuityManager
from fxi.tools.migrate_continuity_ledger import migrate_work_continuity


def test_continuity_sqlite_record_and_get(temp_workspace: FxiConfig):
    """验证使用 SQLite 记录和单章毫秒级查询。"""
    mgr = ContinuityManager(temp_workspace)
    work_id = "test_work_continuity"

    mgr.record_chapter(
        work_id=work_id,
        chapter_index=1,
        title="第1章 惊变",
        tail_snippet="主角拔剑而立，冷眼看着倒下的黑衣人。",
        ending_location="幽暗森林外围",
        active_characters=["林七夜", "黑衣人"],
        ending_situation="战斗结束，周围还有潜伏的杀气。",
        unresolved_hooks=["黑衣人身上的特殊标记到底来自哪个势力？"],
    )

    # 查第1章
    data = mgr.get_continuity(work_id, 1)
    assert data is not None
    assert data["chapter_index"] == 1
    assert data["title"] == "第1章 惊变"
    assert data["ending_location"] == "幽暗森林外围"
    assert "林七夜" in data["active_characters"]
    assert len(data["unresolved_hooks"]) == 1

    # 查不存在的章节
    assert mgr.get_continuity(work_id, 2) is None

    # 获取最新一章
    latest = mgr.get_latest_continuity(work_id)
    assert latest is not None
    assert latest["chapter_index"] == 1

    # 追加第2章
    mgr.record_chapter(
        work_id=work_id,
        chapter_index=2,
        title="第2章 追查",
        tail_snippet="他收起长剑，向着城池走去。",
        ending_location="沧南城门",
        active_characters=["林七夜"],
        ending_situation="进入城市安全区。",
        unresolved_hooks=[],
    )
    latest2 = mgr.get_latest_continuity(work_id)
    assert latest2["chapter_index"] == 2


def test_continuity_sqlite_fallback_and_auto_migration(temp_workspace: FxiConfig):
    """验证当 SQLite 尚无数据但旧 YAML 文件存在时，自动兜底并迁移入库。"""
    work_id = "legacy_work_test"
    target_dir = temp_workspace.projects_dir / work_id / "timeline"
    target_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = target_dir / "continuity_ledger.yaml"

    yaml_data = {
        "work_id": work_id,
        "chapters": {
            10: {
                "chapter_index": 10,
                "title": "第10章 往事",
                "ending_location": "酒馆",
                "active_characters": ["老赵", "小林"],
                "ending_situation": "两人对饮谈心。",
                "unresolved_hooks": ["老赵的秘密任务"],
                "tail_snippet": "杯中酒已空。",
            }
        },
    }
    yaml_path.write_text(yaml.dump(yaml_data, allow_unicode=True), encoding="utf-8")

    mgr = ContinuityManager(temp_workspace)
    # 直接查询第10章：应从旧 YAML 自动迁移并返回
    ch10 = mgr.get_continuity(work_id, 10)
    assert ch10 is not None
    assert ch10["title"] == "第10章 往事"
    assert ch10["ending_location"] == "酒馆"

    # 验证 SQLite 表中已存在该记录
    db = DatabaseClient(temp_workspace.sqlite_path)
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM chapter_continuity WHERE work_id = ? AND chapter_index = 10",
            (work_id,),
        ).fetchone()
        assert row is not None
        assert row["title"] == "第10章 往事"


def test_migrate_continuity_ledger_tool(temp_workspace: FxiConfig):
    """验证离线批量迁移脚本的工作与备份。"""
    work_id = "batch_migrate_work"
    target_dir = temp_workspace.projects_dir / work_id / "timeline"
    target_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = target_dir / "continuity_ledger.yaml"

    chapters_dict = {}
    for i in range(1, 21):
        chapters_dict[i] = {
            "chapter_index": i,
            "title": f"第{i}章",
            "ending_location": f"地点{i}",
            "active_characters": ["主角"],
            "ending_situation": f"局面{i}",
            "unresolved_hooks": [f"钩子{i}"],
            "tail_snippet": f"尾巴{i}",
        }
    yaml_path.write_text(yaml.dump({"work_id": work_id, "chapters": chapters_dict}, allow_unicode=True), encoding="utf-8")

    migrated_count = migrate_work_continuity(work_id, temp_workspace, backup=True)
    assert migrated_count == 20

    # 原文件已被重命名为 .bak
    assert not yaml_path.exists()
    assert yaml_path.with_suffix(".yaml.bak").exists()

    # 通过 ContinuityManager 查询第 15 章
    mgr = ContinuityManager(temp_workspace)
    ch15 = mgr.get_continuity(work_id, 15)
    assert ch15 is not None
    assert ch15["title"] == "第15章"


def test_continuity_large_scale_performance(temp_workspace: FxiConfig):
    """性能压力测试：批量写入 100 章并在 SQLite 下进行单章毫秒级定位。"""
    mgr = ContinuityManager(temp_workspace)
    work_id = "perf_work"

    start_write = time.perf_counter()
    for i in range(1, 101):
        mgr.record_chapter(
            work_id=work_id,
            chapter_index=i,
            title=f"第{i}章",
            tail_snippet=f"第{i}章最后三百字切片描述...",
            ending_location=f"场景_{i}",
            active_characters=["角色A", "角色B"],
            ending_situation=f"第{i}章收尾状态",
            unresolved_hooks=[f"悬念_{i}"],
        )
    write_duration = time.perf_counter() - start_write

    # 100 章写入应在极短时间内完成
    assert write_duration < 5.0

    # 单章读取延迟测试 (O(1) 定位)
    start_read = time.perf_counter()
    ch_99 = mgr.get_continuity(work_id, 99)
    read_duration = time.perf_counter() - start_read

    assert ch_99 is not None
    assert ch_99["chapter_index"] == 99
    # 单次读取应在 50ms 乃至 10ms 以内
    assert read_duration < 0.05
