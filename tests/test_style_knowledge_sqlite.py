"""
tests/test_style_knowledge_sqlite.py - 知识库章节索引与技法规则 SQLite 存储验证
"""

import pytest
from pathlib import Path
from fxi.storage.sqlite_client import DatabaseClient
from fxi.materials_skills.style_canonical import CanonicalRuleMapper


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_manifest.sqlite"
    client = DatabaseClient(db_file)
    return client


def test_source_chapters_crud(temp_db: DatabaseClient):
    work_id = "test_work"
    chapters = [
        {"chapter_index": 2, "chapter_title": "第二章 突破", "char_count": 3200, "sha256": "hash2", "file_path": "ch002.md", "is_analyzed": False},
        {"chapter_index": 1, "chapter_title": "第一章 初始", "char_count": 3000, "sha256": "hash1", "file_path": "ch001.md", "is_analyzed": True},
        {"chapter_index": 3, "chapter_title": "第三章 惊变", "char_count": 3500, "sha256": "hash3", "file_path": "ch003.md", "is_analyzed": False},
    ]
    inserted = temp_db.upsert_source_chapters(work_id, chapters)
    assert inserted == 3

    # 查询全部（顺序应为 1, 2, 3）
    rows = temp_db.get_source_chapters(work_id)
    assert len(rows) == 3
    assert [r["chapter_index"] for r in rows] == [1, 2, 3]

    # 按 is_analyzed 过滤
    unvisited = temp_db.get_source_chapters(work_id, is_analyzed=False)
    assert len(unvisited) == 2
    assert [r["chapter_index"] for r in unvisited] == [2, 3]

    visited = temp_db.get_source_chapters(work_id, is_analyzed=True)
    assert len(visited) == 1
    assert visited[0]["chapter_index"] == 1


def test_style_rule_canonical_deduplication(temp_db: DatabaseClient):
    work_id = "test_work"

    # 第 1 章提炼出规则
    rule1 = {
        "canonical_key": "action_before_explanation",
        "category": "action",
        "scene_scope": "confrontation",
        "instruction": "表达心境前先写微动作",
        "anti_pattern": "严禁直接写心头震怒",
        "chapter_index": 1,
    }
    r_id = temp_db.upsert_style_rule(work_id, rule1)
    assert r_id.startswith("rule_")

    # 第 5 章提炼出同义规则
    rule2 = {
        "canonical_key": "action_before_explanation",
        "category": "action",
        "scene_scope": "confrontation",
        "instruction": "表达心境前先写微动作与体征",
        "anti_pattern": "严禁直接写心头震怒",
        "chapter_index": 5,
    }
    r_id2 = temp_db.upsert_style_rule(work_id, rule2)
    assert r_id2 == r_id  # 规则 ID 不变，复用已有规则记录

    # 验证累加计数
    rules = temp_db.query_scene_style_rules(work_id, scene_type="confrontation")
    assert len(rules) == 1
    assert rules[0]["support_chapters_count"] == 2
    assert "5" in rules[0]["supporting_chapters_json"]


def test_scene_pruning_query(temp_db: DatabaseClient):
    work_id = "test_work"

    # 插入一条纯战斗规则
    temp_db.upsert_style_rule(work_id, {
        "canonical_key": "rapid_combat_action_burst",
        "category": "action",
        "scene_scope": "battle",
        "instruction": "激烈交锋中以短句推进",
        "chapter_index": 1,
    })

    # 插入一条通用规则
    temp_db.upsert_style_rule(work_id, {
        "canonical_key": "sensory_prop_anchoring",
        "category": "detail",
        "scene_scope": "ALL",
        "instruction": "物证交互固定注意力",
        "chapter_index": 2,
    })

    # 插入一条日常规则
    temp_db.upsert_style_rule(work_id, {
        "canonical_key": "daily_banter_tempo",
        "category": "dialogue",
        "scene_scope": "daily",
        "instruction": "日常戏谑中以互损推进",
        "chapter_index": 3,
    })

    # 当查询 battle 场景时：应该查出 battle + ALL，绝不能出现 daily
    battle_rules = temp_db.query_scene_style_rules(work_id, scene_type="battle")
    keys = [r["canonical_key"] for r in battle_rules]
    assert "rapid_combat_action_burst" in keys
    assert "sensory_prop_anchoring" in keys
    assert "daily_banter_tempo" not in keys

    # 当查询 daily 场景时：应该查出 daily + ALL，绝不能出现 battle
    daily_rules = temp_db.query_scene_style_rules(work_id, scene_type="daily")
    d_keys = [r["canonical_key"] for r in daily_rules]
    assert "daily_banter_tempo" in d_keys
    assert "sensory_prop_anchoring" in d_keys
    assert "rapid_combat_action_burst" not in d_keys


def test_style_evidence_offset_pointer(temp_db: DatabaseClient):
    work_id = "test_work"
    rule_id = temp_db.upsert_style_rule(work_id, {
        "canonical_key": "action_before_explanation",
        "instruction": "表达情绪前写微动作",
        "chapter_index": 1,
    })

    ev_id = temp_db.add_style_evidence(
        rule_id=rule_id,
        work_id=work_id,
        chapter_index=20,
        quote="指尖在袖中微颤",
        offset_start=1520,
        offset_end=1540,
    )
    assert ev_id > 0


def test_style_profile_storage(temp_db: DatabaseClient):
    work_id = "test_work"
    metrics = {"avg_sentence_len": 18.5, "dialogue_ratio": 0.42}
    lexicon = {"high_freq_verbs": ["握紧", "冷笑", "微颤"]}

    temp_db.save_style_profile(work_id, author="火星引力", metrics=metrics, lexicon_features=lexicon)
    row = temp_db.get_style_profile(work_id)
    assert row is not None
    assert row["author"] == "火星引力"
    assert "avg_sentence_len" in row["metrics_json"]


def test_canonical_rule_mapper():
    # 测试中文分类映射
    assert CanonicalRuleMapper.normalize_category("对白回应") == "dialogue"
    assert CanonicalRuleMapper.normalize_category("信息揭示") == "information"

    # 测试近义 key 归一
    res = CanonicalRuleMapper.map_to_canonical(
        raw_key="detail_action_before_feeling",
        category="动作",
        instruction="在人物愤怒时先描写拳头紧握",
        scene_scope="对峙",
    )
    assert res["canonical_key"] == "action_before_explanation"
    assert res["category"] == "action"
    assert res["scene_scope"] == "confrontation"

    # 测试可执行门禁
    assert CanonicalRuleMapper.is_executable_rule("在表达情绪前先描写肢体微动作，句长控制在8字以内") is True
    assert CanonicalRuleMapper.is_executable_rule("注意烘托气氛") is False


def test_unknown_scene_scope_does_not_expand_to_all():
    with pytest.raises(ValueError, match="unsupported scene scope"):
        CanonicalRuleMapper.normalize_scene_scope("unknown_future_scene")

    with pytest.raises(ValueError, match="unsupported scene scope"):
        CanonicalRuleMapper.map_to_canonical(
            raw_key="detail_method",
            category="detail",
            instruction="用明确物证固定现场",
            scene_scope="unknown",
        )


def test_canonical_mapping_preserves_condition_and_operation():
    result = CanonicalRuleMapper.map_to_canonical(
        raw_key="dialogue_delay",
        category="对白回应",
        instruction="先承接误判，再延迟解释",
        scene_scope="对峙",
        condition={"scene_type": "confrontation"},
        operation="延迟解释",
        effect_hypothesis="保留下一回合的信息张力",
        cost="增加一个对白回合",
        evidence_refs=["scene-1"],
    )

    assert result["condition"] == {"scene_type": "confrontation"}
    assert result["operation"] == "延迟解释"
    assert result["effect_hypothesis"] == "保留下一回合的信息张力"
    assert result["cost"] == "增加一个对白回合"
    assert result["evidence_refs"] == ["scene-1"]
