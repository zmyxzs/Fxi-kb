"""
tests.test_complex_entity_modeling - 复杂实体建模测试 (夺舍/双魂/多重人格/身外化身)
"""

import pytest
from pathlib import Path

from fxi.core.config import FxiConfig
from fxi.domain.entities import EntityManager, Persona, Soul, Vessel


@pytest.fixture
def test_mgr(temp_workspace: FxiConfig):
    """创建隔离的临时测试环境"""
    return EntityManager(temp_workspace)


def test_possession_and_pov_redaction(test_mgr: EntityManager):
    """测试 1: 夺舍 (Possession) - 肉身与灵魂分离，外人 POV 严格物理脱敏，本尊 POV 拥有真相"""
    work_id = "test_novel"
    entity_id = "char_lin_ping"

    old_demon_soul = Soul(
        soul_id="soul_old_demon",
        true_name="噬天魔尊",
        realm="仙尊残魂",
        core_secrets=["掌握九幽魔功", "万年前被正道偷袭身陨"],
        dao_laws=["吞噬法则", "寂灭魔道"],
    )
    vessel = Vessel(
        vessel_id="vessel_lin_ping",
        name="林平",
        location="七星宗柴房",
        physique="凡人体质",
        appearance="身材消瘦，脸色苍白的少年",
    )
    disguise_persona = Persona(
        persona_id="persona_disguise",
        display_name="林平",
        claimed_identity="七星宗杂役弟子林平",
        speech_channel="physical_dialogue",
    )

    test_mgr.configure_possession(
        work_id=work_id,
        entity_id=entity_id,
        soul=old_demon_soul,
        vessel=vessel,
        disguise_persona=disguise_persona,
    )

    # 1. 宗门外人视角 (例如外门执事 char_deacon)
    observer_pov = test_mgr.get_pov_entity_view(
        work_id=work_id,
        observer_char_id="char_deacon",
        target_entity_id=entity_id,
    )
    assert observer_pov["name"] == "林平"
    assert observer_pov["claimed_identity"] == "七星宗杂役弟子林平"
    assert observer_pov["is_possessed"] is False
    assert observer_pov["is_redacted"] is True
    assert observer_pov["active_soul"] is None
    assert observer_pov["core_secrets"] == []
    assert observer_pov["dao_laws"] == []
    assert observer_pov["active_vessel"]["physique"] == "凡人体质"

    # 2. 魔尊本尊视角 (以自身 ID 查看)
    self_pov = test_mgr.get_pov_entity_view(
        work_id=work_id,
        observer_char_id=entity_id,
        target_entity_id=entity_id,
    )
    assert self_pov["name"] == "噬天魔尊"
    assert self_pov["is_possessed"] is True
    assert self_pov["is_redacted"] is False
    assert self_pov["active_soul"]["soul_id"] == "soul_old_demon"
    assert "掌握九幽魔功" in self_pov["active_soul"]["core_secrets"]
    assert "吞噬法则" in self_pov["active_soul"]["dao_laws"]

    # 3. 识破机密的第三方视角 (持有透露 claim)
    revealed_pov = test_mgr.get_pov_entity_view(
        work_id=work_id,
        observer_char_id="char_rival",
        target_entity_id=entity_id,
        known_claim_ids={f"{entity_id}_possession_revealed"},
    )
    assert revealed_pov["name"] == "噬天魔尊"
    assert revealed_pov["is_possessed"] is True
    assert revealed_pov["is_redacted"] is False
    assert revealed_pov["active_soul"]["realm"] == "仙尊残魂"


def test_dual_souls_dialogue_channel_isolation(test_mgr: EntityManager):
    """测试 2: 双魂共生 (Dual Souls) - 严格发声信道校验，防残魂违规物理发声"""
    work_id = "test_novel"
    entity_id = "char_xiao_yan"

    xiao_yan_soul = Soul(soul_id="soul_xiao_yan", true_name="萧炎")
    yao_lao_soul = Soul(soul_id="soul_yao_lao", true_name="药老")
    vessel = Vessel(vessel_id="vessel_xiao_yan", name="萧炎")

    test_mgr.configure_dual_souls(
        work_id=work_id,
        entity_id=entity_id,
        vessel=vessel,
        primary_soul=xiao_yan_soul,
        symbiotic_soul=yao_lao_soul,
        active_controller_soul_id="soul_xiao_yan",
    )

    # 1. 萧炎为主控时
    ok, _ = test_mgr.validate_dialogue_channel(
        work_id, entity_id, speaker_soul_id="soul_xiao_yan", channel="physical_dialogue"
    )
    assert ok is True

    # 药老尝试物理发声 -> 被拦截阻断
    ok, reason = test_mgr.validate_dialogue_channel(
        work_id, entity_id, speaker_soul_id="soul_yao_lao", channel="physical_dialogue"
    )
    assert ok is False
    assert "严禁物理发声" in reason

    # 药老使用脑内灵识传音 -> 允许
    ok, _ = test_mgr.validate_dialogue_channel(
        work_id, entity_id, speaker_soul_id="soul_yao_lao", channel="telepathy_inner"
    )
    assert ok is True

    # 2. 药老接管肉身代打
    test_mgr.switch_soul_controller(work_id, entity_id, new_controller_soul_id="soul_yao_lao")

    # 此时药老可以物理发声
    ok, _ = test_mgr.validate_dialogue_channel(
        work_id, entity_id, speaker_soul_id="soul_yao_lao", channel="physical_dialogue"
    )
    assert ok is True

    # 萧炎退居脑海，尝试物理发声被拦截
    ok, reason = test_mgr.validate_dialogue_channel(
        work_id, entity_id, speaker_soul_id="soul_xiao_yan", channel="physical_dialogue"
    )
    assert ok is False


def test_split_persona_and_voice_adaptation(test_mgr: EntityManager):
    """测试 3: 多重人格 (Split Persona) - 相态切换与动态声线规则响应"""
    work_id = "test_novel"
    entity_id = "char_split"

    test_mgr.upsert_entity(
        work_id=work_id,
        entity_id=entity_id,
        name="无名修士",
    )

    scholar_persona = Persona(
        persona_id="persona_scholar",
        display_name="儒雅书生",
        claimed_identity="游方书生",
        voice_profile={"tone": "谦逊温和", "catchphrases": ["子曰", "非礼勿视"]},
        anti_behaviors=["暴戾粗口", "残杀无辜"],
    )
    asura_persona = Persona(
        persona_id="persona_asura",
        display_name="修罗战狂",
        claimed_identity="幽冥修罗",
        voice_profile={"tone": "冷酷嗜杀", "catchphrases": ["屠尽九幽", "蝼蚁受死"]},
        anti_behaviors=["摇尾乞怜", "讲经说道"],
    )

    test_mgr.add_persona(work_id, entity_id, scholar_persona)
    test_mgr.add_persona(work_id, entity_id, asura_persona)

    # 1. 默认或切换为书生
    test_mgr.switch_persona(work_id, entity_id, "persona_scholar")
    voice = test_mgr.get_character_voice(work_id, entity_id)
    assert voice["tone"] == "谦逊温和"
    assert "子曰" in voice["catchphrases"]

    # 2. 切换为修罗人格
    test_mgr.switch_persona(work_id, entity_id, "persona_asura")
    voice = test_mgr.get_character_voice(work_id, entity_id)
    assert voice["tone"] == "冷酷嗜杀"
    assert "屠尽九幽" in voice["catchphrases"]


def test_avatar_cognition_and_causal_sync(test_mgr: EntityManager):
    """测试 4: 身外化身 (Avatars) - 跨空间地理独立，经历隔离与因果同步合流"""
    work_id = "test_novel"
    main_entity_id = "char_main_body"

    test_mgr.upsert_entity(
        work_id=work_id,
        entity_id=main_entity_id,
        name="林动",
    )

    # 创建魔界分身
    avatar_vessel = Vessel(
        vessel_id="vessel_avatar_demon",
        name="魔相分身",
        location="魔界无尽火域",
    )
    test_mgr.create_avatar(
        work_id=work_id,
        main_entity_id=main_entity_id,
        avatar_id="avatar_demon",
        avatar_vessel=avatar_vessel,
    )

    # 分身在魔界发生关键因果经历
    test_mgr.record_avatar_experience(
        work_id=work_id,
        main_entity_id=main_entity_id,
        avatar_id="avatar_demon",
        memory_entry="探明异魔皇第三道封印已破碎",
    )

    # 检查本尊状态：因果未同步前，本尊灵魂记忆为空
    trinity = test_mgr.get_trinity_state(work_id, main_entity_id)
    active_soul_id = trinity["active_soul_id"]
    main_soul_memories = trinity["souls"][active_soul_id].get("memories", [])
    assert "探明异魔皇第三道封印已破碎" not in main_soul_memories
    assert trinity["avatars"]["avatar_demon"]["synced"] is False

    # 因果合流同步
    synced = test_mgr.sync_avatar_memories(work_id, main_entity_id, "avatar_demon")
    assert "探明异魔皇第三道封印已破碎" in synced

    # 重新检查本尊灵魂：已获得分身经历
    trinity_after = test_mgr.get_trinity_state(work_id, main_entity_id)
    updated_memories = trinity_after["souls"][active_soul_id]["memories"]
    assert "探明异魔皇第三道封印已破碎" in updated_memories
    assert trinity_after["avatars"]["avatar_demon"]["synced"] is True


def test_entity_abilities_lifecycle_and_temporal_query(test_mgr: EntityManager):
    """测试 4: 实体超凡能力时序索引与章节时间锚点精准查询"""
    work_id = "test_abilities_novel"
    entity_id = "char_lin_qiye"

    test_mgr.upsert_entity(
        work_id=work_id,
        entity_id=entity_id,
        name="林七夜",
        aliases=["达纳都斯", "双神代理人"],
    )

    # 模拟写入 entity_abilities 表
    with test_mgr.db_client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO entity_abilities
            (ability_id, work_id, entity_id, ability_name, category, sequence_num, valid_from_chapter, valid_to_chapter, effect_description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ab_fanchen", work_id, entity_id, "凡尘神域", "divine_domain", "003", 101, None, "沧南市奇迹神墟"),
        )
        cur.execute(
            """
            INSERT INTO entity_abilities
            (ability_id, work_id, entity_id, ability_name, category, sequence_num, valid_from_chapter, valid_to_chapter, effect_description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ab_zhian", work_id, entity_id, "至暗神墟", "divine_domain", "002", 121, None, "黑夜女神至暗神墟"),
        )
        cur.execute(
            """
            INSERT INTO entity_abilities
            (ability_id, work_id, entity_id, ability_name, category, sequence_num, valid_from_chapter, valid_to_chapter, effect_description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ab_shige", work_id, entity_id, "诗成剑气", "combat", None, 281, None, "斋戒所诗歌化剑"),
        )

    # 1. 全篇查询（支持角色名反查）
    all_abs = test_mgr.get_entity_abilities(work_id, "林七夜")
    assert len(all_abs) == 3
    assert [a["ability_name"] for a in all_abs] == ["凡尘神域", "至暗神墟", "诗成剑气"]
    assert all_abs[0]["sequence_num"] == "003"

    # 2. 早期章节时间锚点：第 50 章尚未觉醒神墟
    abs_ch50 = test_mgr.get_entity_abilities(work_id, "林七夜", chapter=50)
    assert len(abs_ch50) == 0

    # 3. 中期章节时间锚点：第 110 章已获得凡尘神域，但至暗神墟尚未展开
    abs_ch110 = test_mgr.get_entity_abilities(work_id, "林七夜", chapter=110)
    assert len(abs_ch110) == 1
    assert abs_ch110[0]["ability_name"] == "凡尘神域"

    # 4. 第一次神战时间锚点：第 268 章已掌握凡尘与至暗，但尚未进入斋戒所（诗成剑气在 281 章）
    abs_ch268 = test_mgr.get_entity_abilities(work_id, "林七夜", chapter=268)
    assert len(abs_ch268) == 2
    names_268 = {a["ability_name"] for a in abs_ch268}
    assert names_268 == {"凡尘神域", "至暗神墟"}
    assert "诗成剑气" not in names_268
