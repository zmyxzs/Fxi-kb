from __future__ import annotations

from pathlib import Path

import pytest

from fxi.core.config import FxiConfig
from fxi.domain.entities import EntityManager
from fxi.game_engine.scene_radar import SceneFocusRadar


@pytest.fixture
def isolated_config(tmp_path: Path) -> FxiConfig:
    root = tmp_path
    data_dir = root / "data"
    config = FxiConfig(
        workspace_root=root,
        data_dir=data_dir,
        projects_dir=root / "projects",
        skills_dir=root / "skills",
        sources_dir=root / "sources",
        materials_dir=root / "materials",
        sqlite_path=data_dir / "manifest.sqlite",
        cache_db_path=data_dir / "cache.sqlite",
        jieba_custom_dict_path=data_dir / "project_lexicon.txt",
    )
    config.ensure_directories()
    yield config


def _voice_profile(reply: str) -> dict[str, object]:
    return {
        "name": "角色甲",
        "tone": "克制清醒",
        "speech_style": "短句、直白、先说结论",
        "catchphrases": ["先活下来"],
        "gestures": ["说话前先观察周围"],
        "taboos": ["不无故热血咆哮"],
        "dialogue_samples": [
            {
                "context": "面对危险时",
                "user": "现在冲过去吗？",
                "reply": reply,
            }
        ],
    }


def _register_character(
    config: FxiConfig, work_id: str, voice_profile: dict[str, object]
) -> None:
    persona_id = "persona_char_alpha_default"
    EntityManager(config).upsert_entity(
        work_id=work_id,
        entity_id="char_alpha",
        name="角色甲",
        aliases=["甲"],
        attributes={
            "trinity": {
                "active_persona_id": persona_id,
                "personas": {
                    persona_id: {
                        "voice_profile": voice_profile,
                    }
                },
            }
        },
    )


def test_scene_radar_reads_bound_persona_voice_fields(isolated_config: FxiConfig):
    voice = _voice_profile("先观察，再决定。")
    _register_character(isolated_config, "work_alpha", voice)

    result = SceneFocusRadar(isolated_config).scan_scene_context(
        work_id="work_alpha",
        scene_type="dialogue",
        pov_character_id="角色甲",
        characters=["甲"],
    )

    assert result["dialogue_samples"]["角色甲"] == voice["dialogue_samples"]
    brief = result["character_voice_briefs"]["角色甲"]
    assert brief["tone"] == "克制清醒"
    assert brief["speech_style"] == "短句、直白、先说结论"
    assert brief["catchphrases"] == ["先活下来"]
    assert brief["gestures"] == ["说话前先观察周围"]
    assert brief["taboos"] == ["不无故热血咆哮"]


def test_scene_radar_does_not_fallback_to_another_work(isolated_config: FxiConfig):
    _register_character(isolated_config, "work_alpha", _voice_profile("只属于甲作品。"))

    result = SceneFocusRadar(isolated_config).scan_scene_context(
        work_id="work_beta",
        scene_type="dialogue",
        pov_character_id="角色甲",
        characters=["角色甲"],
    )

    assert result["dialogue_samples"] == {}
    assert result["character_voice_briefs"] == {}
    assert result["formatted_dialogue_section"] == ""


def test_scene_radar_rejects_invalid_work_id(isolated_config: FxiConfig):
    radar = SceneFocusRadar(isolated_config)

    with pytest.raises(ValueError, match="work_id"):
        radar.scan_scene_context(
            work_id="../work_alpha",
            scene_type="dialogue",
            pov_character_id="角色甲",
        )
