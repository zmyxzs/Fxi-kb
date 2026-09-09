"""
fxi.api.router_v1 - REST API v1 路由声明
"""

from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fxi.api.auth import require_actor
from fxi.api.contracts import (
    CanonCheckRequest,
    CanonCheckResponse,
    ContextAssembleRequest,
    ContextAssembleResponse,
    OOCCheckRequest,
    OOCCheckResponse,
    ContinuityCheckRequest,
    ContinuityCheckResponse,
    AskQueryRequest,
    AskQueryResponse,
    RippleQueryRequest,
    RippleQueryResponse,
    StateQueryRequest,
    StateQueryResponse,
    ContinuityRequest,
    ContinuityResponse,
    VoicesRequest,
    VoicesResponse,
    RelationshipsRequest,
    RelationshipsResponse,
    StyleRequest,
    StyleResponse,
    ModelRouteRequest,
    ModelHealthResponse,
    ModelChatRequest,
    ModelChatResponse,
)
from fxi.character_knowledge.ooc_checker import OOCChecker
from fxi.core.config import load_config
from fxi.core.exceptions import GatewayError
from fxi.core.types import CausalStatus
from fxi.domain.entities import EntityManager
from fxi.domain.relations import RelationManager
from fxi.index_retrieval.context_pruner import ContextPruner
from fxi.index_retrieval.query_engine import QueryEngine
from fxi.materials_skills.style_manager import StyleManager
from fxi.model_gateway.gateway import ModelGateway
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.timeline.continuity import ContinuityManager
from fxi.timeline.fact_checker import ContinuityFactChecker
from fxi.timeline.ripple_analyzer import RippleAnalyzer

router = APIRouter(prefix="/v1", tags=["v1"])
_MODEL_GATEWAY_LOCK = Lock()


def _get_model_gateway(request: Request):
    """Return one process-local gateway so key rotation state is shared."""
    state = request.app.state
    gateway = getattr(state, "model_gateway", None)
    if gateway is not None:
        return gateway
    with _MODEL_GATEWAY_LOCK:
        gateway = getattr(state, "model_gateway", None)
        if gateway is None:
            config = getattr(state, "config", None) or load_config()
            gateway = ModelGateway(config)
            state.model_gateway = gateway
    return gateway


def _require_v1_actor(request: Request, work_id: str, role: str = "reader") -> None:
    """Apply server-side identity and explicit work scope to legacy routes."""
    registry = getattr(request.app.state, "work_registry", None)
    require_actor(request, role, work_id, registry=registry)


@router.post("/models/health", response_model=ModelHealthResponse)
def model_gateway_health(req: ModelRouteRequest, request: Request):
    """Check the selected route and credentials without a paid model call."""
    require_actor(request, "writer")
    try:
        gateway = _get_model_gateway(request)
        healthy = gateway.is_healthy(
            task_type=req.task_type,
            provider_override=req.provider_override,
            model_override=req.model_override,
        )
    except (GatewayError, RuntimeError, OSError):
        healthy = False
    return ModelHealthResponse(healthy=healthy)


@router.post("/models/chat", response_model=ModelChatResponse)
def model_gateway_chat(req: ModelChatRequest, request: Request):
    """Proxy an authenticated chat request through the shared model gateway."""
    require_actor(request, "writer")
    try:
        gateway = _get_model_gateway(request)
        content = gateway.complete_chat(
            task_type=req.task_type,
            system_prompt=req.system_prompt,
            user_prompt=req.user_prompt,
            provider_override=req.provider_override,
            model_override=req.model_override,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except (GatewayError, RuntimeError, OSError) as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "MODEL_GATEWAY_ERROR",
                "message": "model provider request failed",
            },
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(
            status_code=502,
            detail={
                "code": "MODEL_GATEWAY_INVALID_OUTPUT",
                "message": "model provider returned empty content",
            },
        )
    return ModelChatResponse(content=content.strip())


@router.post("/context/assemble", response_model=ContextAssembleResponse)
def assemble_context(req: ContextAssembleRequest, request: Request):
    """装配场景写作上下文 (带剪枝、视点过滤与防吃书)"""
    _require_v1_actor(request, req.work_id, "reader")
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
        budget=req.budget,
        characters=req.characters,
        props=req.props,
        events=req.events,
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
    _require_v1_actor(request, req.work_id, "reader")
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
    _require_v1_actor(request, req.work_id, "writer")
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
    _require_v1_actor(request, req.work_id, "reader")
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
        computed_value=snap.computed_value if snap.computed_value is not None else 0.0,
        status=snap.status.value,
        narrative_order=snap.narrative_order
    )


@router.post("/checks/ooc", response_model=OOCCheckResponse)
def check_ooc(req: OOCCheckRequest, request: Request):
    """提交正文草稿执行防 OOC 与声线偏离智能扫描"""
    _require_v1_actor(request, req.work_id, "reviewer")
    config = getattr(request.app.state, "config", None) or load_config()
    checker = OOCChecker(config)
    violations = checker.scan_draft(
        draft_text=req.draft_text,
        work_id=req.work_id,
        narrative_order=req.narrative_order,
        speaker_id=req.speaker_id,
        characters=req.characters,
        unrevealed_secrets=req.unrevealed_secrets,
        use_llm=True,
    )
    return OOCCheckResponse(
        has_violations=any(v.severity in ("error", "warning") for v in violations),
        violations=[
            {
                "severity": v.severity,
                "character_id": v.character_id,
                "issue_type": v.issue_type,
                "message": v.message,
                "line_snippet": v.line_snippet,
                "suggestion": v.suggestion,
            }
            for v in violations
        ]
    )


@router.post("/checks/continuity", response_model=ContinuityCheckResponse)
def check_continuity(req: ContinuityCheckRequest, request: Request):
    """提交正文草稿执行章节事实与时序前情门禁扫描"""
    _require_v1_actor(request, req.work_id, "reviewer")
    config = getattr(request.app.state, "config", None) or load_config()
    checker = ContinuityFactChecker(config)
    violations = checker.check_continuity(
        work_id=req.work_id,
        chapter_index=req.chapter_index,
        draft_text=req.draft_text,
        planned_events=req.planned_events,
        use_llm=True,
    )
    return ContinuityCheckResponse(
        is_valid=len([v for v in violations if v.severity == "error"]) == 0,
        violations=[
            {
                "severity": v.severity,
                "issue_type": v.issue_type,
                "message": v.message,
                "line_snippet": v.line_snippet,
                "suggestion": v.suggestion,
            }
            for v in violations
        ]
    )


@router.post("/query/ask", response_model=AskQueryResponse)
def ask_question(req: AskQueryRequest, request: Request):
    """【智能自然语言问答接口】：接收问题，自动拆词、多源检索证据并综合回答"""
    _require_v1_actor(request, req.work_id, "reader")
    config = getattr(request.app.state, "config", None) or load_config()
    engine = QueryEngine(config)
    res = engine.ask(
        work_id=req.work_id,
        question=req.question,
        top_k_scenes=req.top_k_scenes,
        source_id=req.source_id,
        source_version=req.source_version,
        knowledge_version=req.knowledge_version,
        narrative_order=req.narrative_order,
        timeline_id=req.timeline_id,
        divergence_narrative_order=req.divergence_narrative_order,
    )
    return AskQueryResponse(
        work_id=res.work_id,
        question=res.question,
        target_entities=res.decomposition.target_entities,
        keywords=res.decomposition.keywords,
        intent=res.decomposition.intent,
        signals=[signal.model_dump(mode="json") for signal in res.decomposition.signals],
        diagnostics=res.decomposition.diagnostics,
        answer=res.answer,
        evidence=res.evidence
    )


@router.post("/context/continuity", response_model=ContinuityResponse)
def get_continuity(req: ContinuityRequest, request: Request):
    """查询指定章节末尾的物理地点、尾声切片与悬念未决钩子"""
    _require_v1_actor(request, req.work_id, "reader")
    config = getattr(request.app.state, "config", None) or load_config()
    cm = ContinuityManager(config)
    data = cm.get_continuity(req.work_id, req.chapter_index)
    if not data:
        return ContinuityResponse(work_id=req.work_id, chapter_index=req.chapter_index)
    return ContinuityResponse(
        work_id=req.work_id,
        chapter_index=req.chapter_index,
        title=data.get("title", ""),
        tail_snippet=data.get("tail_snippet", ""),
        ending_location=data.get("ending_location", ""),
        active_characters=data.get("active_characters", []),
        ending_situation=data.get("ending_situation", ""),
        unresolved_hooks=data.get("unresolved_hooks", [])
    )


@router.post("/context/voices", response_model=VoicesResponse)
def get_character_voices(req: VoicesRequest, request: Request):
    """查询指定角色的声线定位、口吻特征、典型台词与微动作"""
    _require_v1_actor(request, req.work_id, "reader")
    config = getattr(request.app.state, "config", None) or load_config()
    em = EntityManager(config)
    result = {}
    for c in req.characters:
        vp = em.get_character_voice(req.work_id, c)
        if vp:
            result[c] = vp
    return VoicesResponse(work_id=req.work_id, voices=result)


@router.post("/context/relationships", response_model=RelationshipsResponse)
def get_relationships(req: RelationshipsRequest, request: Request):
    """查询在场人物之间的人际张力、共同秘密与互动态度"""
    _require_v1_actor(request, req.work_id, "reader")
    config = getattr(request.app.state, "config", None) or load_config()
    rm = RelationManager(config)
    rels = rm.get_tensions(req.work_id, req.characters)
    return RelationshipsResponse(work_id=req.work_id, relationships=rels)


@router.post("/context/style", response_model=StyleResponse)
def get_style_profile(req: StyleRequest, request: Request):
    """查询作品文笔风格、具象比喻偏好与负向禁令"""
    _require_v1_actor(request, req.work_id, "reader")
    config = getattr(request.app.state, "config", None) or load_config()
    sm = StyleManager(config)
    sp = sm.get_style_profile(req.work_id, req.scene_type)
    return StyleResponse(work_id=req.work_id, style_profile=sp)
