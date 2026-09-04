"""
fxi.index_retrieval.context_pruner - 场景化上下文剪枝器 (锁定 2500~4000 Tokens 黄金区间)
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.core.types import SceneType
from fxi.domain.entities import EntityManager
from fxi.domain.phases import PhaseManager
from fxi.game_engine.passive_radar import PassiveThreatRadar
from fxi.game_engine.projection import TieredParameterProjector
from fxi.index_retrieval.jieba_fts import ChineseFTS
from fxi.materials_skills.anti_patterns import AntiPatternRepository
from fxi.materials_skills.skill_store import SkillStore
from fxi.territory.macro_tags import MacroTagGenerator


class ContextPruner:
    """场景化上下文组装与剪枝引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.entity_mgr = EntityManager(self.config)
        self.phase_mgr = PhaseManager(self.config)
        self.projector = TieredParameterProjector(self.config)
        self.radar = PassiveThreatRadar(self.config)
        self.macro_tags = MacroTagGenerator(self.config)
        self.fts = ChineseFTS(self.config)
        self.skills = SkillStore(self.config)
        self.anti_patterns = AntiPatternRepository(self.config)

    def assemble_and_prune(
        self,
        work_id: str,
        scene_type: SceneType,
        pov_character_id: str,
        current_narrative_order: int,
        query: Optional[str] = None,
        territory_id: Optional[str] = None,
        skill_slug: Optional[str] = None,
        budget: int = 3500
    ) -> str:
        """
        组装纯净、结构化、无视点穿帮的场景写作上下文，并严格剪枝在 budget 预算内
        """
        sections: list[str] = []

        # 1. 视点与当前性格阶段 (约 400 字符)
        sections.append(f"### 1. 当前视点 (POV: {pov_character_id})")
        phase = self.phase_mgr.get_active_phase(work_id, pov_character_id, current_narrative_order)
        if phase:
            traits_str = ", ".join(phase.get("traits", []))
            sections.append(f"- 性格阶段: 【{phase['phase_name']}】 (特征: {traits_str})")
            if phase.get("tone_examples"):
                sections.append(f"- 语气范例: \"{phase['tone_examples'][0]}\"")

        # 2. 阶梯化微观能力投影 (Active Deck / 快捷栏) (约 300 字符)
        proj = self.projector.project_for_prompt(
            work_id=work_id,
            character_id=pov_character_id,
            scene_type=scene_type,
            current_order=current_narrative_order
        )
        if "active_deck" in proj and proj["active_deck"]:
            deck_strs = [f"{s['name']}({s['status']})" for s in proj["active_deck"]]
            sections.append(f"### 2. 焦点能力面板 (Active Deck)\n- 快捷神通/招式: {', '.join(deck_strs)}")
        if "status_stages" in proj:
            sections.append(f"- 状态阶位: 生命 {proj['status_stages']['health']} | 法力 {proj['status_stages']['energy']}")

        # 3. 宏观据点态势标签 (约 200 字符)
        if territory_id:
            tags = self.macro_tags.generate_semantic_brief(work_id, territory_id)
            if tags:
                sections.append("### 3. 宏观基业态势\n" + "\n".join([f"- {t}" for t in tags]))

        # 4. 被动威胁雷达防吃书警报 (若命中则注入)
        if query:
            threat_alerts = self.radar.scan_scene_threats(work_id, pov_character_id, query)
            if threat_alerts:
                sections.append("### 4. 被动抗性防吃书警报\n" + "\n".join([f"- {a}" for a in threat_alerts]))

        # 5. 写作负向禁写教条与高分规则 (约 300 字符)
        anti_rules = self.anti_patterns.get_negative_constraints(skill_slug=skill_slug, scene_type=scene_type.value)
        if anti_rules:
            top_anti = anti_rules[:3]
            sections.append("### 5. 负面行文禁令 (Strict Negative Constraints)\n" + "\n".join([f"- 严禁: {r}" for r in top_anti]))

        # 6. FTS5 原作相关场景片段检索 (填充余量预算)
        if query:
            hits = self.fts.search(query=query, work_id=work_id, limit=3)
            if hits:
                sections.append("### 6. 原作关联伏笔与参考片段")
                for h in hits:
                    sections.append(f"- [场景 {h['scene_uuid']}] {h['snippet']}")

        full_context = "\n\n".join(sections)

        # 预算截断守卫 (粗估 1 token ~= 1.5 字符中文)
        max_chars = int(budget * 1.5)
        if len(full_context) > max_chars:
            full_context = full_context[:max_chars] + "\n\n...[已达 Token 预算上限，其余冷参数自动剪枝]..."

        return full_context
