"""
fxi.materials_skills.style_canonical - 写作规则规范化语义映射与聚类引擎
"""

import re
from typing import Any, Optional


CATEGORY_MAP = {
    "词语搭配": "diction",
    "diction": "diction",
    "句法衔接": "syntax",
    "syntax": "syntax",
    "段落换行": "paragraphing",
    "paragraphing": "paragraphing",
    "对白回应": "dialogue",
    "dialogue": "dialogue",
    "信息揭示": "information",
    "information": "information",
    "详略选择": "detail",
    "detail": "detail",
    "action": "action",
    "动作": "action",
    "pacing": "pacing",
    "节奏": "pacing",
    "emotion": "emotion",
    "情感": "emotion",
}

SCENE_MAP = {
    "all": "ALL",
    "battle": "battle",
    "combat": "battle",
    "战斗": "battle",
    "交锋": "battle",
    "confrontation": "confrontation",
    "对峙": "confrontation",
    "博弈": "confrontation",
    "daily": "daily",
    "日常": "daily",
    "campus_dialogue": "daily",
    "slice_of_life": "daily",
    "suspense": "suspense",
    "悬疑": "suspense",
    "危机": "suspense",
    "emotion": "emotion",
    "family_warmth": "emotion",
    "情感": "emotion",
    "dialogue": "confrontation",
}

CANONICAL_PRESETS = [
    {
        "canonical_key": "action_before_explanation",
        "match_keywords": [
            "action_before", "action_precedes", "action_prior", "detail_action_before",
            "action_then_emotion", "action_reveals_emotion", "action_emotion", "action_then_inner",
            "action_reveals", "action_to_state", "action_carries", "action_then"
        ],
        "category": "action",
        "scene_scope": "confrontation",
        "default_instruction": "在表达强烈心境或揭示情绪前，先描写具象肢体微动作或生理体征，避免抽象名词直接解释。",
        "default_anti_pattern": "严禁直接使用“他感到很愤怒”、“心头涌起悲伤”等抽象情绪说明词。",
    },
    {
        "canonical_key": "action_reaction_chain",
        "match_keywords": [
            "action_reaction", "action_to_reaction", "action_consequence", "consequence_amplification",
            "action_stack", "reaction_chain", "response_escalation"
        ],
        "category": "action",
        "scene_scope": "confrontation",
        "default_instruction": "连环动作推进中紧随对方即时生理反弹与受创反馈，一攻一守动态呼应。",
        "default_anti_pattern": "严禁单方面输出动作，缺乏被击中者即时反馈。",
    },
    {
        "canonical_key": "dialogue_compression_in_tension",
        "match_keywords": [
            "short_dialogue", "compressed_dialogue", "brief_speech", "dialogue_compression",
            "staccato_dialogue", "dialogue_tempo", "speech_rhythm", "dialogue_break"
        ],
        "category": "dialogue",
        "scene_scope": "confrontation",
        "default_instruction": "高危交锋或对峙状态下，单句对白限制在12字以内，以反问、逼问或单字短句推进。",
        "default_anti_pattern": "严禁在高危交锋时发表长篇大论演讲或长篇解释动机。",
    },
    {
        "canonical_key": "dialogue_subversion_and_repartee",
        "match_keywords": [
            "misread", "correction_after", "taunt", "mock", "irony", "retort", "repartee",
            "banter", "subversion", "disdain", "verbal_counter"
        ],
        "category": "dialogue",
        "scene_scope": "confrontation",
        "default_instruction": "对话交锋中先顺承对方言语误判，再于下一回合施加冷冽反诘或事实揭露。",
        "default_anti_pattern": "严禁叙事主体急于辩解澄清，削弱反转戏剧张力。",
    },
    {
        "canonical_key": "rapid_combat_action_burst",
        "match_keywords": [
            "combat", "battle", "impact_burst", "rapid_action", "combat_tempo", "combat_verbs",
            "staccato_physical", "strike", "attack", "collision", "clash"
        ],
        "category": "action",
        "scene_scope": "battle",
        "default_instruction": "激烈交锋中以3~7字连续动词短句爆发推进，省略冗余助词与心理杂念。",
        "default_anti_pattern": "严禁在出招瞬间插入大段背景回忆、战力解说或旁白长句。",
    },
    {
        "canonical_key": "sensory_prop_anchoring",
        "match_keywords": [
            "prop_anchoring", "object_tactile", "grounding_prop", "prop_interaction",
            "sensory_prop", "tactile", "anchor_detail", "object_detail", "material_interaction"
        ],
        "category": "detail",
        "scene_scope": "ALL",
        "default_instruction": "人物交流或对峙时，借由手头具象物证（如茶盏微晃、针尖反光、刀柄冷热）固定注意力与现场压强。",
        "default_anti_pattern": "严禁人物处于虚空背景中无实物交互地干聊。",
    },
    {
        "canonical_key": "cliffhanger_suspension",
        "match_keywords": [
            "cliffhanger", "crisis_cutoff", "revelation_suspended", "cutoff_on_climax",
            "cliffhanger_cut", "suspension", "cutoff"
        ],
        "category": "pacing",
        "scene_scope": "suspense",
        "default_instruction": "在重大信息揭晓前一瞬、或异变骤起动作中途强行截断场景定格，留下强烈悬念。",
        "default_anti_pattern": "严禁在高潮异变发生后继续平铺直叙交代善后细节。",
    },
    {
        "canonical_key": "staged_information_reveal",
        "match_keywords": [
            "gradual_revelation", "information_reveal", "staged_reveal", "layered_information",
            "reveal_sequence", "clue", "anomaly", "exposition", "unveil"
        ],
        "category": "information",
        "scene_scope": "confrontation",
        "default_instruction": "信息揭示遵循“异象显现 -> 周遭反应 -> 叙事主体落定”的三阶递进，不一次性倒空事实。",
        "default_anti_pattern": "严禁借由旁白一次性平铺直叙宣布所有底牌。",
    },
    {
        "canonical_key": "sensory_suppression_silence",
        "match_keywords": [
            "silence_after_shock", "suppression_silence", "quiet_after_impact", "sudden_silence",
            "dead_silence", "pause_for_tension", "stillness"
        ],
        "category": "pacing",
        "scene_scope": "confrontation",
        "default_instruction": "惊天逆转或暴击落地后，插入一至两个回合的全场绝对死寂与停滞微动作，蓄积反差张力。",
        "default_anti_pattern": "严禁在重大震撼发生后路人立刻七嘴八舌密集喧哗。",
    },
    {
        "canonical_key": "spectator_awe_escalation",
        "match_keywords": [
            "awe", "shock", "crowd", "spectator", "bystander", "astonish", "terror",
            "atmosphere_detail", "audience_reaction", "group_reaction"
        ],
        "category": "detail",
        "scene_scope": "confrontation",
        "default_instruction": "重大战果或底牌出鞘时，通过旁观者表情僵滞与倒吸冷气的具象体征放大压迫感。",
        "default_anti_pattern": "严禁旁观者千人一面地喊口号叫好。",
    },
    {
        "canonical_key": "emotional_resonance_restraint",
        "match_keywords": [
            "warmth", "family", "memory", "restrained_emotion", "understated_sorrow",
            "heartfelt", "tenderness", "silent_care"
        ],
        "category": "emotion",
        "scene_scope": "emotion",
        "default_instruction": "至深至切之情隐于柴米油盐、衣履微凉等极平实微物，不直吐肉麻字眼。",
        "default_anti_pattern": "严禁琼瑶式抱头痛哭呼天抢地。",
    },
]


class CanonicalRuleMapper:
    """技法规则规范化分类与语义聚类映射器"""

    @classmethod
    def normalize_category(cls, raw_category: str) -> str:
        cleaned = raw_category.strip().lower()
        return CATEGORY_MAP.get(cleaned, CATEGORY_MAP.get(raw_category.strip(), "syntax"))

    @classmethod
    def normalize_scene_scope(cls, raw_scene: Optional[str]) -> str:
        if not raw_scene:
            return "ALL"
        cleaned = raw_scene.strip().lower()
        if cleaned in {"*", "unknown", "unk", "undefined", "any"}:
            raise ValueError(f"unsupported scene scope: {raw_scene!r}")
        normalized = SCENE_MAP.get(cleaned)
        if normalized is None:
            raise ValueError(f"unsupported scene scope: {raw_scene!r}")
        return normalized

    @classmethod
    def clean_rule_key(cls, raw_key: str) -> str:
        """格式化 rule_key 为合法的标准小写蛇形标识"""
        key = raw_key.strip().lower()
        key = re.sub(r"[^\w\s-]", "", key)
        key = re.sub(r"[-\s]+", "_", key)
        return key.strip("_") or "general_craft_rule"

    @classmethod
    def map_to_canonical(
        cls,
        raw_key: str,
        category: str,
        instruction: str,
        anti_pattern: str = "",
        scene_scope: Optional[str] = None,
        condition: Any = None,
        conditions: Any = None,
        operation: Optional[str] = None,
        effect_hypothesis: Optional[str] = None,
        cost: Any = None,
        conflicts: Optional[list[str]] = None,
        supports: Optional[list[str]] = None,
        source_refs: Optional[list[Any]] = None,
        evidence_refs: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        """
        将任意模型生成的原始规则归一化映射到规范体系
        """
        norm_key = cls.clean_rule_key(raw_key)
        norm_cat = cls.normalize_category(category)
        norm_scene = cls.normalize_scene_scope(scene_scope)

        condition_value = conditions if conditions is not None else condition

        def with_method_metadata(result: dict[str, Any]) -> dict[str, Any]:
            if condition_value is not None:
                result["condition"] = condition_value
            if operation is not None:
                result["operation"] = operation
            if effect_hypothesis is not None:
                result["effect_hypothesis"] = effect_hypothesis
            if cost is not None:
                result["cost"] = cost
            if conflicts is not None:
                result["conflicts"] = list(conflicts)
            if supports is not None:
                result["supports"] = list(supports)
            refs = evidence_refs if evidence_refs is not None else source_refs
            if refs is not None:
                result["evidence_refs"] = list(refs)
            return result

        # 1. 优先在预设白金技法库中匹配关键词
        for preset in CANONICAL_PRESETS:
            for kw in preset["match_keywords"]:
                if kw in norm_key:
                    return with_method_metadata({
                        "canonical_key": preset["canonical_key"],
                        "category": preset["category"],
                        "scene_scope": preset["scene_scope"] if norm_scene == "ALL" else norm_scene,
                        "instruction": instruction or preset["default_instruction"],
                        "anti_pattern": anti_pattern or preset["default_anti_pattern"],
                    })

        # 2. 按核心领域词根聚类收敛
        root_mappings = [
            ("action", "action_dynamic_flow", "action", "confrontation", "以连续微动作带出人物状态与局势流转。"),
            ("dialogue", "dialogue_cadence_exchange", "dialogue", "confrontation", "对白按回合攻防推进，穿插人物微表情。"),
            ("speech", "dialogue_cadence_exchange", "dialogue", "confrontation", "对白按回合攻防推进，穿插人物微表情。"),
            ("combat", "rapid_combat_action_burst", "action", "battle", "交锋以短句爆发推进，省略冗余修饰。"),
            ("detail", "sensory_detail_anchoring", "detail", "ALL", "借由具象环境与物证细节稳固场景真实感。"),
            ("prop", "sensory_prop_anchoring", "detail", "ALL", "以手头物证交互承载人物注意力与心理张力。"),
            ("pacing", "narrative_cadence_tempo", "pacing", "ALL", "长短句交错，高压短促、铺垫舒缓。"),
            ("tempo", "narrative_cadence_tempo", "pacing", "ALL", "长短句交错，高压短促、铺垫舒缓。"),
            ("reveal", "staged_information_reveal", "information", "confrontation", "层级揭示信息，保持读者期待感。"),
            ("emotion", "emotional_resonance_restraint", "emotion", "emotion", "情感克制留白，借物传情。"),
        ]
        for term, c_key, c_cat, c_scene, default_inst in root_mappings:
            if term in norm_key:
                return with_method_metadata({
                    "canonical_key": c_key,
                    "category": c_cat,
                    "scene_scope": c_scene if norm_scene == "ALL" else norm_scene,
                    "instruction": instruction or default_inst,
                    "anti_pattern": anti_pattern,
                })

        # 3. 若未中预设与词根，以语义前缀剥离后收拢
        simplified_key = norm_key
        prefixes_to_strip = ["detail_", "strong_", "brief_", "subtle_", "explicit_", "implicit_"]
        for p in prefixes_to_strip:
            if simplified_key.startswith(p):
                simplified_key = simplified_key[len(p):]

        return with_method_metadata({
            "canonical_key": simplified_key,
            "category": norm_cat,
            "scene_scope": norm_scene,
            "instruction": instruction.strip(),
            "anti_pattern": anti_pattern.strip() if anti_pattern else "",
        })

    @classmethod
    def is_executable_rule(cls, instruction: str) -> bool:
        """
        可执行性语法门禁 (Executable Linter):
        过滤掉“注意烘托气氛”、“加强人物描写”等空洞废话
        """
        text = instruction.strip()
        if len(text) < 8:
            return False

        generic_buzzwords = [
            "注意烘托气氛", "加强描写", "注重人物刻画", "注意语言生动", "写得精彩一些",
            "增强表现力", "注重情感表达", "提高行文水平"
        ]
        if any(bw in text for bw in generic_buzzwords) and len(text) < 25:
            return False

        return True
