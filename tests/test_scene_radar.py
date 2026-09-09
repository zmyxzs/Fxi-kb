import pytest
from fxi.domain.entities import EntityManager
from fxi.core.types import SceneType
from fxi.game_engine.scene_radar import SceneFocusRadar
from fxi.index_retrieval.context_pruner import ContextPruner


def _seed_scene_fixture(config):
    entity_manager = EntityManager(config)
    entity_manager.upsert_entity(
        "zhanshen_fanfic",
        "char_lin_qiye",
        name="林七夜",
        category="character",
        voice_profile={
            "tone": "通透冷静，带一点自嘲",
            "speech_style": "言简意赅的大白话",
            "catchphrases": ["先活下来再说"],
            "gestures": ["插兜"],
            "taboos": ["空泛说教"],
            "dialogue_samples": [
                {
                    "context": "面对告别",
                    "user": "你不怕死吗？",
                    "reply": "怕，所以更不能让别人替我养家。",
                }
            ],
        },
    )
    entity_manager.upsert_entity(
        "zhanshen_fanfic",
        "char_zhao_kongcheng",
        name="赵空城",
        category="character",
        voice_profile={
            "tone": "粗粝沧桑又护短",
            "speech_style": "接地气的短句",
            "catchphrases": ["小子，站稳了"],
            "gestures": ["拍肩"],
            "taboos": ["油腻演说"],
            "dialogue_samples": [
                {
                    "context": "面对告别",
                    "user": "赵叔，保重。",
                    "reply": "少废话，先把自己照顾好。",
                }
            ],
        },
    )
    entity_manager.upsert_entity(
        "zhanshen_fanfic",
        "item_shouyeren_wenzhang",
        name="守夜人纹章",
        category="item",
        attributes={
            "symbolic_text": "若黯夜终临，吾必立于万万人前",
            "interaction_rituals": ["指腹摩挲纹章刻痕"],
        },
    )


def test_scene_focus_radar_dialogue(temp_workspace):
    _seed_scene_fixture(temp_workspace)
    radar = SceneFocusRadar(temp_workspace)
    res = radar.scan_scene_context(
        work_id="zhanshen_fanfic",
        scene_type="dialogue",
        pov_character_id="林七夜",
        characters=["林七夜", "赵空城"],
        props=["守夜人纹章", "牛皮纸"],
        events=["赵空城向林七夜告别", "赵空城掏出牛皮纸", "林七夜抚摸纹章"],
    )

    assert res["is_dialogue_oriented"] is True
    assert res["suppress_active_deck"] is True
    assert "林七夜" in res["dialogue_samples"]
    assert "赵空城" in res["dialogue_samples"]

    # 验证提取的对白样板具有反套路特色
    samples_lin = res["dialogue_samples"]["林七夜"]
    assert any("容易死" in s.get("reply", "") or "养家" in s.get("reply", "") for s in samples_lin)

    # 验证守夜人纹章图腾与誓词
    assert len(res["symbolic_props"]) >= 1
    shouyeren_prop = next(p for p in res["symbolic_props"] if "纹章" in p["name"])
    assert "若黯夜终临" in shouyeren_prop["symbolic_text"]


def test_context_pruner_scene_separation(temp_workspace):
    _seed_scene_fixture(temp_workspace)
    pruner = ContextPruner(temp_workspace)

    # 1. 文戏场景：屏蔽战斗快捷招式面板，注入对白切片与图腾
    dialogue_context = pruner.assemble_and_prune(
        work_id="zhanshen_fanfic",
        scene_type=SceneType.DIALOGUE,
        pov_character_id="林七夜",
        current_narrative_order=220,
        characters=["林七夜", "赵空城"],
        props=["守夜人纹章"],
        events=["告别"],
    )
    assert "Active Deck" not in dialogue_context
    assert "角色音色切片" in dialogue_context
    assert "若黯夜终临" in dialogue_context

    # 2. 战斗场景：注入战斗快捷招式，屏蔽对白样本
    combat_context = pruner.assemble_and_prune(
        work_id="zhanshen_fanfic",
        scene_type=SceneType.COMBAT,
        pov_character_id="林七夜",
        current_narrative_order=220,
        characters=["林七夜"],
    )
    assert "角色音色切片" not in combat_context
