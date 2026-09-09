from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from fxi.core.config import FxiConfig
from fxi.domain.entities import EntityManager
from fxi.game_engine.attributes import AttributeCalculator
from fxi.game_engine.passive_radar import PassiveThreatRadar
from fxi.game_engine.scene_radar import SceneFocusRadar
from fxi.game_engine.skills import SkillTreeEngine
from fxi.materials_skills.candidate_store import package_hash
from fxi.materials_skills.style_manager import StyleManager
from fxi.storage.sqlite_client import DatabaseClient
from fxi.territory.macro_tags import MacroTagGenerator
from fxi.territory.population import TerritoryPopulationManager


@pytest.fixture
def temp_workspace(tmp_path: Path):
    """Provide an isolated workspace managed by pytest's temporary-path fixture."""
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
    db = DatabaseClient(config.sqlite_path)
    from fxi.storage.sqlite_client import ensure_work

    with db.transaction() as cur:
        for work_id in ("work_a", "work_b", "work_base", "work_overlay"):
            ensure_work(cur, work_id)
    yield config


def _write_work(config, work_id: str, payload: dict) -> None:
    path = config.projects_dir / work_id / "work.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_effective_state_only_uses_explicit_base_work(temp_workspace):
    manager = EntityManager(temp_workspace)
    manager.upsert_entity("work_base", "char_actor", "Actor")
    manager.upsert_entity("work_overlay", "char_actor", "Actor")
    db = DatabaseClient(temp_workspace.sqlite_path)
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO entity_abilities
            (ability_id, work_id, entity_id, ability_name, category, valid_from_chapter,
             effect_description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("ability_base", "work_base", "char_actor", "Base Ability", "innate", 1, "configured"),
        )

    isolated = manager.get_effective_state("work_overlay", "char_actor", chapter=1)
    explicit = manager.get_effective_state(
        "work_overlay", "char_actor", chapter=1, base_work_id="work_base"
    )
    assert isolated["canon_abilities_count"] == 0
    assert explicit["canon_abilities_count"] == 1
    assert explicit["source_work_id"] == "work_base"


def test_scene_and_threat_reads_are_work_scoped_and_configured(temp_workspace):
    manager = EntityManager(temp_workspace)
    manager.upsert_entity(
        "work_base",
        "item_relic",
        "Relic",
        category="item",
        aliases=["old relic"],
        attributes={"symbolic_text": "base-only", "interaction_rituals": ["touch"]},
    )
    radar = SceneFocusRadar(temp_workspace)
    scene = radar.scan_scene_context(
        "work_overlay",
        "investigation",
        "char_actor",
        props=["Relic"],
    )
    assert scene["work_id"] == "work_overlay"
    assert scene["symbolic_props"] == []

    _write_work(
        temp_workspace,
        "work_overlay",
        {
            "work_id": "work_overlay",
            "worldview_genre": "genre_alpha",
            "threat_keywords": {"radiation": ["radiation", "辐射"]},
        },
    )
    skills = SkillTreeEngine(temp_workspace)
    skills.register_skill(
        "work_overlay",
        "skill_guard",
        "Radiation Guard",
        skill_type="passive",
        description="radiation resistance",
    )
    skills.learn_skill("work_overlay", "char_actor", "skill_guard")
    alerts = PassiveThreatRadar(temp_workspace).scan_scene_threats(
        "work_overlay", "char_actor", "radiation leak"
    )
    assert alerts and "Radiation Guard" in alerts[0]
    assert PassiveThreatRadar(temp_workspace).scan_scene_threats(
        "work_base", "char_actor", "radiation leak"
    ) == []


def test_attributes_are_configured_and_safe_without_rpg_fallback(temp_workspace):
    _write_work(
        temp_workspace,
        "work_a",
        {
            "work_id": "work_a",
            "worldview_genre": "genre_alpha",
            "attribute_formulas": {
                "genre_alpha": {
                    "power": "strength * 3",
                    "resilience": {"formula": "max(constitution, 1) + 2"},
                }
            },
        },
    )
    result = AttributeCalculator.compute_derived_stats(
        {"力量": 4, "体质": 5}, work_id="work_a", config=temp_workspace
    )
    assert result == {"power": 12, "resilience": 7}

    missing = AttributeCalculator.compute_derived_stats({"strength": 10}, work_id="work_b", config=temp_workspace)
    assert missing["status"] == "INCOMPLETE"
    assert "attack" not in missing
    _write_work(
        temp_workspace,
        "work_unsafe",
        {
            "work_id": "work_unsafe",
            "worldview_genre": "genre_alpha",
            "attribute_formulas": {"genre_alpha": {"power": "__import__('os').system('bad')"}},
        },
    )
    unsafe = AttributeCalculator.compute_derived_stats(
        {"strength": 10}, work_id="work_unsafe", config=temp_workspace
    )
    assert unsafe["status"] == "INCOMPLETE"


def test_skills_require_learning_equipment_mp_and_cooldown(temp_workspace):
    engine = SkillTreeEngine(temp_workspace)
    engine.register_skill("work_a", "skill_alpha", "Alpha", cost_mp=30, cooldown_steps=2)
    engine.learn_skill("work_a", "char_actor", "skill_alpha", is_equipped=False)

    not_equipped, reason = engine.validate_cast("work_a", "char_actor", "skill_alpha", 3, 100)
    assert not not_equipped and "装备" in reason
    engine.equip_skill("work_a", "char_actor", "skill_alpha")
    not_enough, reason = engine.validate_cast("work_a", "char_actor", "skill_alpha", 3, 20)
    assert not not_enough and "法力" in reason

    ready, _ = engine.validate_cast("work_a", "char_actor", "skill_alpha", 3, 50)
    assert ready
    remaining = engine.record_cast("work_a", "char_actor", "skill_alpha", 3, current_mp=50)
    assert remaining == 20
    cooldown, reason = engine.validate_cast("work_a", "char_actor", "skill_alpha", 4, 50)
    assert not cooldown and "冷却" in reason
    cast = engine.cast_skill("work_a", "char_actor", "skill_alpha", 5, 50)
    assert cast["remaining_mp"] == 20
    assert cast["recorded"] is True


def test_missing_territory_data_is_incomplete_not_stable(temp_workspace):
    tags = MacroTagGenerator(temp_workspace).generate_semantic_brief("work_a", "missing")
    assert tags and tags[0].startswith("INCOMPLETE:")
    assert "运转井然" not in " ".join(tags)

    _write_work(
        temp_workspace,
        "work_a",
        {
            "work_id": "work_a",
            "worldview_genre": "genre_alpha",
            "macro_tags": {"labels": {"stable": "CUSTOM STABLE"}},
            "territory_defaults": {"population": 50, "soldiers": 5, "tax_rate": 0.2},
        },
    )
    TerritoryPopulationManager(temp_workspace).init_territory("work_a", "site_a", "char_actor")
    tags = MacroTagGenerator(temp_workspace).generate_semantic_brief("work_a", "site_a")
    assert tags == ["CUSTOM STABLE"]


def test_style_manager_consumes_only_approved_work_head(temp_workspace):
    package = {"style_rules": ["short paragraphs"], "style_examples": ["example"]}
    StyleManager(temp_workspace).save_style_profile("work_a", {"legacy": True})
    db = DatabaseClient(temp_workspace.sqlite_path)
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO v2_style_candidates
            (candidate_id, work_id, version, package_hash, status, payload_json, idempotency_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "candidate_a",
                "work_a",
                "style-v1",
                package_hash(package),
                "APPROVED",
                json.dumps({"package": package}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "key-a",
            ),
        )
        cur.execute(
            "INSERT INTO v2_style_heads (work_id, active_version, updated_at) VALUES (?, ?, datetime('now'))",
            ("work_a", "style-v1"),
        )

    profile = StyleManager(temp_workspace).get_style_profile("work_a")
    assert profile["source"] == "v2_style_head"
    assert profile["style_rules"] == ["short paragraphs"]
    assert StyleManager(temp_workspace).get_style_profile("work_b")["status"] == "UNAVAILABLE"
