"""被动威胁雷达的作品配置、题材与技能匹配边界回归。"""

import yaml

from fxi.game_engine.passive_radar import PassiveThreatRadar
from fxi.game_engine.skills import SkillTreeEngine


def _write_work(config, work_id: str, payload: dict) -> None:
    path = config.projects_dir / work_id / "work.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_missing_work_config_is_explicit_and_has_no_keyword_fallback(temp_workspace):
    skills = SkillTreeEngine(temp_workspace)
    skills.register_skill(
        "work_a",
        "passive_poison_res",
        "幽冥毒素免疫体",
        skill_type="passive",
        description="免疫蛇毒瘴气",
    )
    skills.learn_skill("work_a", "char_hero", "passive_poison_res")

    radar = PassiveThreatRadar(temp_workspace)
    alerts = radar.scan_scene_threats(
        "work_a",
        "char_hero",
        "主角深入黑雾沼泽，遭遇七彩毒蛇伏击",
    )

    assert alerts == []
    assert radar.last_status == {
        "work_id": "work_a",
        "status": "INCOMPLETE",
        "error_code": "WORK_CONFIG_MISSING",
    }


def test_dynamic_genre_rules_match_skill_name_or_description_per_work(temp_workspace):
    _write_work(
        temp_workspace,
        "work_a",
        {
            "work_id": "work_a",
            "worldview_genre": "xianxia",
            "threat_keywords": {
                "xianxia": {
                    "毒素": ["七彩毒蛇", "沼泽剧毒"],
                },
                "urban": {
                    "辐射": ["辐射泄漏"],
                },
            },
        },
    )
    _write_work(
        temp_workspace,
        "work_b",
        {
            "work_id": "work_b",
            "worldview_genre": "urban",
            "threat_keywords": {
                "xianxia": {
                    "毒素": ["七彩毒蛇", "沼泽剧毒"],
                },
                "urban": {
                    "辐射": ["辐射泄漏"],
                },
            },
        },
    )

    skills = SkillTreeEngine(temp_workspace)
    skills.register_skill(
        "work_a",
        "passive_name_match",
        "幽冥毒素免疫体",
        skill_type="passive",
    )
    skills.learn_skill("work_a", "char_hero", "passive_name_match")
    skills.register_skill(
        "work_a",
        "passive_description_match",
        "护体结界",
        skill_type="passive",
        description="抵抗毒素侵袭",
    )
    skills.learn_skill("work_a", "char_hero", "passive_description_match")

    # 同名角色和技能只存在于 work_b 的独立技能表，不得改变 work_a 的结果。
    skills.register_skill(
        "work_b",
        "passive_name_match",
        "幽冥毒素免疫体",
        skill_type="passive",
    )
    skills.learn_skill("work_b", "char_hero", "passive_name_match")

    radar = PassiveThreatRadar(temp_workspace)
    poison_scene = "主角深入黑雾沼泽，遭遇七彩毒蛇，四周弥漫沼泽剧毒"
    work_a_alerts = radar.scan_scene_threats("work_a", "char_hero", poison_scene)

    assert len(work_a_alerts) == 2
    assert any("幽冥毒素免疫体" in alert for alert in work_a_alerts)
    assert any("护体结界" in alert for alert in work_a_alerts)
    assert radar.last_status == {
        "work_id": "work_a",
        "worldview_genre": "xianxia",
        "status": "AVAILABLE",
    }

    # work_b 只启用 urban 题材的辐射规则，不能继承 work_a 的毒素规则。
    assert radar.scan_scene_threats("work_b", "char_hero", poison_scene) == []
    assert radar.last_status == {
        "work_id": "work_b",
        "worldview_genre": "urban",
        "status": "AVAILABLE",
    }
