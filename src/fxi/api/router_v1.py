"""
fxi.api.router_v1 - REST API v1 路由声明
"""

from fastapi import APIRouter, HTTPException, Request
from fxi.api.contracts import (
    CanonCheckRequest,
    CanonCheckResponse,
    ContextAssembleRequest,
    ContextAssembleResponse,
    OOCCheckRequest,
    OOCCheckResponse,
    AskQueryRequest,
    AskQueryResponse,
    RippleQueryRequest,
    RippleQueryResponse,
    StateQueryRequest,
    StateQueryResponse,
)
from fxi.character_knowledge.ooc_checker import OOCChecker
from fxi.core.config import load_config
from fxi.core.types import CausalStatus
from fxi.index_retrieval.context_pruner import ContextPruner
from fxi.index_retrieval.query_engine import QueryEngine

from fxi.state_ledger.calculator import LedgerCalculator
from fxi.timeline.ripple_analyzer import RippleAnalyzer

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/context/assemble", response_model=ContextAssembleResponse)
def assemble_context(req: ContextAssembleRequest, request: Request):
    """装配场景写作上下文 (带剪枝、视点过滤与防吃书)"""
    config = getattr(request.app.state, "config", None) or load_config()
    pruner = ContextPruner(config)
    text = pruner.assemble_and_prune(
        work_id=req.work_id,
        scene_type=req.scene_type,
        pov_character_id=req.pov_character_id,
        current_narrative_order=req.current_narrative_order,
        query=req.query,
        territory_id=req.territory_id,
        skill_slug=req.skill_slug,
        budget=req.budget
    )
    return ContextAssembleResponse(
        work_id=req.work_id,
        pov_character_id=req.pov_character_id,
        assembled_context=text,
        approx_tokens=len(text) // 2
    )


@router.post("/timeline/check-canon-compatibility", response_model=CanonCheckResponse)
def check_canon_compatibility(req: CanonCheckRequest, request: Request):
    """【防吃书预检】：检查同人即将写的原著剧情是否已被蝴蝶效应阻断"""
    config = getattr(request.app.state, "config", None) or load_config()
    analyzer = RippleAnalyzer(config)
    status, reason, suggestions = analyzer.check_canon_compatibility(
        work_id=req.work_id,
        canon_work_id=req.canon_work_id,
        intended_canon_event_id=req.intended_canon_event_id
    )
    return CanonCheckResponse(
        status=status,
        is_safe=(status == CausalStatus.UNTOUCHED),
        diagnostic_reason=reason,
        suggestions=suggestions
    )


@router.post("/timeline/ripple-impact", response_model=RippleQueryResponse)
def query_ripple_impact(req: RippleQueryRequest, request: Request):
    """【蝴蝶效应查询】：分析同人分歧点向下游扩散的波及影响树"""
    config = getattr(request.app.state, "config", None) or load_config()
    analyzer = RippleAnalyzer(config)
    tree = analyzer.mark_divergence(
        work_id=req.work_id,
        canon_work_id=req.canon_work_id,
        canon_event_id=req.canon_event_id,
        fanfic_event_id=req.fanfic_event_id,
        fanfic_summary=req.fanfic_summary,
        narrative_order=req.narrative_order
    )
    return RippleQueryResponse(
        work_id=tree.work_id,
        divergence_canon_event_id=tree.divergence_canon_event_id,
        invalidated_events=tree.invalidated_canon_events,
        mutated_events=tree.mutated_canon_events,
        suggested_alternatives=tree.suggested_alternatives
    )


@router.post("/state/query", response_model=StateQueryResponse)
def query_state(req: StateQueryRequest, request: Request):
    """查询角色属性、金币或战力动态结余"""
    config = getattr(request.app.state, "config", None) or load_config()
    calc = LedgerCalculator(config)
    snap = calc.calculate_balance(
        work_id=req.work_id,
        entity_id=req.entity_id,
        metric_id=req.metric_id,
        narrative_order=req.narrative_order
    )
    return StateQueryResponse(
        work_id=snap.work_id,
        entity_id=snap.entity_id,
        metric_id=snap.metric_id,
        computed_value=snap.computed_value,
        status=snap.status.value,
        narrative_order=snap.narrative_order
    )


@router.post("/checks/ooc", response_model=OOCCheckResponse)
def check_ooc(req: OOCCheckRequest, request: Request):
    """提交正文草稿执行防 OOC 与秘密早泄扫描"""
    config = getattr(request.app.state, "config", None) or load_config()
    checker = OOCChecker(config)
    violations = checker.scan_draft(
        draft_text=req.draft_text,
        work_id=req.work_id,
        narrative_order=req.narrative_order,
        speaker_id=req.speaker_id,
        unrevealed_secrets=req.unrevealed_secrets
    )
    return OOCCheckResponse(
        has_violations=len(violations) > 0,
        violations=[
            {
                "severity": v.severity,
                "character_id": v.character_id,
                "issue_type": v.issue_type,
                "message": v.message,
                "line_snippet": v.line_snippet
            }
            for v in violations
        ]
    )


@router.post("/query/ask", response_model=AskQueryResponse)
def ask_question(req: AskQueryRequest, request: Request):
    """【智能自然语言问答接口】：接收问题，自动拆词、多源检索证据并综合回答"""
    config = getattr(request.app.state, "config", None) or load_config()
    engine = QueryEngine(config)
    res = engine.ask(work_id=req.work_id, question=req.question, top_k_scenes=req.top_k_scenes)
    return AskQueryResponse(
        work_id=res.work_id,
        question=res.question,
        target_entities=res.decomposition.target_entities,
        keywords=res.decomposition.keywords,
        answer=res.answer,
        evidence=res.evidence
    )

