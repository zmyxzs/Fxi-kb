"""CanonicalRegistry 的通用 ID、别名、作品作用域和类型隔离契约。"""

from pathlib import Path

import pytest
import yaml

from fxi.domain.canonical import CanonicalRegistry


def _write_canonical(config, work_id: str, payload: dict) -> Path:
    directory = config.projects_dir / work_id / "entities"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "canonical.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _write_entity(path: Path, entity_id: str, category: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nentity_id: {entity_id}\ncategory: {category}\nname: {name}\n---\n",
        encoding="utf-8",
    )


def test_unknown_ids_are_stable_and_namespaced_by_work_and_type(temp_workspace):
    registry = CanonicalRegistry(temp_workspace)

    character_a = registry.get_canonical_id("work_a", "未配置实体", "character")
    assert character_a == registry.get_canonical_id("work_a", " 未配置实体 ", "characters")
    assert character_a.startswith("character_")
    assert "lin_qiye" not in character_a

    character_b = registry.get_canonical_id("work_b", "未配置实体", "character")
    item_a = registry.get_canonical_id("work_a", "未配置实体", "item")
    assert character_a != character_b
    assert character_a != item_a


def test_configured_aliases_are_scoped_by_work_and_type(temp_workspace):
    _write_canonical(
        temp_workspace,
        "work_a",
        {
            "version": 1,
            "scope": "work_a",
            "canonical_ids": {
                "character": {"共享名称": "char_a"},
                "item": {"共享名称": "item_a"},
            },
            "aliases": {
                "character": {"主角": "char_a"},
                "item": {"遗物": "item_a"},
            },
            "entities": [
                {
                    "id": "location_hub",
                    "type": "location",
                    "name": "总部",
                    "aliases": ["旧总部"],
                }
            ],
        },
    )
    _write_canonical(
        temp_workspace,
        "work_b",
        {
            "scope": "work_b",
            "canonical_ids": {"character": {"共享名称": "char_b"}},
        },
    )
    registry = CanonicalRegistry(temp_workspace)

    assert registry.get_canonical_id("work_a", "共享名称", "character") == "char_a"
    assert registry.get_canonical_id("work_a", "主角", "character") == "char_a"
    assert registry.get_canonical_id("work_a", "共享名称", "item") == "item_a"
    assert registry.get_canonical_id("work_a", "遗物", "item") == "item_a"
    assert registry.get_canonical_id("work_a", "旧总部", "location") == "location_hub"
    assert registry.get_canonical_id("work_b", "共享名称", "character") == "char_b"

    # 映射是规范注册的权威来源，调用者提供的 fallback 不能覆盖它。
    assert (
        registry.get_canonical_id(
            "work_a", "共享名称", "character", default_id="character_wrong"
        )
        == "char_a"
    )
    assert registry.get_canonical_id("work_b", "仅本作品", default_id="char_explicit") == "char_explicit"


def test_scope_and_type_conflicts_are_visible(temp_workspace):
    _write_canonical(
        temp_workspace,
        "work_a",
        {
            "scope": "work_b",
            "canonical_ids": {"character": {"名称": "char_a"}},
        },
    )
    registry = CanonicalRegistry(temp_workspace)
    with pytest.raises(RuntimeError, match="scope"):
        registry.get_canonical_id("work_a", "名称", "character")

    _write_canonical(
        temp_workspace,
        "work_a",
        {
            "scope": "work_a",
            "canonical_ids": {"character": {"名称": "item_wrong"}},
        },
    )
    with pytest.raises(RuntimeError, match="类型"):
        registry.get_canonical_id("work_a", "名称", "character")

    with pytest.raises(ValueError, match="类型"):
        registry.get_canonical_id("work_b", "新名称", "character", default_id="item_wrong")

    _write_canonical(
        temp_workspace,
        "work_b",
        {
            "scope": "work_b",
            "canonical_ids": {
                "character": {"角色": "shared_id"},
                "item": {"物品": "shared_id"},
            },
        },
    )
    with pytest.raises(RuntimeError, match="跨类型复用"):
        registry.get_canonical_id("work_b", "角色", "character")


def test_deduplication_does_not_merge_same_name_across_types(temp_workspace):
    _write_canonical(
        temp_workspace,
        "work_a",
        {
            "scope": "work_a",
            "canonical_ids": {
                "character": {"同名": "char_primary"},
                "item": {"同名": "item_primary"},
            },
        },
    )
    entities_root = temp_workspace.projects_dir / "work_a" / "entities"
    _write_entity(entities_root / "characters" / "char_primary.md", "char_primary", "character", "同名")
    _write_entity(entities_root / "characters" / "char_duplicate.md", "char_duplicate", "character", "同名")
    _write_entity(entities_root / "items" / "item_primary.md", "item_primary", "item", "同名")
    _write_entity(entities_root / "items" / "item_duplicate.md", "item_duplicate", "item", "同名")

    report = CanonicalRegistry(temp_workspace).run_deduplication("work_a")

    assert report == {
        "char_primary": ["char_duplicate"],
        "item_primary": ["item_duplicate"],
    }
    assert (entities_root / "characters" / "char_primary.md").is_file()
    assert (entities_root / "items" / "item_primary.md").is_file()
    assert not (entities_root / "characters" / "char_duplicate.md").exists()
    assert not (entities_root / "items" / "item_duplicate.md").exists()
