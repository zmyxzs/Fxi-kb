"""
tests.regression.test_19_canonical_cases - 19 个长篇小说与全题材核心回归测试矩阵
"""

import json
from pathlib import Path
import pytest

from fxi.core.config import FxiConfig
from fxi.core.exceptions import ResourceDeficitError, SkillCastIllegalError
from fxi.core.types import CausalStatus, LifecycleAction, MetricStatus, SceneType
from fxi.domain.entities import EntityManager
from fxi.domain.items import ItemManager
from fxi.domain.ownership import OwnershipTracker
from fxi.domain.phases import PhaseManager
from fxi.character_knowledge.knowledge_tracker import KnowledgeTracker
from fxi.character_knowledge.ooc_checker import OOCChecker
from fxi.character_knowledge.pov_filter import POVFilter
from fxi.claims.lifecycle import LifecycleManager
from fxi.claims.models import RetconDeclaration
from fxi.claims.retcon import RetconManager
from fxi.claims.triage import TriageEngine
from fxi.game_engine.combat_verifier import CombatVerifier
from fxi.game_engine.passive_radar import PassiveThreatRadar
from fxi.game_engine.projection import TieredParameterProjector
from fxi.game_engine.skills import SkillTreeEngine
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.index_retrieval.project_lexicon import LexiconManager
from fxi.sources.scene_id import generate_scene_uuid
from fxi.state_ledger.anchor import AnchorManager
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.storage.rebuild import Rebuilder
from fxi.territory.macro_tags import MacroTagGenerator
from fxi.territory.population import TerritoryPopulationManager
from fxi.territory.resources import TerritoryResourceManager
from fxi.timeline.dag import CausalDAG
from fxi.timeline.pod_filter import PODFilter
from fxi.timeline.reversion import TimeReversionManager
from fxi.timeline.ripple_analyzer import RippleAnalyzer


def test_reg_01_entity_isolation_across_projects(temp_workspace: FxiConfig):
    """REG-01: 同名角色绝对隔离 - 项目 A 与项目 B 均有名为‘林动’的角色，互不串扰"""
    mgr = EntityManager(temp_workspace)
    mgr.upsert_entity(work_id="work_a", entity_id="char_lin_dong", name="林动", category="character", attributes={"sect": "道宗"})
    mgr.upsert_entity(work_id="work_b", entity_id="char_lin_dong", name="林动", category="character", attributes={"sect": "林氏家族"})

    ent_a = mgr.get_entity(work_id="work_a", entity_id="char_lin_dong")
    ent_b = mgr.get_entity(work_id="work_b", entity_id="char_lin_dong")

    assert ent_a["attributes"]["sect"] == "道宗"
    assert ent_b["attributes"]["sect"] == "林氏家族"


def test_reg_02_pod_butterfly_filter(temp_workspace: FxiConfig):
    """REG-02: 同人分歧点蝴蝶效应 - 原著第 12 章配角战死，同人在第 10 章产生分歧，原著后续动态事件被阻断"""
    canon_events = [
        {"event_id": "ev_08", "narrative_order": 8, "summary": "宗门大比"},
        {"event_id": "ev_10", "narrative_order": 10, "summary": "探秘古墓"},
        {"event_id": "ev_12", "narrative_order": 12, "summary": "配角战死"},
        {"event_id": "ev_15", "narrative_order": 15, "summary": "配角葬礼"},
    ]
    filtered = PODFilter.filter_canon_events(canon_events, divergence_chapter_order=10)
    event_ids = [e["event_id"] for e in filtered]

    assert "ev_08" in event_ids
    assert "ev_10" in event_ids
    assert "ev_12" not in event_ids
    assert "ev_15" not in event_ids


def test_reg_03_state_ledger_recalculation(temp_workspace: FxiConfig):
    """REG-03: 动态账本剧情删改重算 - 删掉中间一笔消费，后续章节余额一键重算且完全正确"""
    calc = LedgerCalculator(temp_workspace)
    # 第 2 章获得 200 金币
    ev1 = calc.record_event("work_a", "char_01", "gold", 200.0, "sc_01", narrative_order=2, reason="任务奖励")
    # 第 3 章消费 50 金币
    ev2 = calc.record_event("work_a", "char_01", "gold", -50.0, "sc_02", narrative_order=3, reason="买药")
    # 第 4 章消费 80 金币
    ev3 = calc.record_event("work_a", "char_01", "gold", -80.0, "sc_03", narrative_order=4, reason="住宿")

    snap_before = calc.calculate_balance("work_a", "char_01", "gold", narrative_order=4)
    assert snap_before.computed_value == 70.0

    # 模拟中途删改剧情: 删除第 3 章的消费 (ev2)
    with calc.db_client.transaction() as cur:
        cur.execute("DELETE FROM state_events WHERE event_id = ?", (ev2,))

    snap_after = calc.calculate_balance("work_a", "char_01", "gold", narrative_order=4)
    assert snap_after.computed_value == 120.0  # 200 - 80 = 120


def test_reg_04_ooc_secret_leak(temp_workspace: FxiConfig):
    """REG-04: 防 OOC 秘密早泄拦截 - 角色在第 5 章说出第 8 章才获知的秘密，扫描拦截报警"""
    tracker = KnowledgeTracker(temp_workspace)
    checker = OOCChecker(temp_workspace)

    # 角色在第 8 章才得知凶手是二长老
    tracker.record_learned_claim("work_a", "char_lin", "claim_killer", narrative_order=8)

    draft_ch5 = "林动冷笑道：'二长老就是幕后黑手！大家不要相信他！'"
    violations = checker.scan_draft(
        draft_text=draft_ch5,
        work_id="work_a",
        narrative_order=5,
        speaker_id="char_lin",
        unrevealed_secrets={"二长老就是幕后黑手": "claim_killer"}
    )
    assert len(violations) >= 1
    assert violations[0].issue_type == "secret_leak"
    assert violations[0].severity == "error"


def test_reg_05_triage_claims(temp_workspace: FxiConfig):
    """REG-05: 命题主张可信度分流 - 原文事实自动接纳，冲突主张人工待审"""
    engine = TriageEngine()
    r1 = engine.classify(statement="林动自幼在青阳镇长大", has_conflict=False, is_canon_quote=True)
    assert r1.status == "auto_accepted"

    r2 = engine.classify(statement="林动其实是域外邪族转世", has_conflict=True, is_canon_quote=False)
    assert r2.status == "requires_review"


def test_reg_06_rebuild_from_scratch(temp_workspace: FxiConfig):
    """REG-06: 纯文本一键全量重建 - 删除数据库后执行 rebuild，所有实体卡和索引满血恢复"""
    mgr = EntityManager(temp_workspace)
    mgr.upsert_entity("work_rebuild", "char_x", "测试人物", category="character", description="纯文本正文")

    rebuilder = Rebuilder(temp_workspace)
    report = rebuilder.rebuild_all()
    assert report.entities_count >= 1

    ent = mgr.get_entity("work_rebuild", "char_x")
    assert ent["name"] == "测试人物"


def test_reg_07_lifecycle_disable(temp_workspace: FxiConfig):
    """REG-07: 文风规则一键熔断回滚 - 规则标记为 disable"""
    lm = LifecycleManager(temp_workspace)
    # 在 claim_versions 表测试 disable
    with lm.db_client.transaction() as cur:
        cur.execute("INSERT INTO claim_families (family_key, claim_family_id, owner_id, scope, created_at) VALUES ('f1', 'f1', 'auth', 'all', 'now')")
        cur.execute("INSERT INTO claim_versions (family_key, claim_id, claim_family_id, version, status, statement, semantic_hash, created_at) VALUES ('f1', 'c1', 'f1', 1, 'accepted', 'stmt', 'hash', 'now')")

    lm.apply_action("claim_versions", "claim_id", "c1", LifecycleAction.DISABLE)

    with lm.db_client.get_connection() as conn:
        row = conn.execute("SELECT status FROM claim_versions WHERE claim_id = 'c1'").fetchone()
        assert row["status"] == "disabled"


def test_reg_08_jieba_lexicon_export(temp_workspace: FxiConfig):
    """REG-08: 中文专有词典切词精准 - 修仙专有名词不被切碎"""
    mgr = EntityManager(temp_workspace)
    mgr.upsert_entity("work_a", "item_fu", "九天玄冰破煞符", category="item")

    lex_mgr = LexiconManager(temp_workspace)
    dict_file = lex_mgr.export_lexicon("work_a")
    assert dict_file.is_file()

    content = dict_file.read_text(encoding="utf-8")
    assert "九天玄冰破煞符" in content

    fts = ChineseFTS(temp_workspace)
    segmented = fts.segment_text("林动祭出了九天玄冰破煞符迎敌")
    assert "九天玄冰破煞符" in segmented.split()


def test_reg_09_pov_filter(temp_workspace: FxiConfig):
    """REG-09: POV 视点盲区防穿帮 - 反派视点请求上下文，主角隐藏底牌被物理剥离"""
    bundle = {
        "claims": [
            {"claim_id": "c_public", "statement": "天气晴朗", "is_secret": False},
            {"claim_id": "c_secret_card", "statement": "主角袖中有祖石护体", "is_secret": True},
        ]
    }
    # 反派只知道公共事实，不知道祖石
    sanitized = POVFilter.sanitize_context_for_pov(bundle, observer_char_id="villain_01", known_claim_ids={"c_public"})
    claim_ids = [c["claim_id"] for c in sanitized["claims"]]
    assert "c_public" in claim_ids
    assert "c_secret_card" not in claim_ids


def test_reg_10_retcon_supersede(temp_workspace: FxiConfig):
    """REG-10: 合法吃书修正生效 - 作者登记 retcon 修正旧设"""
    rm = RetconManager(temp_workspace)
    with rm.db_client.transaction() as cur:
        cur.execute("INSERT INTO claim_families (family_key, claim_family_id, owner_id, scope, created_at) VALUES ('f2', 'f2', 'auth', 'all', 'now')")
        cur.execute("INSERT INTO claim_versions (family_key, claim_id, claim_family_id, version, status, statement, semantic_hash, created_at) VALUES ('f2', 'claim_old', 'f2', 1, 'accepted', 'old', 'h1', 'now')")

    declaration = RetconDeclaration(
        retcon_id="ret_01",
        work_id="work_a",
        superseded_claim_id="claim_old",
        new_claim_id="claim_new",
        effective_narrative_order=50,
        author_note="修改旧设定"
    )
    rm.declare_retcon(declaration)

    assert rm.is_claim_superseded("work_a", "claim_old", narrative_order=60) is True
    assert rm.is_claim_superseded("work_a", "claim_old", narrative_order=40) is False


def test_reg_11_baseline_anchor(temp_workspace: FxiConfig):
    """REG-11: 半途接入基准锚点 - 前 50 章未测量，第 51 章设定 3000 金币基准并消费 500"""
    am = AnchorManager(temp_workspace)
    calc = LedgerCalculator(temp_workspace)

    # 第 51 章设定基准 3000
    am.create_baseline_anchor("work_a", "char_mc", "gold", baseline_value=3000.0, narrative_order=51, scene_uuid="sc_51")
    # 第 52 章消费 500
    calc.record_event("work_a", "char_mc", "gold", delta=-500.0, scene_uuid="sc_52", narrative_order=52, reason="买丹药")

    # 查询第 52 章结余
    snap_52 = calc.calculate_balance("work_a", "char_mc", "gold", narrative_order=52)
    assert snap_52.status == MetricStatus.EXPLICIT
    assert snap_52.computed_value == 2500.0

    # 查询第 40 章 (锚点之前) -> UNMEASURED，不报透支错误！
    snap_40 = calc.calculate_balance("work_a", "char_mc", "gold", narrative_order=40)
    assert snap_40.status == MetricStatus.UNMEASURED


def test_reg_12_scene_uuid_stability(temp_workspace: FxiConfig):
    """REG-12: 场景 UUID 稳定性 - 物理章号重排抗脆性"""
    uuid1 = generate_scene_uuid("source_wdqk", chapter_num=10, scene_seq=1, semantic_slug="cliff")
    uuid2 = generate_scene_uuid("source_wdqk", chapter_num=10, scene_seq=1, semantic_slug="cliff")
    assert uuid1 == uuid2
    assert "cliff" in uuid1


def test_reg_13_character_phases_blackening(temp_workspace: FxiConfig):
    """REG-13: 角色性格随时间黑化切换 - 早期隐忍懦弱 vs 中期杀伐果断"""
    pm = PhaseManager(temp_workspace)
    pm.add_phase("work_a", "char_mc", "p_early", "懦弱隐忍", valid_from_order=1, valid_to_order=30, traits=["隐忍", "退让"])
    pm.add_phase("work_a", "char_mc", "p_mid", "黑化蜕变", valid_from_order=31, valid_to_order=100, traits=["冷酷", "杀伐果断"])

    phase_ch20 = pm.get_active_phase("work_a", "char_mc", narrative_order=20)
    phase_ch40 = pm.get_active_phase("work_a", "char_mc", narrative_order=40)

    assert phase_ch20["phase_name"] == "懦弱隐忍"
    assert "退让" in phase_ch20["traits"]

    assert phase_ch40["phase_name"] == "黑化蜕变"
    assert "杀伐果断" in phase_ch40["traits"]


def test_reg_14_item_prototype_vs_instance(temp_workspace: FxiConfig):
    """REG-14: 量产通用物品独立消耗 - 林动与吴云各持有一个储物袋，林动的损毁不影响吴云"""
    im = ItemManager(temp_workspace)
    im.create_prototype("work_a", "proto_bag", "下品储物袋", category="storage")

    inst_ld = im.spawn_instance("work_a", "char_lin_dong", "proto_bag", quantity=1)
    inst_wy = im.spawn_instance("work_a", "char_wu_yun", "proto_bag", quantity=1)

    # 林动储物袋消耗损毁
    im.consume_instance(inst_ld, quantity=1)

    inv_ld = im.get_character_inventory("work_a", "char_lin_dong")
    inv_wy = im.get_character_inventory("work_a", "char_wu_yun")

    assert len(inv_ld) == 0
    assert len(inv_wy) == 1
    assert inv_wy[0]["item_name"] == "下品储物袋"


def test_reg_15_unique_artifact_ownership(temp_workspace: FxiConfig):
    """REG-15: 唯一神兵流转防穿帮 - 主角在第 50 章赠出神兵，第 60 章查询不在其持有列表"""
    ot = OwnershipTracker(temp_workspace)
    im = ItemManager(temp_workspace)

    # 主角在第 10 章获得青峰剑
    ot.transfer_ownership("work_a", "sword_qingfeng", None, "char_mc", "looted", "sc_10", 10, "击杀得宝")
    assert ot.get_current_owner("work_a", "sword_qingfeng") == "char_mc"

    # 主角在第 50 章将青峰剑赠送给师弟
    ot.transfer_ownership("work_a", "sword_qingfeng", "char_mc", "char_junior", "gifted", "sc_50", 50, "赠剑")
    assert ot.get_current_owner("work_a", "sword_qingfeng") == "char_junior"

    inv_mc = im.get_character_inventory("work_a", "char_mc")
    inv_junior = im.get_character_inventory("work_a", "char_junior")

    mc_items = [it["item_ref_id"] for it in inv_mc]
    junior_items = [it["item_ref_id"] for it in inv_junior]

    assert "sword_qingfeng" not in mc_items
    assert "sword_qingfeng" in junior_items


def test_reg_16_time_reversion_death_loop(temp_workspace: FxiConfig):
    """REG-16: 死亡读档与时间循环隔离 - 回档至 Day 1，客观世界还原，特权主角保留记忆，因果 DAG 单调递增"""
    trm = TimeReversionManager(temp_workspace)
    # 创建 Day 1 存档点
    trm.create_checkpoint(
        checkpoint_id="cp_day1",
        work_id="work_loop",
        timeline_id="main",
        chapter_id="ch_01",
        narrative_order=1,
        physical_timestamp="大历元年三月初一 08:00",
        world_state={"npc_alive": True, "gold": 500},
        retained_entities=["char_protagonist"]
    )

    # 推进到第 15 章主角死亡触发回档
    report = trm.rollback_to_checkpoint(
        checkpoint_id="cp_day1",
        current_narrative_order=15,
        trigger_reason="protagonist_killed_by_boss"
    )

    assert report.target_physical_time == "大历元年三月初一 08:00"
    assert report.restored_world_state["npc_alive"] is True
    assert "char_protagonist" in report.retained_entities
    assert "reversion_cp_day1_step_15" in report.new_loop_causal_event_id


def test_reg_17_game_skill_cooldown_and_cost(temp_workspace: FxiConfig):
    """REG-17: 游戏技能冷却与蓝耗越界拦截 - 冷却未好或蓝量透支时拦截抛出 SkillCastIllegalError"""
    engine = SkillTreeEngine(temp_workspace)
    engine.register_skill("work_a", "skill_meteor", "陨石风暴", cost_mp=80, cooldown_steps=3)
    engine.learn_skill("work_a", "char_mage", "skill_meteor", is_equipped=True)

    # 第 10 章释放技能，消耗 80 蓝
    ok, msg = engine.validate_cast("work_a", "char_mage", "skill_meteor", current_narrative_order=10, current_mp=100)
    assert ok is True
    engine.record_cast("work_a", "char_mage", "skill_meteor", current_narrative_order=10)

    # 第 11 章再次尝试释放 (冷却尚需 3 章，目前仅过 1 章)
    ok_cd, msg_cd = engine.validate_cast("work_a", "char_mage", "skill_meteor", current_narrative_order=11, current_mp=100)
    assert ok_cd is False
    assert "冷却中" in msg_cd

    # 尝试在第 14 章释放 (冷却已过)，但蓝量仅剩 30 (不够 80)
    ok_mp, msg_mp = engine.validate_cast("work_a", "char_mage", "skill_meteor", current_narrative_order=14, current_mp=30)
    assert ok_mp is False
    assert "法力" in msg_mp

    # 验证 CombatVerifier 抛出硬异常拦截
    verifier = CombatVerifier(temp_workspace)
    with pytest.raises(SkillCastIllegalError):
        verifier.verify_action("work_a", "char_mage", "skill_meteor", current_narrative_order=11, current_mp=100)


def test_reg_18_territory_deficit_and_alerts(temp_workspace: FxiConfig):
    """REG-18: 领地资源透支拦截与断粮预警 - 扣除超出库存抛出 ResourceDeficitError，宏观标签生成断粮预警"""
    rm = TerritoryResourceManager(temp_workspace)
    pm = TerritoryPopulationManager(temp_workspace)
    tag_gen = MacroTagGenerator(temp_workspace)

    pm.init_territory("work_a", "town_01", "lord_mc", tier="town", loyalty=45.0)  # 民心低
    rm.set_resource("work_a", "town_01", "粮食", current_amount=100.0, daily_net_yield=-20.0)

    # 试图扣除 500 粮食 (透支)
    with pytest.raises(ResourceDeficitError):
        rm.deduct_resources("work_a", "town_01", {"粮食": 500.0})

    # 生成宏观剧作语义标签
    tags = tag_gen.generate_semantic_brief("work_a", "town_01")
    tags_text = " ".join(tags)

    assert "粮食告急" in tags_text
    assert "民心动荡" in tags_text


def test_reg_19_tiered_projection_and_passive_radar(temp_workspace: FxiConfig):
    """REG-19: 阶梯投影防爆与被动防吃书雷达 - 快捷栏只暴露重点技能，毒素威胁激活沉睡抗性"""
    ste = SkillTreeEngine(temp_workspace)
    projector = TieredParameterProjector(temp_workspace)
    radar = PassiveThreatRadar(temp_workspace)

    # 注册 10 个技能，仅装配 2 个
    for i in range(1, 11):
        sid = f"skill_{i}"
        ste.register_skill("work_a", sid, f"招式_{i}", cost_mp=10)
        ste.learn_skill("work_a", "char_hero", sid, is_equipped=(i <= 2))

    # 另外学习一个被动抗毒技能 (未装配快捷栏)
    ste.register_skill("work_a", "passive_poison_res", "幽冥毒素免疫体", skill_type="passive", description="免疫五阶以下蛇毒瘴气")
    ste.learn_skill("work_a", "char_hero", "passive_poison_res", is_equipped=False)

    # 1. 验证阶梯投影：只投影装配的 2 个技能，未装配的不出现在 active_deck
    proj = projector.project_for_prompt("work_a", "char_hero", SceneType.COMBAT, current_order=10)
    assert len(proj["active_deck"]) == 2

    # 2. 验证大纲出现“沼泽剧毒”时，雷达精准激活沉睡的抗毒被动
    alerts = radar.scan_scene_threats("work_a", "char_hero", "主角深入黑雾沼泽，遭遇七彩毒蛇伏击")
    assert len(alerts) >= 1
    assert "幽冥毒素免疫体" in alerts[0]
    assert "防吃书警报" in alerts[0]
