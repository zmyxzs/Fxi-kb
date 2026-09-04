"""
fxi.index_retrieval.query_engine - 自然语言智能问答与多源证据链检索聚合引擎
"""

import json
import yaml
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

from fxi.core.config import FxiConfig, load_config
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.model_gateway.gateway import ModelGateway
from fxi.storage.sqlite_client import DatabaseClient
from fxi.storage.text_io import read_markdown_frontmatter


class QueryDecomposition(BaseModel):
    """自然语言提问的意图与关键词拆解"""
    target_entities: list[str] = Field(default_factory=list, description="问题中提及或关联的核心实体/角色名")
    keywords: list[str] = Field(default_factory=list, description="用于全文检索的核心关键词（动词、名词、特殊设定）")
    intent: str = Field(default="general", description="查询意图，如 origin, ability, relationship, timeline_event, causal_fate")


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

PROMPT_SYNTHESIZE_ANSWER = """你是一个严谨的小说世界观与剧情知识库分析专家。请根据下方提供的多源检索证据，针对用户提出的问题进行全面、准确、有据可查的回答。
严禁凭空臆测，严禁使用未在证据中出现的设定。回答时请具体注明出处（如涉及的角色档案、因果事件ID、原著章节/场景片段）。

【用户问题】
{question}

【检索证据】
=== 实体档案与时序演变 ===
{entities_context}

=== 因果事件图谱 ===
{events_context}

=== 原著场景片段 (FTS5) ===
{scenes_context}

请直接输出结构化、清晰详实的回答。"""


class QueryEngine:
    """自然语言问答与证据链聚合引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.fts = ChineseFTS(self.config)
        self.gateway = ModelGateway(self.config)
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def decompose_query(self, work_id: str, question: str, use_mock: bool = False) -> QueryDecomposition:
        """解析并拆解自然语言提问"""
        if use_mock:
            return self._heuristic_decompose(work_id, question)

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
                return decomp
        except Exception:
            pass

        return self._heuristic_decompose(work_id, question)

    def _heuristic_decompose(self, work_id: str, question: str) -> QueryDecomposition:
        """基于已知实体名与 jieba 分词的确定性保底拆解"""
        with self.db_client.get_connection() as conn:
            cur = conn.execute(
                "SELECT entity_id, name, aliases_json FROM entities WHERE work_id = ?",
                (work_id,)
            )
            known_entities = cur.fetchall()

        matched_entities: list[str] = []
        for row in known_entities:
            name = row["name"]
            if name and name in question:
                matched_entities.append(name)
            aliases = json.loads(row["aliases_json"] or "[]") if row["aliases_json"] else []
            for alias in aliases:
                if alias and alias in question and name not in matched_entities:
                    matched_entities.append(name)

        import jieba
        words = [w for w in jieba.cut_for_search(question) if len(w.strip()) > 1]
        stopwords = {"怎么", "什么", "为什么", "怎样", "哪个", "哪里", "是谁", "如何", "这个", "那个"}
        keywords = [w for w in words if w not in stopwords and w not in matched_entities]

        return QueryDecomposition(
            target_entities=matched_entities or ["主角"],
            keywords=keywords[:4] if keywords else [question[:4]],
            intent="general"
        )

    def retrieve_evidence(
        self,
        work_id: str,
        decomp: QueryDecomposition,
        top_k_scenes: int = 5
    ) -> dict[str, Any]:
        """多源并行检索：实体画像卡 + 因果 DAG + FTS5 场景"""
        entities_data: list[dict[str, Any]] = []
        events_data: list[dict[str, Any]] = []

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
                    if doc_path.is_file():
                        try:
                            _, body = read_markdown_frontmatter(doc_path)
                            body_text = body.strip()
                        except Exception:
                            pass

                    # 查询性格演变相态
                    cur_p = conn.execute(
                        "SELECT phase_id, phase_name, valid_from_order, valid_to_order, traits_json FROM entity_phases WHERE work_id = ? AND entity_id = ? ORDER BY valid_from_order",
                        (work_id, ent_id)
                    )
                    phases = cur_p.fetchall()

                    entities_data.append({
                        "entity_id": ent_id,
                        "name": row["name"],
                        "category": row["category"],
                        "attributes": yaml.safe_load(row["attributes_yaml"] or "{}"),
                        "bio": body_text,
                        "phases": [
                            {
                                "phase_id": p["phase_id"],
                                "phase_name": p["phase_name"],
                                "valid_from_order": p["valid_from_order"],
                                "valid_to_order": p["valid_to_order"],
                                "traits": json.loads(p["traits_json"] or "[]")
                            }
                            for p in phases
                        ]
                    })

            # 2. 因果图谱事件检索
            event_query_terms = list(set(decomp.target_entities + decomp.keywords))
            if event_query_terms:
                conditions = " OR ".join(["summary LIKE ?" for _ in event_query_terms])
                params = [f"%{term}%" for term in event_query_terms]
                cur_e = conn.execute(
                    f"SELECT event_id, narrative_order, summary, status FROM causal_events WHERE work_id = ? AND ({conditions}) ORDER BY narrative_order LIMIT 10",
                    (work_id, *params)
                )
                events_data = [dict(ev) for ev in cur_e.fetchall()]

        # 3. FTS5 原著场景切片检索
        search_terms = " ".join(decomp.keywords or decomp.target_entities)
        scenes_data = self.fts.search(query=search_terms, work_id=work_id, limit=top_k_scenes)

        return {
            "entities": entities_data,
            "events": events_data,
            "scenes": scenes_data
        }

    def synthesize_answer(
        self,
        work_id: str,
        question: str,
        evidence: dict[str, Any],
        use_mock: bool = False
    ) -> str:
        """调用大模型或规则引擎聚合生成权威带证据链的回答"""
        # 格式化实体档案
        entities_lines = []
        for e in evidence.get("entities", []):
            phases_summary = "; ".join([
                f"{p['phase_name']}(第{p['valid_from_order']}~{p['valid_to_order'] or '今'}章)"
                for p in e.get("phases", [])
            ])
            entities_lines.append(
                f"- 实体: {e['name']} ({e['entity_id']})\n"
                f"  属性: {e.get('attributes', {})}\n"
                f"  阶段演变: {phases_summary or '暂无'}\n"
                f"  档案背景: {e.get('bio', '无')}"
            )
        entities_context = "\n".join(entities_lines) or "（未匹配到特定实体档案）"

        # 格式化因果图
        events_lines = []
        for ev in evidence.get("events", []):
            events_lines.append(f"- [序位第{ev['narrative_order']}章][{ev['event_id']}]: {ev['summary']}")
        events_context = "\n".join(events_lines) or "（未检索到直接因果事件）"

        # 格式化场景片段
        scenes_lines = []
        for sc in evidence.get("scenes", []):
            scenes_lines.append(f"- [场景 {sc['scene_uuid']}]: {sc['snippet']}")
        scenes_context = "\n".join(scenes_lines) or "（未匹配到原著场景高亮片段）"

        if use_mock:
            ans_parts = [f"针对关于《{work_id}》的问题：“{question}”：\n"]
            if evidence.get("entities"):
                ans_parts.append(f"【档案记载】: {entities_lines[0]}")
            if evidence.get("events"):
                ans_parts.append(f"【因果节点】: {events_lines[0]}")
            if evidence.get("scenes"):
                ans_parts.append(f"【原著场景】: {scenes_lines[0]}")
            return "\n".join(ans_parts)

        prompt = PROMPT_SYNTHESIZE_ANSWER.format(
            question=question,
            entities_context=entities_context,
            events_context=events_context,
            scenes_context=scenes_context
        )

        try:
            return self.gateway.complete(
                task_type="fast_extraction",
                prompt=prompt,
                use_cache=True,
                use_mock=False
            )
        except Exception as err:
            return f"生成答案时发生异常: {err}\n\n已检索到的证据:\n{entities_context}\n{events_context}"

    def ask(
        self,
        work_id: str,
        question: str,
        top_k_scenes: int = 5,
        use_mock: bool = False
    ) -> AskResult:
        """自然语言智能问答一键门面方法"""
        decomp = self.decompose_query(work_id=work_id, question=question, use_mock=use_mock)
        evidence = self.retrieve_evidence(work_id=work_id, decomp=decomp, top_k_scenes=top_k_scenes)
        answer = self.synthesize_answer(work_id=work_id, question=question, evidence=evidence, use_mock=use_mock)

        return AskResult(
            work_id=work_id,
            question=question,
            decomposition=decomp,
            answer=answer,
            evidence=evidence
        )
