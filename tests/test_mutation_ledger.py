"""
tests.test_mutation_ledger - 同人时间线变动账本与动态有效状态合成测试
"""

from pathlib import Path
import tempfile
import pytest

from fxi.core.config import FxiConfig
from fxi.domain.entities import EntityManager
from fxi.domain.mutation_ledger import MutationLedger, TimelineMutation
from fxi.storage.sqlite_client import DatabaseClient


@pytest.fixture
def test_env(temp_workspace: FxiConfig):
    config = temp_workspace
    db = DatabaseClient(config.sqlite_path)

    # 准备基础原著数据 (base work)
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO works (work_id, owner_id, slug, title, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("zhanshen_base", "default_author", "zhanshen_base", "斩神原著"),
        )
        cur.execute(
            "INSERT INTO works (work_id, owner_id, slug, title, created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("zhanshen_fanfic", "default_author", "zhanshen_fanfic", "斩神同人"),
        )
        cur.execute(
            "INSERT INTO entities (work_id, entity_id, category, name, file_path, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("zhanshen_base", "char_lin", "character", "林七夜", "dummy.md"),
        )
        # 原著能力：第 1 章觉醒炽天使神威，第 15 章觉醒凡尘神域，第 281 章领悟诗成剑气
        cur.execute(
            """
            INSERT INTO entity_abilities (
                ability_id, work_id, entity_id, ability_name, category, sequence_num,
                valid_from_chapter, valid_to_chapter, effect_description, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ab_001", "zhanshen_base", "char_lin", "炽天使神威", "divine", "003", 1, None, "黑夜守护"),
        )
        cur.execute(
            """
            INSERT INTO entity_abilities (
                ability_id, work_id, entity_id, ability_name, category, sequence_num,
                valid_from_chapter, valid_to_chapter, effect_description, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ab_002", "zhanshen_base", "char_lin", "凡尘神域", "acquired", None, 15, None, "掌控微尘领域"),
        )
        cur.execute(
            """
            INSERT INTO entity_abilities (
                ability_id, work_id, entity_id, ability_name, category, sequence_num,
                valid_from_chapter, valid_to_chapter, effect_description, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ab_003", "zhanshen_base", "char_lin", "诗成剑气", "sword", None, 281, None, "笔落惊风雨"),
        )

    return config, db


def test_timeline_mutation_serialization():
    m = TimelineMutation(
        mutation_id="mut_test_01",
        work_id="fanfic_01",
        trigger_chapter=12,
        cause_event="主角穿越干涉",
        entity_id="char_lin",
        mutation_type="ability_grant",
        target_name="凡尘神域",
        original_canon_chapter=15,
        payload={"power": 100},
    )
    d = m.to_dict()
    assert d["mutation_id"] == "mut_test_01"
    assert d["original_canon_chapter"] == 15

    m2 = TimelineMutation.from_dict(d)
    assert m2.mutation_id == m.mutation_id
    assert m2.trigger_chapter == 12
    assert m2.target_name == "凡尘神域"


def test_mutation_ledger_crud_and_yaml_sync(test_env):
    config, db = test_env
    ledger = MutationLedger(config=config, db_client=db)

    # 1. 记录变动
    m = ledger.record_mutation(
        work_id="zhanshen_fanfic",
        trigger_chapter=10,
        cause_event="主角传授上古身法",
        entity_name_or_id="char_lin",
        mutation_type="ability_grant",
        target_name="大荒星陨步",
        payload={"cost": "10%精神力"},
    )
    assert m.mutation_id.startswith("mut_010_")
    assert m.status == "active"

    # 2. 检查 YAML 文件已同步
    yaml_file = ledger.get_ledger_path("zhanshen_fanfic")
    assert yaml_file.is_file()
    assert "大荒星陨步" in yaml_file.read_text(encoding="utf-8")

    # 3. 按章节过滤
    muts_ch9 = ledger.list_mutations("zhanshen_fanfic", chapter=9)
    assert len(muts_ch9) == 0

    muts_ch10 = ledger.list_mutations("zhanshen_fanfic", chapter=10)
    assert len(muts_ch10) == 1
    assert muts_ch10[0].target_name == "大荒星陨步"

    # 4. 从文件重新同步恢复
    count = ledger.sync_from_file("zhanshen_fanfic")
    assert count == 1


def test_effective_state_synthesis_with_early_awakening(test_env):
    config, db = test_env
    ledger = MutationLedger(config=config, db_client=db)
    mgr = EntityManager(config=config)

    # 在同人作品中立项：第 10 章因主角干预，提前觉醒凡尘神域 (原著第15章)
    ledger.record_mutation(
        work_id="zhanshen_fanfic",
        trigger_chapter=10,
        cause_event="主角赠予淬体圣药打破封印",
        entity_name_or_id="char_lin",
        mutation_type="ability_grant",
        target_name="凡尘神域",
        original_canon_chapter=15,
    )

    # 测试第 9 章：尚未触发变动，只有第 1 章的炽天使神威
    state_ch9 = mgr.get_effective_state("zhanshen_fanfic", "char_lin", chapter=9, base_work_id="zhanshen_base")
    assert state_ch9["canon_abilities_count"] == 1
    assert state_ch9["mutations_applied_count"] == 0
    names_ch9 = [a["ability_name"] for a in state_ch9["effective_abilities"]]
    assert names_ch9 == ["炽天使神威"]

    # 测试第 10 章：触发变动，成功提前觉醒凡尘神域！
    state_ch10 = mgr.get_effective_state("zhanshen_fanfic", "char_lin", chapter=10, base_work_id="zhanshen_base")
    assert state_ch10["mutations_applied_count"] == 1
    names_ch10 = [a["ability_name"] for a in state_ch10["effective_abilities"]]
    assert "炽天使神威" in names_ch10
    assert "凡尘神域" in names_ch10
    assert "诗成剑气" not in names_ch10  # 严格防偷跑：第281章技能绝不出现

    fan_chen = next(a for a in state_ch10["effective_abilities"] if a["ability_name"] == "凡尘神域")
    assert fan_chen["is_mutated"] is True
    assert fan_chen["is_early_awakened"] is True
    assert "提前于第10章生效" in fan_chen["origin_label"]


def test_ability_modify_and_suppress_mutations(test_env):
    config, db = test_env
    ledger = MutationLedger(config=config, db_client=db)
    mgr = EntityManager(config=config)

    # 1. 变动：修改炽天使神威，消除代价
    ledger.record_mutation(
        work_id="zhanshen_fanfic",
        trigger_chapter=5,
        cause_event="主角重铸神明法则",
        entity_name_or_id="char_lin",
        mutation_type="ability_modify",
        target_name="炽天使神威",
        payload={"effect_description": "无副作用神威领域"},
    )

    state_ch5 = mgr.get_effective_state("zhanshen_fanfic", "char_lin", chapter=5, base_work_id="zhanshen_base")
    ab = state_ch5["effective_abilities"][0]
    assert ab["is_mutated"] is True
    assert ab["effect_description"] == "无副作用神威领域"

    # 2. 变动：在第 8 章封印/压制炽天使神威
    ledger.record_mutation(
        work_id="zhanshen_fanfic",
        trigger_chapter=8,
        cause_event="天道法则强行压制",
        entity_name_or_id="char_lin",
        mutation_type="ability_suppress",
        target_name="炽天使神威",
    )

    state_ch8 = mgr.get_effective_state("zhanshen_fanfic", "char_lin", chapter=8, base_work_id="zhanshen_base")
    names_ch8 = [a["ability_name"] for a in state_ch8["effective_abilities"]]
    assert "炽天使神威" not in names_ch8
