"""
fxi.index_retrieval.retrieval_plan - 只读查询能力与范围契约。

本模块只描述查询计划，不读取数据库、不写入业务状态，也不拥有任何作品事实。
具体实体、事件、主张和上下文过滤仍由各领域模块负责。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# 这些是检索层允许编排的能力名，不是具体作品的事实或题材词。
CAPABILITY_NAMES = frozenset(
    {
        "entity_profile",
        "relationship",
        "ownership",
        "skill",
        "timeline",
        "causal_reason",
        "state",
        "general_search",
    }
)

# 兼容旧 prompt、旧 work.yaml 和旧 QueryDecomposition.intent 值。
CAPABILITY_ALIASES = {
    "general": "general_search",
    "origin": "entity_profile",
    "ability": "skill",
    "item_ownership": "ownership",
    "timeline_event": "timeline",
    "causal_fate": "causal_reason",
}

# 通用语言结构；作品相关词必须从指定 work.yaml 读取。
GENERIC_INTENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "causal_reason": ("为什么", "为何", "原因", "缘故", "缘由", "怎么会", "因何"),
    "timeline": ("时间线", "经过", "过程", "经历", "后来", "顺序"),
    "relationship": ("关系", "对立", "合作", "情感", "看重"),
}

# 只过滤结构词。动作词和剧情谓词保留，避免降低事件召回率。
QUERY_STOPWORDS = frozenset(
    {
        "怎么", "什么", "为什么", "怎样", "哪个", "哪里", "是谁", "如何", "这个", "那个",
        "为何", "何以", "原因", "缘故", "缘由", "因何", "因果",
        "在", "到", "于", "了", "的", "有", "是", "被", "和", "与",
    }
)


class ScopeContext(BaseModel):
    """查询允许读取的作品、来源、时间线和认知范围。"""

    model_config = ConfigDict(frozen=True)

    work_id: str = Field(min_length=1)
    project_id: Optional[str] = Field(default=None, min_length=1)
    world_id: Optional[str] = Field(default=None, min_length=1)
    timeline_id: Optional[str] = Field(default=None, min_length=1)
    source_id: Optional[str] = Field(default=None, min_length=1)
    source_version: Optional[str] = Field(default=None, min_length=1)
    knowledge_version: Optional[str] = Field(default=None, min_length=1)
    narrative_order: Optional[int] = Field(default=None, ge=0)
    divergence_narrative_order: Optional[int] = Field(default=None, ge=0)
    pov_character_id: Optional[str] = Field(default=None, min_length=1)
    scope_status: Literal["RESOLVED", "REQUIRED", "INCOMPLETE"] = "RESOLVED"

    @model_validator(mode="after")
    def validate_source_pair(self) -> "ScopeContext":
        if (self.source_id is None) != (self.source_version is None):
            raise ValueError("source_id 与 source_version 必须同时提供")
        return self


class IntentSignal(BaseModel):
    """查询拆解产生的可解释能力信号，不是已证实的业务事实。"""

    model_config = ConfigDict(frozen=True)

    capability: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    matched_by: Literal["rule", "config", "model", "schema"]
    matched_terms: tuple[str, ...] = Field(default_factory=tuple)
    priority: int = 0


class EvidenceBlock(BaseModel):
    """单一能力返回的证据块及其可信范围。"""

    kind: str = Field(min_length=1)
    items: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["AVAILABLE", "EMPTY", "INCOMPLETE", "LEGACY_HINT", "UNSUPPORTED"]
    scope: dict[str, Any] = Field(default_factory=dict)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class RetrievalPlan(BaseModel):
    """只读检索计划；构建计划不应触碰数据库或写入业务状态。"""

    model_config = ConfigDict(frozen=True)

    scope: ScopeContext
    signals: tuple[IntentSignal, ...] = Field(default_factory=tuple)
    capabilities: tuple[str, ...] = Field(default_factory=tuple)
    required_evidence: tuple[str, ...] = Field(default_factory=tuple)
    item_limits: dict[str, int] = Field(default_factory=dict)
    diagnostics: tuple[dict[str, Any], ...] = Field(default_factory=tuple)


class RetrievalPlanBuilder:
    """Build a read-only capability plan without touching domain state."""

    _DEFAULT_LIMITS = {
        "causal_seed_events": 5,
        "causal_hops": 3,
        "causal_events": 20,
        "timeline_events": 10,
    }

    @classmethod
    def build(
        cls,
        decomposition: Any,
        scope: ScopeContext,
        *,
        top_k_scenes: int = 8,
    ) -> RetrievalPlan:
        diagnostics: list[dict[str, Any]] = []
        raw_signals = getattr(decomposition, "signals", []) or []
        signals: list[IntentSignal] = []
        for raw_signal in raw_signals:
            if isinstance(raw_signal, IntentSignal):
                signal = raw_signal
            elif isinstance(raw_signal, Mapping):
                try:
                    signal = IntentSignal.model_validate(raw_signal)
                except Exception:
                    diagnostics.append(
                        {
                            "code": "INVALID_QUERY_SIGNAL",
                            "message": "查询能力信号格式无效，已忽略",
                            "retryable": False,
                        }
                    )
                    continue
            else:
                continue
            capability = canonical_capability(signal.capability)
            if capability is None:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_QUERY_CAPABILITY",
                        "message": "检索计划忽略了未注册的查询能力",
                        "retryable": False,
                        "details": {"capability": signal.capability},
                    }
                )
                continue
            if capability != signal.capability:
                signal = IntentSignal(
                    capability=capability,
                    confidence=signal.confidence,
                    matched_by=signal.matched_by,
                    matched_terms=signal.matched_terms,
                    priority=signal.priority or capability_priority(capability),
                )
            signals.append(signal)

        if not signals:
            legacy_intent = getattr(decomposition, "intent", "general")
            capability = canonical_capability(legacy_intent)
            if capability and capability != "general_search":
                signals.append(
                    IntentSignal(
                        capability=capability,
                        confidence=0.55,
                        matched_by="model",
                        matched_terms=tuple(),
                        priority=capability_priority(capability),
                    )
                )

        capabilities: list[str] = []

        def add_capability(capability: str) -> None:
            if capability not in capabilities:
                capabilities.append(capability)

        if getattr(decomposition, "target_entities", None):
            add_capability("entity_profile")
        for signal in sorted(
            signals,
            key=lambda item: (-item.priority, item.capability),
        ):
            add_capability(signal.capability)
        if getattr(decomposition, "keywords", None) or getattr(decomposition, "target_entities", None):
            add_capability("general_search")
        if not capabilities:
            add_capability("general_search")

        if "causal_reason" in capabilities and not scope.timeline_id:
            diagnostics.append(
                {
                    "code": "TIMELINE_SCOPE_UNSPECIFIED",
                    "message": "因果检索尚未绑定显式时间线，将使用现有作品级兼容范围",
                    "retryable": False,
                }
            )
        if scope.scope_status != "RESOLVED":
            diagnostics.append(
                {
                    "code": "RETRIEVAL_SCOPE_INCOMPLETE",
                    "message": "检索范围尚未完全解析，计划不得把未限定结果当作权威事实",
                    "retryable": False,
                }
            )

        limits = dict(cls._DEFAULT_LIMITS)
        limits["top_k_scenes"] = max(0, int(top_k_scenes))
        required_evidence = tuple(
            capability for capability in capabilities if capability != "general_search"
        )
        return RetrievalPlan(
            scope=scope,
            signals=tuple(signals),
            capabilities=tuple(capabilities),
            required_evidence=required_evidence,
            item_limits=limits,
            diagnostics=tuple(diagnostics),
        )


def canonical_capability(value: Any) -> Optional[str]:
    """Return a registered capability name, or ``None`` for an unknown config value."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    normalized = CAPABILITY_ALIASES.get(normalized, normalized)
    return normalized if normalized in CAPABILITY_NAMES else None


def capability_priority(capability: str) -> int:
    """Stable priority used only to choose a primary compatibility intent."""

    return {
        "causal_reason": 100,
        "timeline": 80,
        "relationship": 70,
        "ownership": 65,
        "skill": 60,
        "state": 55,
        "entity_profile": 50,
        "general_search": 0,
    }.get(capability, 10)


def _signal_confidence(*, capability: str, matched_term: str, matched_by: str) -> float:
    """Produce a conservative heuristic score; this is not a calibrated probability."""

    base = 0.72 if matched_by == "rule" else 0.68 if matched_by == "config" else 0.60
    length_bonus = min(len(matched_term), 8) * 0.025
    priority_bonus = min(capability_priority(capability), 100) / 1000
    return min(0.95, round(base + length_bonus + priority_bonus, 3))


def match_intent_signals(
    question: str,
    patterns: dict[str, tuple[str, ...]],
    *,
    matched_by: Literal["rule", "config"] = "rule",
) -> tuple[list[IntentSignal], list[dict[str, Any]]]:
    """Match normalized phrases and return all supported signals deterministically.

    Unknown work-scoped capability names are rejected as diagnostics rather than
    becoming silent no-op intent values.
    """

    diagnostics: list[dict[str, Any]] = []
    matches: dict[str, list[str]] = {}
    for raw_capability, raw_patterns in patterns.items():
        capability = canonical_capability(raw_capability)
        if capability is None:
            diagnostics.append(
                {
                    "code": "UNKNOWN_QUERY_CAPABILITY",
                    "message": "查询配置引用了未注册的能力，已忽略",
                    "retryable": False,
                    "details": {"capability": str(raw_capability)},
                }
            )
            continue
        for pattern in raw_patterns:
            if not isinstance(pattern, str):
                continue
            term = pattern.strip()
            if len(term) < 2:
                diagnostics.append(
                    {
                        "code": "QUERY_CAPABILITY_PATTERN_TOO_SHORT",
                        "message": "查询能力词至少需要两个字符，已忽略",
                        "retryable": False,
                        "details": {"capability": capability},
                    }
                )
                continue
            if term in question:
                matches.setdefault(capability, []).append(term)

    signals = [
        IntentSignal(
            capability=capability,
            confidence=_signal_confidence(
                capability=capability,
                matched_term=max(terms, key=len),
                matched_by=matched_by,
            ),
            matched_by=matched_by,
            matched_terms=tuple(sorted(set(terms), key=lambda value: (-len(value), value))),
            priority=capability_priority(capability),
        )
        for capability, terms in matches.items()
    ]
    signals.sort(
        key=lambda signal: (
            -signal.priority,
            -max((len(term) for term in signal.matched_terms), default=0),
            signal.capability,
        )
    )
    return signals, diagnostics
