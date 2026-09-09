"""状态计算工作包：commit、规则版本、作品隔离与重放回归。"""

from pathlib import Path

import pytest

from fxi.core.config import FxiConfig
from fxi.core.exceptions import ValidationError
from fxi.core.types import MetricStatus
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.state_ledger.definitions import MetricDefinition
from fxi.storage.sqlite_client import DatabaseClient, ensure_entity, ensure_work


@pytest.fixture
def local_config(tmp_path: Path):
    root = tmp_path
    sqlite_path = root / "state-calculator.sqlite"
    cache_path = root / "state-calculator-cache.sqlite"
    cfg = FxiConfig(
        workspace_root=root,
        data_dir=root,
        projects_dir=root,
        skills_dir=root,
        sources_dir=root,
        materials_dir=root,
        sqlite_path=sqlite_path,
        cache_db_path=cache_path,
        jieba_custom_dict_path=root / "state-calculator-lexicon.txt",
    )
    cfg.ensure_directories()
    DatabaseClient(cfg.sqlite_path).init_db()
    yield cfg


def _setup_work(config: FxiConfig, *work_ids: str):
    db = DatabaseClient(config.sqlite_path)
    with db.transaction() as cur:
        for work_id in work_ids:
            ensure_work(cur, work_id)
            ensure_entity(cur, work_id, "hero", category="character")


def _register_metric(calc: LedgerCalculator, work_id: str, rule_version: str = "v1"):
    calc.register_metric(
        MetricDefinition(
            work_id=work_id,
            metric_id="gold",
            metric_name="金币",
            allows_negative=True,
            rule_version=rule_version,
        )
    )


def test_commit_id_deduplicates_same_event_and_rejects_changed_payload(local_config: FxiConfig):
    _setup_work(local_config, "work_a")
    calc = LedgerCalculator(local_config)
    _register_metric(calc, "work_a")

    first = calc.record_event(
        "work_a", "hero", "gold", 10, "scene-1", 1, "gain", commit_id="commit-1"
    )
    replay = calc.record_event(
        "work_a", "hero", "gold", 10, "scene-1", 1, "gain", commit_id="commit-1"
    )

    assert replay == first
    with calc.db_client.transaction() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS count FROM state_events WHERE work_id = ? AND metric_id = ?",
            ("work_a", "gold"),
        ).fetchone()
    assert row["count"] == 1

    with pytest.raises(ValidationError):
        calc.record_event(
            "work_a", "hero", "gold", 11, "scene-1", 1, "tampered", commit_id="commit-1"
        )


def test_rule_version_uses_persisted_definition_after_another_calculator_updates_it(
    local_config: FxiConfig,
):
    _setup_work(local_config, "work_a")
    first_calc = LedgerCalculator(local_config)
    _register_metric(first_calc, "work_a", "v1")
    first_calc.record_event("work_a", "hero", "gold", 10, "scene-1", 1, "v1")

    second_calc = LedgerCalculator(local_config)
    _register_metric(second_calc, "work_a", "v2")
    second_event = first_calc.record_event(
        "work_a", "hero", "gold", 5, "scene-2", 2, "v2-after-refresh"
    )

    with first_calc.db_client.transaction() as cur:
        metric = cur.execute(
            "SELECT rule_version FROM state_metrics WHERE work_id = ? AND metric_id = ?",
            ("work_a", "gold"),
        ).fetchone()
        event = cur.execute(
            "SELECT rule_version FROM state_events WHERE event_id = ?", (second_event,)
        ).fetchone()
    assert metric["rule_version"] == "v2"
    assert event["rule_version"] == "v2"

    balance = second_calc.calculate_balance("work_a", "hero", "gold", 2)
    assert balance.status == MetricStatus.EXPLICIT
    assert balance.computed_value == 5


def test_commit_replay_without_explicit_rule_version_survives_metric_update(
    local_config: FxiConfig,
):
    _setup_work(local_config, "work_a")
    calc = LedgerCalculator(local_config)
    _register_metric(calc, "work_a", "v1")

    first = calc.record_event(
        "work_a", "hero", "gold", 10, "scene-1", 1, "stable", commit_id="commit-stable"
    )
    _register_metric(calc, "work_a", "v2")
    replay = calc.record_event(
        "work_a", "hero", "gold", 10, "scene-1", 1, "stable", commit_id="commit-stable"
    )

    assert replay == first
    with calc.db_client.transaction() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS count FROM state_events WHERE work_id = ? AND metric_id = ?",
            ("work_a", "gold"),
        ).fetchone()
    assert row["count"] == 1


def test_commit_and_idempotency_keys_are_isolated_by_work(local_config: FxiConfig):
    _setup_work(local_config, "work_a", "work_b")
    calc = LedgerCalculator(local_config)
    _register_metric(calc, "work_a")
    _register_metric(calc, "work_b")

    event_a = calc.record_event(
        "work_a",
        "hero",
        "gold",
        10,
        "scene-1",
        1,
        "work-a",
        commit_id="same-commit",
        idempotency_key="same-idempotency",
    )
    event_b = calc.record_event(
        "work_b",
        "hero",
        "gold",
        20,
        "scene-1",
        1,
        "work-b",
        commit_id="same-commit",
        idempotency_key="same-idempotency",
    )

    assert event_b != event_a
    assert calc.calculate_balance("work_a", "hero", "gold", 1).computed_value == 10
    assert calc.calculate_balance("work_b", "hero", "gold", 1).computed_value == 20
