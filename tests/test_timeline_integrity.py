"""Timeline integrity, DAG invariants, State receipts, Reversion, and Ripple tests."""

import pytest

from fxi.core.config import FxiConfig
from fxi.core.exceptions import CausalConflictError, NotFoundError, ValidationError
from fxi.core.types import MetricStatus
from fxi.state_ledger.anchor import AnchorManager
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.storage.sqlite_client import DatabaseClient, ensure_work
from fxi.timeline.dag import CausalDAG
from fxi.timeline.reversion import TimeReversionManager
from fxi.timeline.ripple_analyzer import RippleAnalyzer


def _setup_works(config: FxiConfig, *work_ids: str):
    db = DatabaseClient(config.sqlite_path)
    with db.transaction() as cur:
        for w in work_ids:
            ensure_work(cur, w)


def test_dag_rejects_unregistered_work_and_invalid_nodes(temp_workspace: FxiConfig):
    dag = CausalDAG(temp_workspace)
    # 未注册作品必须失败 (NotFoundError)
    with pytest.raises(NotFoundError):
        dag.register_event("ev_1", "unknown_work", "sc_1", 1, "t1", "summary")

    _setup_works(temp_workspace, "work_a", "work_b")
    # 注册合法节点
    dag.register_event("ev_1", "work_a", "sc_1", 1, "t1", "summary", timeline_id="main")

    # 尝试改挂作品或时间线
    with pytest.raises(ValidationError):
        dag.register_event("ev_1", "work_b", "sc_1", 1, "t1", "summary", timeline_id="main")
    with pytest.raises(ValidationError):
        dag.register_event("ev_1", "work_a", "sc_1", 1, "t1", "summary", timeline_id="branch_1")


def test_dag_rejects_cycles_duplicate_edges_and_cross_work_edges(temp_workspace: FxiConfig):
    _setup_works(temp_workspace, "work_a", "work_b")
    dag = CausalDAG(temp_workspace)

    dag.register_event("ev_1", "work_a", "sc_1", 1, "t1", "Event 1", timeline_id="main")
    dag.register_event("ev_2", "work_a", "sc_2", 2, "t2", "Event 2", timeline_id="main")
    dag.register_event("ev_3", "work_a", "sc_3", 3, "t3", "Event 3", timeline_id="main")
    dag.register_event("ev_b1", "work_b", "sc_b1", 1, "t1", "Event B1", timeline_id="main")

    # 自环拒绝
    with pytest.raises(CausalConflictError):
        dag.add_causal_link("work_a", "ev_1", "ev_1")

    # 跨作品拒绝
    with pytest.raises(ValidationError):
        dag.add_causal_link("work_a", "ev_1", "ev_b1")

    # 正常加边 ev_1 -> ev_2, ev_2 -> ev_3
    dag.add_causal_link("work_a", "ev_1", "ev_2")
    dag.add_causal_link("work_a", "ev_2", "ev_3")

    # 重复边拒绝
    with pytest.raises(ValidationError):
        dag.add_causal_link("work_a", "ev_1", "ev_2")

    # 反向形成环 ev_3 -> ev_1 拒绝 (CausalConflictError)
    with pytest.raises(CausalConflictError):
        dag.add_causal_link("work_a", "ev_3", "ev_1")


def test_state_anchor_and_receipt_idempotency(temp_workspace: FxiConfig):
    _setup_works(temp_workspace, "work_a")
    anchor_mgr = AnchorManager(temp_workspace)
    calc = LedgerCalculator(temp_workspace)

    # 创建带 commit_id 和 idempotency_key 的基准锚点
    event_id_1 = anchor_mgr.create_baseline_anchor(
        work_id="work_a",
        entity_id="char_hero",
        metric_id="hp",
        baseline_value=100.0,
        narrative_order=10,
        scene_uuid="sc_10",
        reason="基准血量",
        commit_id="commit_001",
        idempotency_key="idemp_anchor_1",
        knowledge_version="kv_1",
    )
    assert event_id_1 > 0

    # 同一 idempotency_key 重放 -> 幂等返回同一 event_id
    event_id_replay = anchor_mgr.create_baseline_anchor(
        work_id="work_a",
        entity_id="char_hero",
        metric_id="hp",
        baseline_value=100.0,
        narrative_order=10,
        scene_uuid="sc_10",
        reason="基准血量",
        commit_id="commit_001",
        idempotency_key="idemp_anchor_1",
        knowledge_version="kv_1",
    )
    assert event_id_replay == event_id_1

    # 相同 idempotency_key 提供不同载荷 -> 必须拒绝 (ValidationError)
    with pytest.raises(ValidationError):
        anchor_mgr.create_baseline_anchor(
            work_id="work_a",
            entity_id="char_hero",
            metric_id="hp",
            baseline_value=200.0,  # 不同数值
            narrative_order=10,
            scene_uuid="sc_10",
            reason="篡改数值",
            commit_id="commit_001",
            idempotency_key="idemp_anchor_1",
            knowledge_version="kv_1",
        )

    # 验证结余结算
    balance = calc.calculate_balance("work_a", "char_hero", "hp", narrative_order=10)
    assert balance.status == MetricStatus.EXPLICIT
    assert balance.computed_value == 100.0


def test_reversion_successor_monotonicity_and_actor_tracking(temp_workspace: FxiConfig):
    _setup_works(temp_workspace, "work_reversion")
    trm = TimeReversionManager(temp_workspace)

    trm.create_checkpoint(
        checkpoint_id="cp_loop_1",
        work_id="work_reversion",
        timeline_id="main",
        chapter_id="ch_01",
        narrative_order=1,
        physical_timestamp="2026-01-01 08:00",
        world_state={"gold": 1000},
        retained_entities=["char_mc"],
    )

    report = trm.rollback_to_checkpoint(
        checkpoint_id="cp_loop_1",
        current_narrative_order=20,
        trigger_reason="party_wipe",
        actor_id="admin_user",
        idempotency_key="rev_idemp_1",
    )

    assert report.checkpoint_id == "cp_loop_1"
    assert report.actor_id == "admin_user"
    assert report.idempotency_key == "rev_idemp_1"
    assert "char_mc" in report.retained_entities
    assert report.restored_world_state["gold"] == 1000

    # 验证因果图中生成了单调递增的新循环节点
    dag = CausalDAG(temp_workspace)
    event = dag.get_event("work_reversion", report.new_loop_causal_event_id)
    assert event is not None
    assert event["narrative_order"] == 20
    assert event["is_canon"] == 0


def test_ripple_divergence_mapping_conflict_guard(temp_workspace: FxiConfig):
    _setup_works(temp_workspace, "canon_book", "fanfic_book")
    dag = CausalDAG(temp_workspace)

    # 注册原著事件
    dag.register_event("canon_ev_1", "canon_book", "sc_c1", 1, "t1", "原著事件1", is_canon=True)
    dag.register_event("canon_ev_2", "canon_book", "sc_c2", 2, "t2", "原著事件2", is_canon=True)
    dag.add_causal_link("canon_book", "canon_ev_1", "canon_ev_2")

    ripple = RippleAnalyzer(temp_workspace)

    # 正常标记同人分歧点
    tree = ripple.mark_divergence(
        work_id="fanfic_book",
        canon_work_id="canon_book",
        canon_event_id="canon_ev_1",
        fanfic_event_id="fanfic_ev_1",
        fanfic_summary="同人改变历史1",
        narrative_order=1,
    )
    assert tree.mapped_fanfic_event_id == "fanfic_ev_1"

    # 再次尝试将同一原著事件映射到不同的同人事件 -> 必须显式拒绝 (ValidationError)
    with pytest.raises(ValidationError):
        ripple.mark_divergence(
            work_id="fanfic_book",
            canon_work_id="canon_book",
            canon_event_id="canon_ev_1",
            fanfic_event_id="fanfic_ev_CONFLICT",
            fanfic_summary="冲突的分歧事件",
            narrative_order=1,
        )
