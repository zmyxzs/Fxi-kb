"""
fxi.index_retrieval.query_engine - 自然语言智能问答与多源证据链检索聚合引擎
"""

import json
import yaml
from collections.abc import Mapping, Sequence
from typing import Any, Optional
from pydantic import BaseModel, Field

from fxi.core.config import FxiConfig, load_config
from fxi.domain.entities import get_worldview_genre, load_work_config, select_work_section
from fxi.domain.mutation_ledger import MutationLedger
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.index_retrieval.retrieval_plan import (
    GENERIC_INTENT_PATTERNS,
    QUERY_STOPWORDS,
    RetrievalPlanBuilder,
    ScopeContext,
    EvidenceBlock,
    IntentSignal,
    canonical_capability,
    capability_priority,
    match_intent_signals,
)
from fxi.model_gateway.gateway import ModelGateway
from fxi.sources.evidence_store import EvidenceStore
from fxi.state_ledger.calculator import LedgerCalculator
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.text_io import read_markdown_frontmatter
from fxi.timeline.dag import CausalDAG
from fxi.timeline.continuity import ContinuityManager
from fxi.timeline.pod_filter import PODFilter


class QueryDecomposition(BaseModel):
    """自然语言提问的意图与关键词拆解"""
    target_entities: list[str] = Field(default_factory=list, description="问题中提及或关联的核心实体/角色名")
    keywords: list[str] = Field(default_factory=list, description="用于全文检索的核心关键词（动词、名词、特殊设定）")
    intent: str = Field(default="general", description="兼容字段：主要查询能力；完整结果见 signals")
    signals: list[IntentSignal] = Field(default_factory=list, description="可组合的查询能力信号")
    diagnostics: list[dict[str, Any]] = Field(default_factory=list, description="拆解阶段的可诊断降级信息")


class AskResult(BaseModel):
    """自然语言问答综合结果与完整证据链溯源"""
    work_id: str
    question: str
    decomposition: QueryDecomposition
    answer: str
    evidence: dict[str, Any] = Field(default_factory=dict)


PROMPT_QUERY_DECOMPOSE = """分析用户针对小说作品提出的自然语言问题，提取关键实体名称和用于全文索引的关键检索词（2-4个）：
作品ID: {work_id}
用户问题: {question}

请以严格JSON格式输出，严禁附加多余废话：
```json
{{
  "target_entities": ["实体名1", "实体名2"],
  "keywords": ["关键词1", "关键词2"],
  "intent": "意图简述"
}}
```"""

PROMPT_SYNTHESIZE_ANSWER = """你是一个严谨的小说世界观与剧情知识库分析专家。请严格根据下方提供的多源检索证据，针对用户提出的问题进行全面、准确、有据可查的回答。

【回答规范】
1. 严禁凭空臆测，严禁使用未在证据中出现的设定。
2. 高信息密度，拒绝套话：直接给出核心事实、关键论据、人物动机与逻辑关系，严禁输出任何“作为知名作品角色”、“在网络文学中”等空洞宏观车轱辘话。
3. 严格注明出处：引用事实时请标明对应的角色档案、因果事件ID、原著章节或场景片段。

【用户问题】
{question}

【检索证据】
=== 实体档案与时序演变 ===
{entities_context}

=== 因果事件图谱 ===
{events_context}

=== 原著场景片段 (FTS5) ===
{scenes_context}

=== 状态账本 ===
{state_context}

=== 已接纳主张 ===
{claims_context}

=== 同人变动与 POD ===
{mutation_context}

=== 章节连续性台账 ===
{continuity_context}

请直接输出结构化、清晰、事实密度极高的权威回答："""


# 兼容旧的模块级名称；权威定义集中在 retrieval_plan，避免多套停用词/模式表。
_GENERIC_INTENT_PATTERNS = GENERIC_INTENT_PATTERNS
_QUERY_STOPWORDS = QUERY_STOPWORDS



class QueryEngine:
    """自然语言问答与证据链聚合引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.fts = ChineseFTS(self.config)
        self.gateway = ModelGateway(self.config)
        self.evidence_store = EvidenceStore(self.config.sources_dir)
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.dag = CausalDAG(self.config)
        self.state_ledger = LedgerCalculator(self.config)
        self.mutation_ledger = MutationLedger(self.config, self.db_client)
        self.continuity = ContinuityManager(self.config)
        self.pod_filter = PODFilter()

    @staticmethod
    def _diagnostic(
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        diagnostic: dict[str, Any] = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        if details:
            diagnostic["details"] = details
        return diagnostic

    def _load_intent_patterns(self, work_id: str) -> dict[str, tuple[str, ...]]:
        """Load optional work-scoped intent vocabulary without inventing facts."""
        patterns, _ = self._load_intent_patterns_with_diagnostics(work_id)
        return patterns

    def _load_intent_patterns_with_diagnostics(
        self,
        work_id: str,
    ) -> tuple[dict[str, tuple[str, ...]], list[dict[str, Any]]]:
        """Load work-scoped vocabulary and preserve malformed-config diagnostics."""
        settings, _ = load_work_config(self.config, work_id)
        if not settings:
            return {}, []
        raw = select_work_section(
            settings,
            "query_intent_keywords",
            get_worldview_genre(settings),
        )
        if not isinstance(raw, Mapping):
            return {}, []

        patterns: dict[str, tuple[str, ...]] = {}
        diagnostics: list[dict[str, Any]] = []
        for intent, values in raw.items():
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, Sequence) or isinstance(values, (bytes, bytearray, str)):
                diagnostics.append(
                    self._diagnostic(
                        "QUERY_CAPABILITY_PATTERN_INVALID",
                        "查询能力配置必须是字符串或字符串序列，已忽略",
                        details={"capability": str(intent)},
                    )
                )
                continue
            normalized = tuple(dict.fromkeys(
                value.strip()
                for value in values
                if isinstance(value, str) and value.strip()
            ))
            if normalized:
                patterns[str(intent).strip()] = normalized
            else:
                diagnostics.append(
                    self._diagnostic(
                        "QUERY_CAPABILITY_PATTERN_EMPTY",
                        "查询能力没有可用关键词，已忽略",
                        details={"capability": str(intent)},
                    )
                )
        return patterns, diagnostics

    @staticmethod
    def _normalize_question(question: str) -> str:
        """Normalize presentation variants without changing the user's semantics."""
        import unicodedata

        if not isinstance(question, str):
            return ""
        return " ".join(unicodedata.normalize("NFKC", question).strip().split())

    @staticmethod
    def _primary_intent(signals: list[IntentSignal], *, has_item_entity: bool) -> str:
        """Return the compatibility intent projection for old callers."""
        if signals:
            capability = signals[0].capability
            if capability == "general_search":
                return "general"
            if capability == "ownership":
                return "item_ownership"
            return capability
        if has_item_entity:
            return "item_ownership"
        return "general"

    @staticmethod
    def _merge_signals(*signal_groups: list[IntentSignal]) -> list[IntentSignal]:
        """Merge rule/config/model signals while keeping one deterministic signal per capability."""
        source_priority = {"rule": 0, "config": 1, "model": 2, "schema": 3}
        merged: dict[str, IntentSignal] = {}
        for signals in signal_groups:
            for signal in signals:
                current = merged.get(signal.capability)
                if current is None:
                    merged[signal.capability] = signal
                    continue
                matched_by = current.matched_by
                if source_priority.get(signal.matched_by, -1) > source_priority.get(
                    current.matched_by,
                    -1,
                ):
                    matched_by = signal.matched_by
                merged[signal.capability] = IntentSignal(
                    capability=signal.capability,
                    confidence=max(current.confidence, signal.confidence),
                    matched_by=matched_by,
                    matched_terms=tuple(
                        sorted(
                            set(current.matched_terms) | set(signal.matched_terms),
                            key=lambda value: (-len(value), value),
                        )
                    ),
                    priority=max(current.priority, signal.priority),
                )
        return sorted(
            merged.values(),
            key=lambda signal: (
                -signal.priority,
                -max((len(term) for term in signal.matched_terms), default=0),
                signal.capability,
            ),
        )

    def _normalize_model_decomposition(
        self,
        decomp: QueryDecomposition,
        fallback_signals: list[IntentSignal],
    ) -> QueryDecomposition:
        """Normalize model output into the same capability vocabulary as rules."""
        diagnostics = list(decomp.diagnostics)
        model_signals: list[IntentSignal] = []
        for raw_signal in decomp.signals:
            capability = canonical_capability(raw_signal.capability)
            if capability is None:
                diagnostics.append(
                    self._diagnostic(
                        "UNKNOWN_QUERY_CAPABILITY",
                        "模型查询分解返回了未注册的能力，已忽略",
                        details={"capability": raw_signal.capability},
                    )
                )
                continue
            model_signals.append(
                IntentSignal(
                    capability=capability,
                    confidence=raw_signal.confidence,
                    matched_by="model",
                    matched_terms=raw_signal.matched_terms,
                    priority=raw_signal.priority or capability_priority(capability),
                )
            )

        canonical_intent = canonical_capability(decomp.intent)
        if decomp.intent and canonical_intent is None and decomp.intent != "general":
            diagnostics.append(
                self._diagnostic(
                    "UNKNOWN_QUERY_CAPABILITY",
                    "模型查询分解返回了未注册的主要能力，已降级为 general",
                    details={"capability": decomp.intent},
                )
            )
            canonical_intent = "general_search"
        if canonical_intent and canonical_intent != "general_search" and not model_signals:
            model_signals.append(
                IntentSignal(
                    capability=canonical_intent,
                    confidence=0.6,
                    matched_by="model",
                    matched_terms=tuple(),
                    priority=capability_priority(canonical_intent),
                )
            )

        decomp.signals = self._merge_signals(fallback_signals, model_signals)
        decomp.diagnostics = diagnostics
        decomp.intent = self._primary_intent(decomp.signals, has_item_entity=False)
        if not decomp.signals and decomp.target_entities:
            decomp.intent = "general"
        return decomp

    def decompose_query(self, work_id: str, question: str, use_mock: bool = False) -> QueryDecomposition:
        """解析并拆解自然语言提问（优先毫秒级快速分流，未命中再调大模型）"""
        # 1. 优先本地毫秒级快查 (Fast-Path)：若问题中已明确出现库内实体名，直接返回，立省 6 秒
        fast_decomp = self._heuristic_decompose(work_id, question)
        if fast_decomp.target_entities:
            return fast_decomp

        if use_mock:
            return fast_decomp

        # 2. 未直接命中库内实体名时，由模型深度推断潜在指代 (如 '那个盲眼少年是谁')
        diagnostics: list[dict[str, Any]] = []
        try:
            prompt = PROMPT_QUERY_DECOMPOSE.format(work_id=work_id, question=question)
            decomp = self.gateway.complete(
                task_type="fast_extraction",
                prompt=prompt,
                schema=QueryDecomposition,
                use_cache=True,
                use_mock=use_mock
            )
            if isinstance(decomp, QueryDecomposition) and (decomp.target_entities or decomp.keywords):
                return self._normalize_model_decomposition(
                    decomp,
                    fallback_signals=fast_decomp.signals,
                )
            diagnostics.append(
                self._diagnostic(
                    "MODEL_DECOMPOSITION_INVALID",
                    "模型查询分解未返回可用结果，已使用确定性拆解",
                    details={"result_type": type(decomp).__name__},
                )
            )
        except Exception as err:
            diagnostics.append(
                self._diagnostic(
                    "MODEL_DECOMPOSITION_FAILED",
                    "模型查询分解失败，已使用确定性拆解",
                    retryable=True,
                    details={"exception_type": type(err).__name__},
                )
            )

        fast_decomp.diagnostics.extend(diagnostics)
        return fast_decomp


    def _heuristic_decompose(self, work_id: str, question: str) -> QueryDecomposition:
        """基于已知实体名与 jieba 分词的确定性保底拆解"""
        question_text = self._normalize_question(question)
        with self.db_client.get_connection() as conn:
            cur = conn.execute(
                "SELECT entity_id, name, category, aliases_json FROM entities WHERE work_id = ?",
                (work_id,)
            )
            known_entities = cur.fetchall()

        matched_entities: list[str] = []
        matched_categories: dict[str, str] = {}
        for row in known_entities:
            name = row["name"]
            if name and name in question_text:
                matched_entities.append(name)
                matched_categories[name] = str(row["category"] or "")
            aliases = json.loads(row["aliases_json"] or "[]") if row["aliases_json"] else []
            for alias in aliases:
                if alias and alias in question_text and name not in matched_entities:
                    matched_entities.append(name)
                    matched_categories[name] = str(row["category"] or "")

        # 过滤被更长实体名包含的子实体，避免同一实体产生重复命中。
        matched_entities = [
            ent for ent in matched_entities
            if not any(other != ent and ent in other for other in matched_entities)
        ]

        import jieba
        words = [w.strip() for w in jieba.cut_for_search(question_text) if len(w.strip()) > 1]

        # 关键词必须排除实体名及其子串，避免实体名污染全文搜索
        keywords = []
        for w in words:
            if w in _QUERY_STOPWORDS:
                continue
            if any(w in ent or ent in w for ent in matched_entities):
                continue
            if w not in keywords:
                keywords.append(w)

        # 意图只产生可解释信号：通用语言结构来自引擎，题材词汇来自当前作品配置。
        config_patterns, config_diagnostics = self._load_intent_patterns_with_diagnostics(work_id)
        generic_signals, generic_diagnostics = match_intent_signals(
            question_text,
            _GENERIC_INTENT_PATTERNS,
            matched_by="rule",
        )
        config_signals, config_match_diagnostics = match_intent_signals(
            question_text,
            config_patterns,
            matched_by="config",
        )
        signals = self._merge_signals(generic_signals, config_signals)
        if any(matched_categories.get(entity) == "item" for entity in matched_entities):
            signals = self._merge_signals(
                signals,
                [
                    IntentSignal(
                        capability="ownership",
                        confidence=0.82,
                        matched_by="schema",
                        matched_terms=tuple(
                            entity
                            for entity in matched_entities
                            if matched_categories.get(entity) == "item"
                        ),
                        priority=capability_priority("ownership"),
                    )
                ],
            )

        keywords.sort(key=lambda x: -len(x))

        return QueryDecomposition(
            target_entities=matched_entities,
            keywords=keywords[:4] if keywords else ([question_text[:4]] if question_text else []),
            intent=self._primary_intent(
                signals,
                has_item_entity=any(
                    matched_categories.get(entity) == "item" for entity in matched_entities
                ),
            ),
            signals=signals,
            diagnostics=[*config_diagnostics, *generic_diagnostics, *config_match_diagnostics],
        )

    @staticmethod
    def _effective_chapter(narrative_order: int) -> int:
        return narrative_order // 10 if narrative_order >= 100 else narrative_order

    def _load_claims(
        self,
        conn,
        work_id: str,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """读取当前作品的已接纳主张，拒绝无作品归属的全局泄漏。"""
        rows = conn.execute(
            """
            SELECT cv.claim_id, cv.statement, cv.status, cv.version,
                   cv.work_id AS claim_work_id, cf.work_id AS family_work_id, cf.scope
            FROM claim_versions AS cv
            LEFT JOIN claim_families AS cf ON cf.family_key = cv.family_key
            WHERE cv.work_id = ?
              AND (cf.work_id = ? OR cf.work_id IS NULL)
              AND cv.status = 'accepted'
            ORDER BY cv.version DESC, cv.id DESC
            """,
            (work_id, work_id),
        ).fetchall()

        claims: list[dict[str, Any]] = []
        for row in rows:
            evidence_sql = """
                SELECT ce.source_id, ws.source_version, ce.scene_uuid,
                       ce.line_start, ce.line_end, ce.quote
                FROM claim_evidence AS ce
                JOIN work_sources AS ws
                    ON ws.source_id = ce.source_id AND ws.work_id = ?
                WHERE ce.claim_id = ?
            """
            evidence_params: list[Any] = [work_id, row["claim_id"]]
            if source_id:
                evidence_sql += " AND ce.source_id = ?"
                evidence_params.append(source_id)
            if source_version:
                evidence_sql += " AND ws.source_version = ?"
                evidence_params.append(source_version)
            evidence_sql += " ORDER BY ce.id"
            evidence = conn.execute(evidence_sql, evidence_params).fetchall()
            scope = str(row["scope"] or "").strip().lower()
            claims.append({
                "claim_id": row["claim_id"],
                "statement": row["statement"],
                "status": row["status"],
                "version": row["version"],
                "work_id": row["claim_work_id"],
                "family_work_id": row["family_work_id"],
                "scope": scope,
                "is_secret": scope not in {"", "all", "public", "global", "canon", "world"},
                "evidence": [dict(item) for item in evidence],
            })
        return claims

    def _load_state_evidence(
        self,
        conn,
        work_id: str,
        entity_ids: set[str],
        knowledge_version: Optional[str],
        narrative_order: Optional[int],
    ) -> dict[str, Any]:
        """读取状态事件并按当前时间/知识版本计算可用余额。"""
        clauses = ["e.work_id = ?"]
        params: list[Any] = [work_id]
        if entity_ids:
            placeholders = ", ".join("?" for _ in entity_ids)
            clauses.append(f"e.entity_id IN ({placeholders})")
            params.extend(sorted(entity_ids))
        if narrative_order is not None:
            clauses.append("e.narrative_order <= ?")
            params.append(narrative_order)
        if knowledge_version is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM state_event_receipts AS r "
                "WHERE r.state_event_id = e.event_id AND r.knowledge_version = ?)"
            )
            params.append(knowledge_version)

        state_sql = (
            """
            SELECT e.event_id, e.entity_id, e.metric_id, e.delta, e.new_value,
                   e.is_anchor, e.scene_uuid, e.narrative_order, e.reason,
                   e.rule_version, e.created_at
            FROM state_events AS e
            WHERE """
            + " AND ".join(clauses)
            + "\n            ORDER BY e.narrative_order DESC, e.event_id DESC\n            LIMIT 100"
        )
        rows = conn.execute(state_sql, params).fetchall()
        events = [dict(row) for row in rows]

        latest_orders: dict[tuple[str, str], int] = {}
        for event in events:
            key = (event["entity_id"], event["metric_id"])
            latest_orders.setdefault(key, int(event["narrative_order"]))

        balances: list[dict[str, Any]] = []
        for (entity_id, metric_id), latest_order in latest_orders.items():
            balance_order = narrative_order if narrative_order is not None else latest_order
            snapshot = self.state_ledger.calculate_balance(
                work_id=work_id,
                entity_id=entity_id,
                metric_id=metric_id,
                narrative_order=balance_order,
                knowledge_version=knowledge_version,
            )
            balances.append({
                "work_id": snapshot.work_id,
                "entity_id": snapshot.entity_id,
                "metric_id": snapshot.metric_id,
                "computed_value": snapshot.computed_value,
                "status": snapshot.status.value,
                "narrative_order": snapshot.narrative_order,
            })

        return {
            "available": bool(events),
            "status": "EXPLICIT" if events else "UNMEASURED",
            "events": events,
            "balances": balances,
            "scope": {
                "work_id": work_id,
                "knowledge_version": knowledge_version,
                "narrative_order": narrative_order,
            },
        }

    def _load_continuity_evidence(
        self,
        work_id: str,
        narrative_order: Optional[int],
    ) -> dict[str, Any]:
        if narrative_order is None:
            record = self.continuity.get_latest_continuity(work_id)
        else:
            ledger = self.continuity.load_ledger(work_id)
            chapter = self._effective_chapter(narrative_order)
            eligible = [
                (int(index), item)
                for index, item in ledger.get("chapters", {}).items()
                if int(index) <= chapter and isinstance(item, dict)
            ]
            record = max(eligible, key=lambda item: item[0])[1] if eligible else None
        return {
            "available": record is not None,
            "chapter": record,
            "scope": {"work_id": work_id, "narrative_order": narrative_order},
        }

    def retrieve_evidence(
        self,
        work_id: str,
        decomp: QueryDecomposition,
        top_k_scenes: int = 8,
        *,
        knowledge_version: Optional[str] = None,
        narrative_order: Optional[int] = None,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        timeline_id: Optional[str] = None,
        divergence_narrative_order: Optional[int] = None,
    ) -> dict[str, Any]:
        """多源检索并保留状态、主张、变动、POD 与连续性事实。"""
        if narrative_order is not None and narrative_order < 0:
            raise ValueError("narrative_order must be non-negative")
        if source_id is not None or source_version is not None:
            if (
                not isinstance(source_id, str)
                or not source_id.strip()
                or not isinstance(source_version, str)
                or not source_version.strip()
            ):
                raise ValueError("source_id and source_version must be provided together")
        diagnostics = list(getattr(decomp, "diagnostics", []))
        source_scope_required = False
        if source_id is None and source_version is None:
            from fxi.api.registry import SourceScopeRequiredError, WorkRegistry

            try:
                binding = WorkRegistry(self.db_client).resolve(work_id)
            except SourceScopeRequiredError:
                source_scope_required = True
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_SCOPE_REQUIRED",
                        "作品绑定了多个来源，必须显式提供 source_id 与 source_version",
                    )
                )
            except Exception as err:
                source_scope_required = True
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_SCOPE_REQUIRED",
                        "未能解析作品的唯一活动来源，未读取未版本化事实",
                        details={"exception_type": type(err).__name__},
                    )
                )
            else:
                if not binding.source_version:
                    source_scope_required = True
                    diagnostics.append(
                        self._diagnostic(
                            "SOURCE_SCOPE_REQUIRED",
                            "活动来源缺少不可变版本，必须显式修复来源登记",
                        )
                    )
                else:
                    source_id = binding.source_id
                    source_version = binding.source_version
        scope = ScopeContext(
            work_id=work_id,
            timeline_id=timeline_id,
            source_id=source_id,
            source_version=source_version,
            knowledge_version=knowledge_version,
            narrative_order=narrative_order,
            divergence_narrative_order=divergence_narrative_order,
            scope_status="REQUIRED" if source_scope_required else "RESOLVED",
        )
        retrieval_plan = RetrievalPlanBuilder.build(
            decomp,
            scope,
            top_k_scenes=top_k_scenes,
        )
        diagnostics.extend(retrieval_plan.diagnostics)
        versioned_source_requested = source_id is not None
        immutable_source_loaded = False
        immutable_document_ids: dict[int, str] = {}
        immutable_document_cache: dict[int, tuple[bool, str]] = {}
        if versioned_source_requested:
            source_scope_available = True
            with self.db_client.get_connection() as conn:
                bindings = conn.execute(
                    """
                    SELECT work_id, source_version
                    FROM work_sources
                    WHERE source_id = ?
                    ORDER BY work_id
                    """,
                    (source_id,),
                ).fetchall()
            if bindings:
                matching_binding = next(
                    (
                        binding
                        for binding in bindings
                        if binding["work_id"] == work_id
                    ),
                    None,
                )
                if matching_binding is None:
                    source_scope_available = False
                    diagnostics.append(
                        self._diagnostic(
                            "SOURCE_NOT_BOUND_TO_WORK",
                            "指定来源已登记给其他作品，未读取其版本正文",
                            details={
                                "source_id": source_id,
                                "work_id": work_id,
                            },
                        )
                    )
                elif matching_binding["source_version"] != source_version:
                    source_scope_available = False
                    diagnostics.append(
                        self._diagnostic(
                            "SOURCE_VERSION_MISMATCH",
                            "指定来源版本与当前作品登记版本不一致，未读取正文",
                            details={
                                "source_id": source_id,
                                "work_id": work_id,
                                "expected_source_version": matching_binding["source_version"],
                                "requested_source_version": source_version,
                            },
                        )
                    )
            else:
                # 显式 source_id/source_version 已经给出来源身份；即使登记表
                # 尚未补齐，也可以只读取该不可变快照，不能回退到其他来源绑定。
                source_scope_available = True
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_BINDING_UNREGISTERED",
                        "指定来源未在作品登记表中绑定，仅按显式来源身份读取快照",
                        details={"source_id": source_id, "work_id": work_id},
                    )
                )
            if source_scope_available:
                try:
                    snapshot = self.evidence_store.read_snapshot(source_id, source_version)
                    immutable_document_ids = {
                        int(document.chapter_index): document.document_id
                        for document in snapshot.documents
                    }
                    immutable_source_loaded = True
                except Exception as err:
                    diagnostics.append(
                        self._diagnostic(
                            "SOURCE_SNAPSHOT_UNAVAILABLE",
                            "指定来源快照不可用，未回读当前来源目录",
                            details={"exception_type": type(err).__name__},
                        )
                    )
            else:
                try:
                    self.evidence_store.read_snapshot(source_id, source_version)
                except Exception as err:
                    diagnostics.append(
                        self._diagnostic(
                            "SOURCE_SNAPSHOT_UNAVAILABLE",
                            "指定来源快照不可用，未回读当前来源目录",
                            details={"exception_type": type(err).__name__},
                        )
                    )

        def _read_immutable_source_document(chapter_index: Any) -> tuple[bool, str]:
            """按快照章节读取不可变正文；失败时禁止退回当前来源目录。"""
            if not immutable_source_loaded:
                return False, ""
            try:
                chapter = int(chapter_index)
            except (TypeError, ValueError):
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_DOCUMENT_INVALID_CHAPTER",
                        "FTS 命中缺少可用于绑定来源文档的章节序号",
                        details={"chapter_index": str(chapter_index)},
                    )
                )
                return False, ""
            if chapter in immutable_document_cache:
                return immutable_document_cache[chapter]
            document_id = immutable_document_ids.get(chapter)
            if not document_id:
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_DOCUMENT_NOT_FOUND",
                        "指定来源版本没有对应章节文档，未使用当前来源目录正文",
                        details={"chapter_index": chapter},
                    )
                )
                immutable_document_cache[chapter] = (False, "")
                return immutable_document_cache[chapter]
            try:
                stored = self.evidence_store.read_document(
                    source_id,
                    source_version,
                    document_id,
                )
            except Exception as err:
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_DOCUMENT_READ_FAILED",
                        "指定来源版本文档不可读，未伪造当前版本正文",
                        details={
                            "exception_type": type(err).__name__,
                            "chapter_index": chapter,
                            "document_id": document_id,
                        },
                    )
                )
                immutable_document_cache[chapter] = (False, "")
                return immutable_document_cache[chapter]
            immutable_document_cache[chapter] = (True, stored.content)
            return immutable_document_cache[chapter]

        entities_data: list[dict[str, Any]] = []
        events_data: list[dict[str, Any]] = []
        claims_data: list[dict[str, Any]] = []
        state_evidence: dict[str, Any]

        with self.db_client.get_connection() as conn:
            # 1. 实体画像检索
            for ent_name in decomp.target_entities:
                cur = conn.execute(
                    "SELECT entity_id, name, category, attributes_yaml FROM entities WHERE work_id = ? AND (name = ? OR aliases_json LIKE ?)",
                    (work_id, ent_name, f'%"{ent_name}"%')
                )
                row = cur.fetchone()
                if row:
                    ent_id = row["entity_id"]
                    doc_path = self.config.projects_dir / work_id / "entities" / f"{row['category']}s" / f"{ent_id}.md"
                    body_text = ""
                    bio_source = "unavailable"
                    if versioned_source_requested:
                        diagnostics.append(
                            self._diagnostic(
                                "PROJECT_DOCUMENT_NOT_SOURCE_VERSIONED",
                                "项目实体档案不是 source snapshot 文档，未回读当前项目投影",
                                details={
                                    "document": f"entities/{row['category']}s/{ent_id}.md",
                                    "source_version": source_version,
                                },
                            )
                        )
                    elif doc_path.is_file():
                        try:
                            _, body = read_markdown_frontmatter(doc_path)
                            body_text = body.strip()
                            bio_source = "document"
                        except Exception as err:
                            diagnostics.append(
                                self._diagnostic(
                                    "PROJECT_DOCUMENT_READ_FAILED",
                                    "项目实体文档读取失败，未使用不可验证的档案正文",
                                    details={
                                        "exception_type": type(err).__name__,
                                        "document": f"entities/{row['category']}s/{ent_id}.md",
                                    },
                                )
                            )
                    else:
                        diagnostics.append(
                            self._diagnostic(
                                "PROJECT_DOCUMENT_MISSING",
                                "项目实体文档不存在，档案正文不可用",
                                details={"document": f"entities/{row['category']}s/{ent_id}.md"},
                            )
                        )

                    # 查询性格演变相态
                    cur_p = conn.execute(
                        "SELECT phase_id, phase_name, valid_from_order, valid_to_order, traits_json FROM entity_phases WHERE work_id = ? AND entity_id = ? ORDER BY valid_from_order",
                        (work_id, ent_id)
                    )
                    phases = cur_p.fetchall()
                    if narrative_order is not None:
                        phases = [
                            phase for phase in phases
                            if phase["valid_from_order"] <= narrative_order
                            and (
                                phase["valid_to_order"] is None
                                or phase["valid_to_order"] >= narrative_order
                            )
                        ]

                    # 查询技能树（若存在）
                    cur_s = conn.execute(
                        """
                        SELECT cs.skill_id, COALESCE(st.name, cs.skill_id) AS skill_name, COALESCE(st.tier, 1) AS tier, COALESCE(st.skill_type, 'active') AS skill_type, COALESCE(st.description, '') AS description
                        FROM character_skills cs
                        LEFT JOIN skills_tree st ON cs.work_id = st.work_id AND cs.skill_id = st.skill_id
                        WHERE cs.work_id = ? AND cs.entity_id = ?
                        """,
                        (work_id, ent_id)
                    )
                    skills = cur_s.fetchall()

                    # 查询物品持有流转
                    cur_o = conn.execute(
                        """
                        SELECT item_ref_id, transfer_type, narrative_order, reason
                        FROM item_ownership_events
                        WHERE work_id = ? AND (from_owner_id = ? OR to_owner_id = ?)
                        ORDER BY narrative_order
                        """,
                        (work_id, ent_id, ent_id)
                    )
                    ownership_events = cur_o.fetchall()
                    if narrative_order is not None:
                        ownership_events = [
                            event for event in ownership_events
                            if event["narrative_order"] <= narrative_order
                        ]

                    # 查询人际关系
                    cur_r = conn.execute(
                        """
                        SELECT source_id, target_id, relation_type, valid_from_order, valid_to_order
                        FROM entity_relations
                        WHERE work_id = ? AND (source_id = ? OR target_id = ?)
                        """,
                        (work_id, ent_id, ent_id)
                    )
                    relations = cur_r.fetchall()
                    if narrative_order is not None:
                        relations = [
                            relation for relation in relations
                            if relation["valid_from_order"] <= narrative_order
                            and (
                                relation["valid_to_order"] is None
                                or relation["valid_to_order"] >= narrative_order
                            )
                        ]

                    entities_data.append({
                        "entity_id": ent_id,
                        "name": row["name"],
                        "category": row["category"],
                        "attributes": yaml.safe_load(row["attributes_yaml"] or "{}"),
                        "bio": body_text,
                        "bio_source": bio_source,
                        "bio_available": bool(body_text),
                        "phases": [
                            {
                                "phase_id": p["phase_id"],
                                "phase_name": p["phase_name"],
                                "valid_from_order": p["valid_from_order"],
                                "valid_to_order": p["valid_to_order"],
                                "traits": json.loads(p["traits_json"] or "[]")
                            }
                            for p in phases
                        ],
                        "skills": [dict(s) for s in skills],
                        "ownership_events": [dict(o) for o in ownership_events],
                        "relations": [dict(r) for r in relations],
                    })


            # 2. 因果图谱事件检索与拓扑因果溯源
            matched_events: list[dict[str, Any]] = []
            seen_event_ids: set[str] = set()
            event_scope_clause = ""
            event_scope_params: list[Any] = []
            if narrative_order is not None:
                event_scope_clause += " AND narrative_order <= ?"
                event_scope_params.append(narrative_order)
            if timeline_id is not None:
                event_scope_clause += " AND timeline_id = ?"
                event_scope_params.append(timeline_id)
            if source_scope_required:
                # 没有唯一来源时，事件表中的旧投影也不能作为权威事实读取。
                event_scope_clause += " AND 0"
            elif versioned_source_requested:
                if source_scope_available:
                    event_scope_clause += (
                        " AND ((source_id = ? AND source_version = ?)"
                        " OR (source_id IS NULL AND source_version IS NULL))"
                    )
                    event_scope_params.extend([source_id, source_version])
                else:
                    # 仅允许未绑定来源版本的旧事件作为后续 LEGACY_HINT。
                    event_scope_clause += " AND source_id IS NULL AND source_version IS NULL"

            # 2.1 优先按核心关键词检索事件（剧情/事件聚焦点）
            if decomp.keywords:
                kw_conds = " OR ".join(["summary LIKE ?" for _ in decomp.keywords])
                kw_params = [f"%{k}%" for k in decomp.keywords]
                cur_kw = conn.execute(
                    f"SELECT event_id, scene_uuid, narrative_order, summary, status, timeline_id, source_id, source_version FROM causal_events WHERE work_id = ?{event_scope_clause} AND ({kw_conds}) ORDER BY narrative_order ASC LIMIT 10",
                    (work_id, *event_scope_params, *kw_params)
                )
                for r in cur_kw.fetchall():
                    ev_dict = dict(r)
                    if ev_dict["event_id"] not in seen_event_ids:
                        seen_event_ids.add(ev_dict["event_id"])
                        matched_events.append(ev_dict)

            # 2.2 若意图为因果溯源或问题包含“为什么/原因”，沿 DAG 拓扑反向拉取直接原因事件 (向上追溯 1~3 层)
            cause_events: list[dict[str, Any]] = []
            is_causal = decomp.intent == "causal_reason" or any(
                signal.capability == "causal_reason"
                for signal in getattr(decomp, "signals", [])
            ) or any(w in decomp.keywords for w in ["为什么", "原因"])
            if is_causal:
                current_frontier = [
                    ev["event_id"]
                    for ev in matched_events[
                        : retrieval_plan.item_limits["causal_seed_events"]
                    ]
                ]
                visited_causes = set(seen_event_ids)
                for hop in range(retrieval_plan.item_limits["causal_hops"]):
                    next_frontier = []
                    for eid in current_frontier:
                        direct_causes = self.dag.get_direct_causes(
                            work_id,
                            eid,
                            timeline_id=timeline_id,
                        )
                        for cid in direct_causes:
                            if len(cause_events) >= retrieval_plan.item_limits["causal_events"]:
                                break
                            if cid not in visited_causes:
                                visited_causes.add(cid)
                                next_frontier.append(cid)
                                cur_c = conn.execute(
                                    f"SELECT event_id, scene_uuid, narrative_order, summary, status, timeline_id, source_id, source_version FROM causal_events WHERE work_id = ? AND event_id = ?{event_scope_clause}",
                                    (work_id, cid, *event_scope_params)
                                )
                                crow = cur_c.fetchone()
                                if crow:
                                    c_dict = dict(crow)
                                    c_dict["summary"] = f"[前置动因/起因(第{hop+1}层)] {c_dict['summary']}"
                                    cause_events.append(c_dict)
                        if len(cause_events) >= retrieval_plan.item_limits["causal_events"]:
                            break
                    current_frontier = next_frontier
                    if len(cause_events) >= retrieval_plan.item_limits["causal_events"]:
                        break

            # 2.3 若关键词未命中任何事件，回退至实体名检索
            if not matched_events and decomp.target_entities:
                ent_conds = " OR ".join(["summary LIKE ?" for _ in decomp.target_entities])
                ent_params = [f"%{e}%" for e in decomp.target_entities]
                cur_ent = conn.execute(
                    f"SELECT event_id, scene_uuid, narrative_order, summary, status, timeline_id, source_id, source_version FROM causal_events WHERE work_id = ?{event_scope_clause} AND ({ent_conds}) ORDER BY narrative_order ASC LIMIT 10",
                    (work_id, *event_scope_params, *ent_params)
                )
                for r in cur_ent.fetchall():
                    ev_dict = dict(r)
                    if ev_dict["event_id"] not in seen_event_ids:
                        seen_event_ids.add(ev_dict["event_id"])
                        matched_events.append(ev_dict)

            all_events = cause_events + matched_events
            all_events.sort(key=lambda x: x.get("narrative_order", 0))
            events_data = self.pod_filter.filter_events(all_events, divergence_narrative_order)
            for event in events_data:
                if versioned_source_requested:
                    if (
                        event.get("source_id") == source_id
                        and event.get("source_version") == source_version
                    ):
                        event["provenance_status"] = "AVAILABLE"
                    else:
                        event["provenance_status"] = "LEGACY_HINT"
                elif source_scope_required:
                    event["provenance_status"] = "INCOMPLETE"
                else:
                    event["provenance_status"] = "INCOMPLETE"
            claims_data = self._load_claims(
                conn,
                work_id,
                source_id=source_id,
                source_version=source_version,
            )
            entity_ids = {entity["entity_id"] for entity in entities_data}
            state_evidence = self._load_state_evidence(
                conn,
                work_id,
                entity_ids,
                knowledge_version,
                narrative_order,
            )

        mutations = self.mutation_ledger.list_mutations(
            work_id=work_id,
            chapter=(self._effective_chapter(narrative_order) if narrative_order is not None else None),
            status="active",
        )
        continuity_evidence = self._load_continuity_evidence(work_id, narrative_order)
        pod_event_ids = {event["event_id"] for event in all_events}
        visible_pod_event_ids = {event["event_id"] for event in events_data}

        # 3. FTS5 原著场景切片检索
        # 关键词优先作为 FTS 必须命中项；实体已经由结构化查询读取，
        # 不再强制要求它与关键词出现在同一场景，避免无谓的 AND 误伤。
        required_terms = [decomp.keywords[0]] if decomp.keywords else []
        if not required_terms and decomp.target_entities:
            required_terms.append(decomp.target_entities[0])

        search_query = " ".join(decomp.keywords or decomp.target_entities)
        is_causal = (decomp.intent == "causal_reason")

        def search_scenes(order_by: str) -> list[dict[str, Any]]:
            """按来源版本检索；范围不可用时只允许 legacy 片段提示。"""

            if source_scope_required:
                legacy = self.fts.search(
                    query=search_query,
                    work_id=work_id,
                    limit=top_k_scenes,
                    required_terms=required_terms if len(required_terms) >= 2 else None,
                    order_by=order_by,
                    allow_legacy_hint=True,
                )
                if legacy:
                    diagnostics.append(
                        self._diagnostic(
                            "SOURCE_SCOPE_REQUIRED_LEGACY_HINT",
                            "未解析到唯一来源，FTS 片段仅作为非事实提示",
                        )
                    )
                return legacy

            scoped = self.fts.search(
                query=search_query,
                work_id=work_id,
                limit=top_k_scenes,
                required_terms=required_terms if len(required_terms) >= 2 else None,
                order_by=order_by,
                source_id=source_id,
                source_version=source_version,
            )
            if not versioned_source_requested:
                return scoped
            if scoped:
                return scoped
            legacy = self.fts.search(
                query=search_query,
                work_id=work_id,
                limit=top_k_scenes,
                required_terms=required_terms if len(required_terms) >= 2 else None,
                order_by=order_by,
                allow_legacy_hint=True,
            )
            if legacy and not scoped:
                diagnostics.append(
                    self._diagnostic(
                        "SOURCE_INDEX_NOT_VERSIONED",
                        "来源版本范围不可用，FTS 片段仅作为非事实提示",
                    )
                )
            return legacy

        # 若属于起因/原因探究类问题，优先检索按章节顺序排列的最早发生场景（决策与动因发生在事件起源处）
        raw_scenes = []
        if is_causal:
            raw_scenes = search_scenes("chronological")

        # 若不是因果问题或按时间排序数量不足，结合 BM25 相关度最高场景
        if len(raw_scenes) < top_k_scenes:
            bm25_scenes = search_scenes("rank")
            seen_existing = {s["scene_uuid"] for s in raw_scenes}
            for sc in bm25_scenes:
                if sc["scene_uuid"] not in seen_existing:
                    raw_scenes.append(sc)
                    if len(raw_scenes) >= top_k_scenes:
                        break


        scenes_data = []
        seen_scene_uuids = set()
        for sc in raw_scenes:
            scene_uuid = sc["scene_uuid"]
            seen_scene_uuids.add(scene_uuid)
            scene_text = ""
            content_source = "fts_snippet"
            document_available = False
            content_scope = "scene"
            if versioned_source_requested:
                document_available, immutable_text = _read_immutable_source_document(
                    sc.get("chapter_index")
                )
                if document_available:
                    scene_text = immutable_text[:1200] + ("..." if len(immutable_text) > 1200 else "")
                    content_source = "evidence_store"
                    content_scope = "source_document"
            else:
                scene_path = self.config.sources_dir / work_id / "scenes" / f"{scene_uuid}.md"
                if scene_path.is_file():
                    try:
                        full_text = scene_path.read_text(encoding="utf-8").strip()
                        scene_text = full_text[:1200] + ("..." if len(full_text) > 1200 else "")
                        content_source = "document"
                        document_available = True
                    except Exception as err:
                        diagnostics.append(
                            self._diagnostic(
                                "SCENE_DOCUMENT_READ_FAILED",
                                "场景文档读取失败，仅保留可识别为片段的 FTS 结果",
                                details={
                                    "exception_type": type(err).__name__,
                                    "document": f"scenes/{scene_uuid}.md",
                                },
                            )
                        )
                else:
                    diagnostics.append(
                        self._diagnostic(
                            "SCENE_DOCUMENT_MISSING",
                            "场景文档不存在，仅保留可识别为片段的 FTS 结果",
                            details={"document": f"scenes/{scene_uuid}.md"},
                        )
                    )

            if source_scope_required:
                # 可以保留 FTS 定位和文档读取诊断，但当前来源未解析时不把
                # 当前目录正文作为事实返回。
                scene_text = ""
                content_source = "fts_snippet"
                document_available = False
                content_scope = "unversioned_fts_hint"

            scenes_data.append({
                "scene_uuid": scene_uuid,
                "work_id": sc["work_id"],
                "chapter_index": sc.get("chapter_index", 0),
                "snippet": sc["snippet"],
                "content": (
                    scene_text
                    if versioned_source_requested
                    else scene_text or sc["snippet"]
                ),
                "content_source": content_source,
                "content_scope": (
                    content_scope
                    if document_available
                    else "unversioned_fts_hint"
                    if versioned_source_requested
                    else content_scope
                ),
                "document_available": document_available,
            })

        # 补充因果事件直接指向的原著场景
        for ev in events_data[:3]:
            sc_id = ev.get("scene_uuid")
            if sc_id and sc_id not in seen_scene_uuids:
                if versioned_source_requested:
                    document_available, immutable_text = _read_immutable_source_document(
                        ev.get("narrative_order")
                    )
                    if document_available:
                        seen_scene_uuids.add(sc_id)
                        scenes_data.append({
                            "scene_uuid": sc_id,
                            "work_id": work_id,
                            "chapter_index": ev.get("narrative_order", 0),
                            "snippet": f"【事件关联场景】第{ev.get('narrative_order')}章",
                            "content": immutable_text[:1200] + ("..." if len(immutable_text) > 1200 else ""),
                            "content_source": "evidence_store",
                            "content_scope": "source_document",
                            "document_available": True,
                        })
                else:
                    sc_path = self.config.sources_dir / work_id / "scenes" / f"{sc_id}.md"
                    if sc_path.is_file():
                        try:
                            content = sc_path.read_text(encoding="utf-8").strip()
                            seen_scene_uuids.add(sc_id)
                            scenes_data.append({
                                "scene_uuid": sc_id,
                                "work_id": work_id,
                                "chapter_index": ev.get("narrative_order", 0),
                                "snippet": f"【事件关联场景】第{ev.get('narrative_order')}章",
                                "content": content[:1200] + ("..." if len(content) > 1200 else ""),
                                "content_source": "document",
                                "content_scope": "scene",
                                "document_available": True,
                            })
                        except Exception as err:
                            diagnostics.append(
                                self._diagnostic(
                                    "SCENE_DOCUMENT_READ_FAILED",
                                    "事件关联场景文档读取失败，未伪造场景正文",
                                    details={
                                        "exception_type": type(err).__name__,
                                        "document": f"scenes/{sc_id}.md",
                                    },
                                )
                            )
                    else:
                        diagnostics.append(
                            self._diagnostic(
                                "SCENE_DOCUMENT_MISSING",
                                "事件关联场景文档不存在，场景正文不可用",
                                details={"document": f"scenes/{sc_id}.md"},
                            )
                        )

        scope_payload = retrieval_plan.scope.model_dump(mode="json")

        def _item_dict(item: Any) -> dict[str, Any]:
            if isinstance(item, Mapping):
                return dict(item)
            to_dict = getattr(item, "to_dict", None)
            if callable(to_dict):
                value = to_dict()
                if isinstance(value, Mapping):
                    return dict(value)
            return {"value": item}

        def _block(
            kind: str,
            items: list[Any],
            *,
            authority: str,
            status: Optional[str] = None,
            block_diagnostics: Optional[list[dict[str, Any]]] = None,
        ) -> dict[str, Any]:
            normalized_items = [_item_dict(item) for item in items]
            effective_status = status or ("AVAILABLE" if normalized_items else "EMPTY")
            if (
                effective_status == "AVAILABLE"
                and retrieval_plan.scope.scope_status != "RESOLVED"
            ):
                effective_status = "INCOMPLETE"
            provenance = (
                [
                    {
                        "authority": authority,
                        "scope": scope_payload,
                        "status": effective_status,
                    }
                ]
                if normalized_items
                and effective_status not in {"EMPTY", "UNSUPPORTED"}
                else []
            )
            return EvidenceBlock(
                kind=kind,
                items=normalized_items,
                status=effective_status,
                scope=scope_payload,
                provenance=provenance,
                diagnostics=list(block_diagnostics or []),
            ).model_dump(mode="json")

        relation_items = [
            relation
            for entity in entities_data
            for relation in entity.get("relations", [])
        ]
        ownership_items = [
            ownership
            for entity in entities_data
            for ownership in entity.get("ownership_events", [])
        ]
        skill_items = [
            skill
            for entity in entities_data
            for skill in entity.get("skills", [])
        ]
        state_items = [
            *state_evidence.get("balances", []),
            *state_evidence.get("events", []),
        ]
        legacy_scene_items = [
            scene
            for scene in scenes_data
            if scene.get("content_scope") == "unversioned_fts_hint"
        ]
        verified_scene_items = [
            scene
            for scene in scenes_data
            if scene not in legacy_scene_items
        ]

        def _event_block_status(items: list[dict[str, Any]]) -> Optional[str]:
            if not items:
                return None
            provenance_statuses = {
                item.get("provenance_status") for item in items
            }
            if "INCOMPLETE" in provenance_statuses or not timeline_id:
                return "INCOMPLETE"
            if provenance_statuses and provenance_statuses <= {"LEGACY_HINT"}:
                return "LEGACY_HINT"
            if "LEGACY_HINT" in provenance_statuses:
                return "INCOMPLETE"
            return None

        evidence_blocks: list[dict[str, Any]] = []
        for capability in retrieval_plan.capabilities:
            if capability == "entity_profile":
                evidence_blocks.append(
                    _block(
                        capability,
                        entities_data,
                        authority="domain.entities",
                        status=(
                            "INCOMPLETE"
                            if entities_data and versioned_source_requested
                            else None
                        ),
                    )
                )
            elif capability == "relationship":
                evidence_blocks.append(
                    _block(
                        capability,
                        relation_items,
                        authority="domain.entity_relations",
                        status=(
                            "INCOMPLETE"
                            if relation_items and versioned_source_requested
                            else None
                        ),
                    )
                )
            elif capability == "ownership":
                evidence_blocks.append(
                    _block(
                        capability,
                        ownership_items,
                        authority="domain.item_ownership_events",
                        status=(
                            "INCOMPLETE"
                            if ownership_items and versioned_source_requested
                            else None
                        ),
                    )
                )
            elif capability == "skill":
                evidence_blocks.append(
                    _block(
                        capability,
                        skill_items,
                        authority="domain.character_skills",
                        status=(
                            "INCOMPLETE"
                            if skill_items and versioned_source_requested
                            else None
                        ),
                    )
                )
            elif capability == "timeline":
                timeline_status = _event_block_status(events_data)
                timeline_diagnostics = (
                    [
                        self._diagnostic(
                            "TIMELINE_SCOPE_UNSPECIFIED",
                            "时间线事件未绑定显式 timeline_id",
                        )
                    ]
                    if timeline_status == "INCOMPLETE" and not timeline_id
                    else None
                )
                if timeline_status == "LEGACY_HINT":
                    timeline_diagnostics = [
                        self._diagnostic(
                            "LEGACY_CAUSAL_EVENT_HINT_ONLY",
                            "旧因果事件缺少来源版本证据，只能作为提示",
                        )
                    ]
                evidence_blocks.append(
                    _block(
                        capability,
                        events_data,
                        authority="timeline.causal_events",
                        status=timeline_status,
                        block_diagnostics=timeline_diagnostics,
                    )
                )
            elif capability == "causal_reason":
                causal_status = _event_block_status(cause_events)
                causal_diagnostics = (
                    [
                        self._diagnostic(
                            "TIMELINE_SCOPE_UNSPECIFIED",
                            "因果前置事件未绑定显式 timeline_id",
                        )
                    ]
                    if causal_status == "INCOMPLETE" and not timeline_id
                    else None
                )
                if causal_status == "LEGACY_HINT":
                    causal_diagnostics = [
                        self._diagnostic(
                            "LEGACY_CAUSAL_EVENT_HINT_ONLY",
                            "旧因果事件缺少来源版本证据，只能作为提示",
                        )
                    ]
                evidence_blocks.append(
                    _block(
                        capability,
                        cause_events,
                        authority="timeline.causal_links",
                        status=causal_status,
                        block_diagnostics=causal_diagnostics,
                    )
                )
            elif capability == "state":
                state_status = (
                    "INCOMPLETE"
                    if state_items and not state_evidence.get("available")
                    else None
                )
                evidence_blocks.append(
                    _block(
                        capability,
                        state_items,
                        authority="state_ledger",
                        status=state_status,
                    )
                )
            elif capability == "general_search":
                scene_status = (
                    "LEGACY_HINT"
                    if scenes_data and not verified_scene_items and legacy_scene_items
                    else None
                )
                evidence_blocks.append(
                    _block(
                        capability,
                        scenes_data,
                        authority="index_retrieval.fts_scenes",
                        status=scene_status,
                        block_diagnostics=(
                            [
                                self._diagnostic(
                                    "LEGACY_FTS_HINT_ONLY",
                                    "未绑定来源版本的 FTS 片段只能作为提示",
                                )
                            ]
                            if scene_status == "LEGACY_HINT"
                            else None
                        ),
                    )
                )
            else:
                evidence_blocks.append(
                    _block(
                        capability,
                        [],
                        authority="retrieval_plan",
                        status="UNSUPPORTED",
                        block_diagnostics=[
                            self._diagnostic(
                                "UNSUPPORTED_QUERY_CAPABILITY",
                                "检索能力没有对应的证据处理器",
                                details={"capability": capability},
                            )
                        ],
                    )
                )

        def _append_unplanned_block(
            kind: str,
            items: list[Any],
            *,
            authority: str,
            status: Optional[str] = None,
            block_diagnostics: Optional[list[dict[str, Any]]] = None,
        ) -> None:
            if any(block["kind"] == kind for block in evidence_blocks):
                return
            evidence_blocks.append(
                _block(
                    kind,
                    items,
                    authority=authority,
                    status=status,
                    block_diagnostics=block_diagnostics,
                )
            )

        timeline_status = _event_block_status(events_data)
        _append_unplanned_block(
            "timeline",
            events_data,
            authority="timeline.causal_events",
            status=timeline_status,
            block_diagnostics=(
                [
                    self._diagnostic(
                        "LEGACY_CAUSAL_EVENT_HINT_ONLY",
                        "旧因果事件缺少来源版本证据，只能作为提示",
                    )
                ]
                if timeline_status == "LEGACY_HINT"
                else None
            ),
        )

        _append_unplanned_block(
            "claims",
            claims_data,
            authority="claims.lifecycle",
        )
        _append_unplanned_block(
            "mutations",
            [mutation.to_dict() for mutation in mutations],
            authority="domain.mutation_ledger",
        )
        continuity_items = []
        if continuity_evidence.get("chapter"):
            continuity_items.append(continuity_evidence["chapter"])
        _append_unplanned_block(
            "continuity",
            continuity_items,
            authority="timeline.continuity",
        )

        statuses = [block["status"] for block in evidence_blocks]
        if "INCOMPLETE" in statuses:
            result_status = "INCOMPLETE"
        elif "AVAILABLE" in statuses:
            result_status = "AVAILABLE"
        elif "LEGACY_HINT" in statuses:
            result_status = "LEGACY_HINT"
        elif "UNSUPPORTED" in statuses:
            result_status = "UNSUPPORTED"
        else:
            result_status = "EMPTY"
        has_evidence = result_status != "EMPTY"
        if not has_evidence:
            diagnostics.append(
                self._diagnostic(
                    "NO_EVIDENCE_FOUND",
                    "当前作品和检索范围内没有可验证证据",
                    details={
                        "work_id": work_id,
                        "source_id": source_id,
                        "source_version": source_version,
                    },
                )
            )

        return {
            "entities": entities_data,
            "events": events_data,
            "scenes": scenes_data,
            "claims": claims_data,
            "state_ledger": state_evidence,
            "states": state_evidence["balances"],
            "mutations": [mutation.to_dict() for mutation in mutations],
            "active_mutations": [mutation.to_dict() for mutation in mutations],
            "continuity": continuity_evidence,
            "evidence_blocks": evidence_blocks,
            "pod": {
                "divergence_narrative_order": divergence_narrative_order,
                "events": events_data,
                "filtered_event_ids": sorted(pod_event_ids - visible_pod_event_ids),
            },
            "scope": {
                "work_id": work_id,
                "knowledge_version": knowledge_version,
                "narrative_order": narrative_order,
                "timeline_id": timeline_id,
                "source_id": source_id,
                "source_version": source_version,
            },
            "retrieval_plan": retrieval_plan.model_dump(mode="json"),
            "result_status": result_status,
            "diagnostics": diagnostics,
        }


    def synthesize_answer(
        self,
        work_id: str,
        question: str,
        evidence: dict[str, Any],
        use_mock: bool = False
    ) -> str:
        """调用大模型或规则引擎聚合生成权威带证据链的回答"""
        evidence_block_status = {
            block.get("kind"): block.get("status")
            for block in evidence.get("evidence_blocks", [])
            if isinstance(block, Mapping)
        }

        # 格式化实体档案
        entities_lines = []
        entity_items = evidence.get("entities", [])
        if evidence_block_status.get("entity_profile") in {"INCOMPLETE", "LEGACY_HINT", "UNSUPPORTED"}:
            entity_items = []
        for e in entity_items:
            phases_summary = "; ".join([
                f"{p['phase_name']}(第{p['valid_from_order']}~{p['valid_to_order'] or '今'}章)"
                for p in e.get("phases", [])
            ])
            skills_summary = ", ".join([
                f"{s['skill_name']}(Tier {s['tier']}: {s.get('skill_type','')})"
                for s in e.get("skills", [])
            ])
            ownership_summary = "; ".join([
                f"第{o['narrative_order']}章 {o['transfer_type']} {o['item_ref_id']}({o.get('reason','')})"
                for o in e.get("ownership_events", [])
            ])
            relations_summary = "; ".join([
                f"与 {r['target_id'] if r['source_id'] == e['entity_id'] else r['source_id']} ({r['relation_type']})"
                for r in e.get("relations", [])
            ])


            entities_lines.append(
                f"- 实体: {e['name']} ({e['entity_id']})\n"
                f"  属性: {e.get('attributes', {})}\n"
                f"  阶段演变: {phases_summary or '单阶段'}\n"
                f"  已知能力/技能: {skills_summary or '暂无'}\n"
                f"  物品持有记录: {ownership_summary or '暂无'}\n"
                f"  人际关系: {relations_summary or '暂无'}\n"
                f"  档案背景: {e.get('bio', '无')}"
            )
        entities_context = "\n".join(entities_lines) or "（未匹配到特定实体档案）"

        # 格式化因果图
        events_lines = []
        for ev in evidence.get("events", []):
            if ev.get("provenance_status") in {"INCOMPLETE", "LEGACY_HINT"}:
                continue
            events_lines.append(f"- [序位第{ev['narrative_order']}章][{ev['event_id']}]: {ev['summary']}")
        events_context = "\n".join(events_lines) or "（未检索到直接因果事件）"

        # 格式化场景片段
        source_scope = evidence.get("scope") or {}
        versioned_source_requested = bool(
            source_scope.get("source_id") and source_scope.get("source_version")
        )
        scenes_lines = []
        for sc in evidence.get("scenes", []):
            if versioned_source_requested and sc.get("content_source") != "evidence_store":
                continue
            if sc.get("content_scope") == "unversioned_fts_hint":
                continue
            text = sc.get("content") or sc.get("snippet", "")
            scenes_lines.append(f"- [场景 {sc['scene_uuid']} (第{sc.get('chapter_index', '?')}章)]: {text}")
        scenes_context = "\n".join(scenes_lines) or "（未匹配到原著场景高亮片段）"

        claims_lines = [
            f"- [{claim.get('claim_id')}] {claim.get('statement', '')}"
            for claim in evidence.get("claims", [])
            if evidence_block_status.get("claims") not in {"INCOMPLETE", "LEGACY_HINT", "UNSUPPORTED"}
        ]
        claims_context = "\n".join(claims_lines) or "（未检索到已接纳主张）"

        state_evidence = evidence.get("state_ledger") or {}
        state_lines = [
            f"- {item.get('entity_id')} / {item.get('metric_id')}: "
            f"{item.get('computed_value')} [{item.get('status')}] "
            f"(叙事序位 {item.get('narrative_order')})"
            for item in state_evidence.get("balances", [])
        ]
        if not state_lines:
            state_lines = [
                f"- 事件 {item.get('event_id')}: {item.get('reason', '')} "
                f"(变动 {item.get('delta')}, 叙事序位 {item.get('narrative_order')})"
                for item in state_evidence.get("events", [])
            ]
        state_context = "\n".join(state_lines) or "（状态账本暂无可用事实）"

        mutation_lines = [
            f"- [{mutation.get('mutation_id')}] {mutation.get('entity_id')}: "
            f"{mutation.get('mutation_type')} -> {mutation.get('target_name')} "
            f"(触发章 {mutation.get('trigger_chapter')})"
            for mutation in evidence.get("mutations", [])
            if evidence_block_status.get("mutations") not in {"INCOMPLETE", "LEGACY_HINT", "UNSUPPORTED"}
        ]
        pod = evidence.get("pod") or {}
        filtered_ids = pod.get("filtered_event_ids", [])
        if filtered_ids:
            mutation_lines.append(f"- POD 已过滤事件: {', '.join(filtered_ids)}")
        mutation_context = "\n".join(mutation_lines) or "（暂无生效变动或 POD 过滤记录）"

        continuity_record = (evidence.get("continuity") or {}).get("chapter")
        if continuity_record:
            continuity_context = (
                f"- 第{continuity_record.get('chapter_index')}章 "
                f"{continuity_record.get('title', '')}: "
                f"地点={continuity_record.get('ending_location', '')}; "
                f"情势={continuity_record.get('ending_situation', '')}; "
                f"未决钩子={continuity_record.get('unresolved_hooks', [])}"
            )
        else:
            continuity_context = "（连续性台账暂无可用事实）"


        result_status = evidence.get("result_status")
        if result_status == "EMPTY":
            return "无法基于当前检索范围回答：未找到可验证证据。"
        if result_status == "UNSUPPORTED":
            return "无法基于当前检索范围回答：请求的查询能力暂不支持。"
        if result_status == "LEGACY_HINT" and not any(
            block.get("status") == "AVAILABLE"
            for block in evidence.get("evidence_blocks", [])
            if isinstance(block, Mapping)
        ):
            return "无法基于当前检索范围回答：当前仅有未版本化提示，缺少可验证证据。"

        if use_mock:
            ans_parts = [f"针对关于《{work_id}》的问题：“{question}”：\n"]
            if entities_lines:
                ans_parts.append(f"【档案记载】: {entities_lines[0]}")
            if events_lines:
                ans_parts.append(f"【因果节点】: {events_lines[0]}")
            if scenes_lines:
                ans_parts.append(f"【原著场景】: {scenes_lines[0]}")
            if state_evidence.get("available"):
                ans_parts.append(f"【状态账本】: {state_lines[0]}")
            if claims_lines:
                ans_parts.append(f"【已接纳主张】: {claims_lines[0]}")
            if evidence.get("mutations") and mutation_lines:
                ans_parts.append(f"【同人变动】: {mutation_lines[0]}")
            if continuity_record:
                ans_parts.append(f"【连续性台账】: {continuity_context}")
            return "\n".join(ans_parts)


        prompt = PROMPT_SYNTHESIZE_ANSWER.format(
            question=question,
            entities_context=entities_context,
            events_context=events_context,
            scenes_context=scenes_context,
            state_context=state_context,
            claims_context=claims_context,
            mutation_context=mutation_context,
            continuity_context=continuity_context,
        )

        try:
            return self.gateway.complete(
                task_type="fast_extraction",
                prompt=prompt,
                use_cache=True,
                use_mock=False
            )
        except Exception as err:
            evidence.setdefault("diagnostics", []).append(
                self._diagnostic(
                    "ANSWER_SYNTHESIS_FAILED",
                    "答案生成服务失败，未生成未经证实的回答",
                    retryable=True,
                    details={"exception_type": type(err).__name__},
                )
            )
            return "无法生成基于当前证据的回答：答案生成服务不可用。"

    def ask(
        self,
        work_id: str,
        question: str,
        top_k_scenes: int = 5,
        use_mock: bool = False,
        *,
        knowledge_version: Optional[str] = None,
        narrative_order: Optional[int] = None,
        source_id: Optional[str] = None,
        source_version: Optional[str] = None,
        timeline_id: Optional[str] = None,
        divergence_narrative_order: Optional[int] = None,
    ) -> AskResult:
        """自然语言智能问答一键门面方法"""
        decomp = self.decompose_query(work_id=work_id, question=question, use_mock=use_mock)
        evidence = self.retrieve_evidence(
            work_id=work_id,
            decomp=decomp,
            top_k_scenes=top_k_scenes,
            knowledge_version=knowledge_version,
            narrative_order=narrative_order,
            source_id=source_id,
            source_version=source_version,
            timeline_id=timeline_id,
            divergence_narrative_order=divergence_narrative_order,
        )
        answer = self.synthesize_answer(work_id=work_id, question=question, evidence=evidence, use_mock=use_mock)

        return AskResult(
            work_id=work_id,
            question=question,
            decomposition=decomp,
            answer=answer,
            evidence=evidence
        )
