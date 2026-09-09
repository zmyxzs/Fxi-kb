"""
tests.test_bootstrap_baseline - 测试项目基线冷启动工具与 Version 0 沉淀
"""

import json
from pathlib import Path
import pytest

from fxi.core.config import FxiConfig
from fxi.domain.entities import EntityManager
from fxi.storage.sqlite_client import DatabaseClient
from fxi.timeline.continuity import ContinuityManager
from fxi.tools.bootstrap_project import bootstrap_work


def _create_mock_source_chapters(root: Path):
    ch21 = root / "chapters" / "ch_21"
    ch21.mkdir(parents=True, exist_ok=True)
    (ch21 / "chapter_21.md").write_text(
        "第21章 破晓\n罗峰立于扬州城楼之上，手握影月战刀，凝视着荒野区的雾气。\n" * 5,
        encoding="utf-8",
    )
    (ch21 / "chapter_plan.json").write_text(
        json.dumps(
            {
                "chapter_beats": [
                    {"event_id": "beat_1", "content": "罗峰检查装备"},
                    {"event_id": "beat_2", "content": "城防警报骤响，兽潮逼近"},
                ],
                "scenes": [
                    {
                        "fact_lock": {
                            "allowed_characters": ["罗峰", "邬通"],
                            "location_id": "扬州城东城门",
                            "allowed_props": ["影月战刀"],
                        }
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ch22 = root / "chapters" / "ch_22"
    ch22.mkdir(parents=True, exist_ok=True)
    (ch22 / "chapter_22.md").write_text(
        "第22章 兽吼\n怪兽咆哮震天动地，罗峰施展九重雷刀，刀芒如电，斩杀数头领主级怪兽。\n" * 5,
        encoding="utf-8",
    )
    (ch22 / "chapter_plan.json").write_text(
        json.dumps(
            {
                "chapter_beats": [
                    {"event_id": "beat_1", "content": "战刀出鞘杀敌"},
                    {"event_id": "beat_2", "content": "兽群暂退，守军修整"},
                ],
                "scenes": [
                    {
                        "fact_lock": {
                            "allowed_characters": ["罗峰", "火锤小队"],
                            "location_id": "023号城市废墟",
                            "allowed_props": ["通讯手表"],
                        }
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_bootstrap_work_creates_version_zero_baseline(temp_workspace: FxiConfig, tmp_path: Path):
    """测试将前序章节沉淀为 knowledge-v0 初始快照"""
    source_root = tmp_path / "studio_data"
    _create_mock_source_chapters(source_root)

    work_id = "test_fanfic_001"
    res = bootstrap_work(
        config=temp_workspace,
        work_id=work_id,
        source_root=source_root,
        chapter_indices=(21, 22),
    )

    assert res["status"] == "success"
    assert res["work_id"] == work_id
    assert res["knowledge_version"] == "knowledge-v0"
    assert res["chapter_version"] == "chapter-v22"
    assert res["chapter_indices"] == [21, 22]
    assert res["entities_count"] >= 3

    # 1. 验证 SQLite 数据库头指针
    db = DatabaseClient(temp_workspace.sqlite_path)
    with db.get_connection() as conn:
        head = conn.execute(
            "SELECT knowledge_version, chapter_version FROM v2_work_heads WHERE work_id = ?",
            (work_id,),
        ).fetchone()
        assert head is not None
        assert head["knowledge_version"] == "knowledge-v0"
        assert head["chapter_version"] == "chapter-v22"

        # 2. 验证不可变快照表
        snap = conn.execute(
            "SELECT * FROM v2_source_snapshots WHERE work_id = ?",
            (work_id,),
        ).fetchone()
        assert snap is not None
        assert snap["version"] == res["source_version"]
        docs = json.loads(snap["documents_json"])
        assert len(docs) == 2

    # 3. 验证连续性台账已毫秒级入库
    continuity_mgr = ContinuityManager(temp_workspace)
    c21 = continuity_mgr.get_continuity(work_id, 21)
    assert c21 is not None
    assert c21["title"] == "第21章"
    assert "罗峰" in c21["active_characters"]
    assert c21["ending_location"] == "扬州城东城门"

    c22 = continuity_mgr.get_continuity(work_id, 22)
    assert c22 is not None
    assert c22["title"] == "第22章"
    assert "兽群暂退" in c22["ending_situation"]

    # 4. 验证基线实体落盘
    ent_mgr = EntityManager(temp_workspace)
    entities = ent_mgr.list_entities(work_id)
    names = {e["name"] for e in entities}
    assert "罗峰" in names
    assert "023号城市废墟" in names or "扬州城东城门" in names


def test_bootstrap_real_studio_chapters_if_present(temp_workspace: FxiConfig):
    """如果本地存在真实的 novel-Studio/data/zhanshen_fanfic，验证真实数据冷启动"""
    real_studio_path = Path("D:/Code/novel-Studio/data/zhanshen_fanfic")
    if not real_studio_path.is_dir():
        pytest.skip("本地未找到真实 novel-Studio/data/zhanshen_fanfic 目录，跳过真实数据测试")

    work_id = "zhanshen_fanfic_test"
    res = bootstrap_work(
        config=temp_workspace,
        work_id=work_id,
        source_root=real_studio_path,
        chapter_indices=(21, 22),
    )

    assert res["status"] == "success"
    assert res["knowledge_version"] == "knowledge-v0"

    continuity_mgr = ContinuityManager(temp_workspace)
    c21 = continuity_mgr.get_continuity(work_id, 21)
    assert c21 is not None
    assert len(c21["tail_snippet"]) > 0
