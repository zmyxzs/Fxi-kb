import pytest

from fxi.core.exceptions import CorruptedDataError
from fxi.materials_skills.anti_patterns import AntiPatternRepository


def test_work_id_does_not_fallback_to_base_project(temp_workspace):
    inherited_dir = temp_workspace.projects_dir / "source" / "style"
    inherited_dir.mkdir(parents=True)
    (inherited_dir / "anti_patterns.yaml").write_text(
        "anti_patterns:\n  - 不能从未授权的原著作品继承规则\n",
        encoding="utf-8",
    )

    constraints = AntiPatternRepository(temp_workspace).get_negative_constraints(
        work_id="source_fanfic"
    )

    assert constraints == []


def test_malformed_work_config_is_reported_instead_of_silently_ignored(temp_workspace):
    config_dir = temp_workspace.projects_dir / "work_a" / "style"
    config_dir.mkdir(parents=True)
    (config_dir / "anti_patterns.yaml").write_text(
        "patterns: [\n",
        encoding="utf-8",
    )

    with pytest.raises(CorruptedDataError) as exc_info:
        AntiPatternRepository(temp_workspace).get_negative_constraints(
            work_id="work_a"
        )

    assert exc_info.value.code == "CORRUPTED_DATA_ERROR"
