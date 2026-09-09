"""工作包 C：状态账本、回档、所有权与 claim 生命周期回归。"""

from pathlib import Path

import pytest

from fxi.claims.lifecycle import LifecycleManager
from fxi.claims.models import RetconDeclaration
from fxi.claims.retcon import RetconManager
from fxi.core.config import FxiConfig
from fxi.core.exceptions import NotFoundError, OwnershipConflictError, ValidationError
from fxi.core.types import LifecycleAction, MetricStatus
from fxi.domain.ownership import OwnershipTracker
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.state_ledger.definitions import MetricDefinition
from fxi.storage.sqlite_client import DatabaseClient, ensure_entity, ensure_work
from fxi.timeline.reversion import TimeReversionManager


@pytest.fixture
def local_config(tmp_path: Path):
    root = tmp_path
    sqlite_path = root / "state-c-regression.sqlite"
    cfg = FxiConfig(
        workspace_root=root,
        data_dir=root,
        projects_dir=root,
        skills_dir=root,
        sources_dir=root,
        materials_dir=root,
        sqlite_path=sqlite_path,
        cache_db_path=root / "state-c-cache.sqlite",
        jieba_custom_dict_path=root / "state-c-lexicon.txt",
    )
    cfg.ensure_directories()
    DatabaseClient(cfg.sqlite_path).init_db()
    yield cfg


def _setup_work(config: FxiConfig, work_id: str, *entities: tuple[str, str]):
    db = DatabaseClient(config.sqlite_path)
    with db.transaction() as cur:
        ensure_work(cur, work_id)
        for entity_id, category in entities:
            ensure_entity(cur, work_id, entity_id, category=category)


def test_ledger_uses_narrative_order_status_rule_and_snapshots(local_config: FxiConfig):
    _setup_work(local_config, "work_state", ("hero", "character"))
    calc = LedgerCalculator(local_config)
    calc.register_metric(
        MetricDefinition(
            work_id="work_state",
            metric_id="gold",
            metric_name="金币",
            allows_negative=False,
            rule_version="v2",
        )
    )

    # 先写叙事顺序 20，再写顺序 10，event_id 不能取代 narrative_order。
    calc.record_event("work_state", "hero", "gold", 50, "sc_20", 20, "late", rule_version="v2")
    calc.record_event(
        "work_state",
        "hero",
        "gold",
        100,
        "sc_10",
        10,
        "baseline",
        is_anchor=True,
        new_value=100,
        rule_version="v2",
    )
    calc.record_event("work_state", "hero", "gold", 25, "sc_05", 5, "unmeasured", rule_version="v2")
    calc.record_event("work_state", "hero", "gold", 900, "sc_other_rule", 15, "other rule", rule_version="v1")

    at_20 = calc.calculate_balance("work_state", "hero", "gold", 20, rule_version="v2")
    before_anchor = calc.calculate_balance("work_state", "hero", "gold", 5, rule_version="v2")
    assert at_20.computed_value == 150
    assert before_anchor.status == MetricStatus.UNMEASURED

    with calc.db_client.get_connection() as conn:
        snapshot = conn.execute(
            """
            SELECT computed_value, status, based_on_event_id
            FROM state_snapshots
            WHERE work_id = 'work_state' AND entity_id = 'hero'
              AND metric_id = 'gold' AND narrative_order = 20
            """
        ).fetchone()
    assert snapshot["computed_value"] == 150
    assert snapshot["status"] == MetricStatus.EXPLICIT.value
    assert snapshot["based_on_event_id"] is not None


def test_ledger_respects_not_applicable_and_negative_policy(local_config: FxiConfig):
    _setup_work(local_config, "work_state", ("hero", "character"))
    calc = LedgerCalculator(local_config)
    calc.register_metric(
        MetricDefinition(
            work_id="work_state",
            metric_id="stamina",
            metric_name="体力",
            status_type=MetricStatus.NOT_APPLICABLE,
        )
    )
    not_applicable = calc.calculate_balance("work_state", "hero", "stamina", 10)
    assert not_applicable.status == MetricStatus.NOT_APPLICABLE
    assert not_applicable.computed_value is None

    calc.register_metric(
        MetricDefinition(
            work_id="work_state",
            metric_id="debt",
            metric_name="债务",
            allows_negative=False,
        )
    )
    calc.record_event(
        "work_state", "hero", "debt", 10, "sc_debt_1", 1, "anchor", is_anchor=True, new_value=10
    )
    with pytest.raises(ValidationError):
        calc.record_event("work_state", "hero", "debt", -11, "sc_debt_2", 2, "overdraw")


def test_reversion_projects_successor_and_is_idempotent(local_config: FxiConfig):
    _setup_work(local_config, "work_loop", ("hero", "character"))
    manager = TimeReversionManager(local_config)
    manager.create_checkpoint(
        "cp_1",
        "work_loop",
        "main",
        "ch_01",
        1,
        "day 1",
        {"gold": 100},
        ["hero"],
    )
    first = manager.rollback_to_checkpoint("cp_1", 20, actor_id="tester", idempotency_key="rollback-1")
    replay = manager.rollback_to_checkpoint("cp_1", 20, actor_id="tester", idempotency_key="rollback-1")
    assert replay.new_loop_causal_event_id == first.new_loop_causal_event_id

    with manager.db_client.get_connection() as conn:
        event = conn.execute(
            "SELECT summary FROM causal_events WHERE event_id = ?",
            (first.new_loop_causal_event_id,),
        ).fetchone()
        link = conn.execute(
            """
            SELECT 1 FROM causal_links
            WHERE work_id = 'work_loop' AND effect_event_id = ?
            """,
            (first.new_loop_causal_event_id,),
        ).fetchone()
    assert '"successor_of": "reversion_checkpoint_cp_1"' in event["summary"]
    assert link is not None

    with pytest.raises(ValidationError):
        manager.rollback_to_checkpoint("cp_1", 21, actor_id="tester", idempotency_key="rollback-1")


def test_ownership_as_of_scope_and_input_validation(local_config: FxiConfig):
    _setup_work(
        local_config,
        "work_items",
        ("owner_a", "character"),
        ("owner_b", "character"),
        ("sword", "item"),
    )
    tracker = OwnershipTracker(local_config)
    tracker.transfer_ownership("work_items", "sword", None, "owner_a", "looted", "sc_10", 10, "found")
    tracker.transfer_ownership("work_items", "sword", "owner_a", "owner_b", "gifted", "sc_30", 30, "gift")

    assert tracker.get_current_owner("work_items", "sword", as_of=20) == "owner_a"
    assert tracker.get_current_owner("work_items", "sword", as_of=30) == "owner_b"
    with pytest.raises(OwnershipConflictError):
        tracker.transfer_ownership("work_items", "sword", "owner_a", None, "destroyed", "sc_40", 40, "wrong")
    with pytest.raises(NotFoundError):
        tracker.transfer_ownership("work_items", "unknown", "owner_a", "owner_a", "gifted", "sc_x", 1, "unknown")
    with pytest.raises(ValidationError):
        tracker.transfer_ownership(
            "work_items", "sword", "owner_b", "owner_a", "gifted", "sc_bad", 40, "negative", quantity=-1
        )


def test_retcon_effective_order_and_lifecycle_allowlist(local_config: FxiConfig):
    _setup_work(local_config, "work_a", ("hero", "character"))
    _setup_work(local_config, "work_b", ("hero", "character"))
    manager = RetconManager(local_config)
    with manager.db_client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO claim_families
            (family_key, claim_family_id, work_id, owner_id, scope, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("family_a", "family_a", "work_a", "author", "all", "now"),
        )
        cur.execute(
            """
            INSERT INTO claim_versions
            (family_key, claim_id, claim_family_id, version, work_id, status, statement, semantic_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("family_a", "claim_old", "family_a", 1, "work_a", "accepted", "old", "h1", "now"),
        )
    declaration = RetconDeclaration(
        retcon_id="ret_1",
        work_id="work_a",
        superseded_claim_id="claim_old",
        new_claim_id="claim_new",
        effective_narrative_order=50,
        author_note="reframe",
    )
    manager.declare_retcon(declaration)
    assert manager.is_claim_superseded("work_a", "claim_old", 49) is False
    assert manager.is_claim_superseded("work_a", "claim_old", 50) is True
    with manager.db_client.transaction() as cur:
        cur.execute(
            """
            INSERT INTO claim_families
            (family_key, claim_family_id, work_id, owner_id, scope, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("family_b", "family_b", "work_b", "author", "all", "now"),
        )
        cur.execute(
            """
            INSERT INTO claim_versions
            (family_key, claim_id, claim_family_id, version, work_id, status, statement, semantic_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("family_b", "claim_cross", "family_b", 1, "work_b", "accepted", "other", "h2", "now"),
        )
    with pytest.raises(ValidationError):
        manager.declare_retcon(
            RetconDeclaration(
                retcon_id="ret_cross",
                work_id="work_a",
                superseded_claim_id="claim_cross",
                new_claim_id="claim_new",
                effective_narrative_order=1,
                author_note="cross-work",
            )
        )

    lifecycle = LifecycleManager(local_config)
    assert lifecycle.apply_action("claim_versions", "claim_id", "missing", LifecycleAction.EXCLUDE) is False
    assert lifecycle.apply_action("claim_versions", "claim_id", "missing", LifecycleAction.PURGE) is False
    with pytest.raises(ValidationError):
        lifecycle.apply_action(
            "claim_versions; DROP TABLE claim_versions",
            "claim_id",
            "claim_old",
            LifecycleAction.EXCLUDE,
        )
    assert lifecycle.apply_action("claim_versions", "claim_id", "claim_old", LifecycleAction.EXCLUDE) is True
    with lifecycle.db_client.get_connection() as conn:
        row = conn.execute("SELECT status FROM claim_versions WHERE claim_id = 'claim_old'").fetchone()
    assert row["status"] == "excluded"
