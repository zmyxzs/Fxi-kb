"""
fxi.character_knowledge.ooc_checker - 智能角色性格与声线防 OOC 审查器 (IntelligentOOCChecker)
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from json_repair import repair_json
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.domain.entities import EntityManager
from fxi.domain.phases import PhaseManager
from fxi.character_knowledge.knowledge_tracker import KnowledgeTracker
from fxi.model_gateway.gateway import ModelGateway


@dataclass
class OOCViolation:
    severity: str  # error | warning | incomplete
    character_id: str
    issue_type: str  # secret_leak | anti_behavior | speech_tone_mismatch | taboo_breach | preachy_robot
    message: str
    line_snippet: Optional[str] = None
    suggestion: Optional[str] = None


class OOCChecker:
    """角色防 OOC、声线契合度与认知违规智能扫描引擎"""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        gateway: Optional[ModelGateway] = None,
    ):
        self.config = config or load_config()
        self.phase_mgr = PhaseManager(self.config)
        self.tracker = KnowledgeTracker(self.config)
        self.entity_mgr = EntityManager(self.config)
        self.gateway = gateway or ModelGateway(self.config)

    def _work_rules_path(self, work_id: str) -> Path:
        if not isinstance(work_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", work_id):
            raise ValueError("work_id 必须是安全的非空作品标识符")
        return self.config.projects_dir / work_id / "work.yaml"

    def _ooc_rules(self, work_id: str) -> tuple[Optional[list[Mapping[str, Any]]], Optional[str]]:
        """读取作品自己的 OOC 规则；规则缺失时不推断任何作品人设。"""
        path = self._work_rules_path(work_id)
        if not path.is_file():
            return None, f"作品 [{work_id}] 未配置 work.yaml 的 ooc_rules"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            return None, f"作品 [{work_id}] 的 OOC 规则不可读取: {type(exc).__name__}"
        if not isinstance(raw, Mapping):
            return None, f"作品 [{work_id}] 的 work.yaml 必须是对象"
        rules: Any = raw.get("ooc_rules")
        if rules is None and isinstance(raw.get("rules"), Mapping):
            rules = raw["rules"].get("ooc")
        if isinstance(rules, Mapping):
            rules = rules.get("heuristics")
        if rules is None:
            return None, f"作品 [{work_id}] 未配置 ooc_rules"
        if not isinstance(rules, list) or any(not isinstance(rule, Mapping) for rule in rules):
            return None, f"作品 [{work_id}] 的 ooc_rules 必须是对象列表"
        return list(rules), None

    @staticmethod
    def _incomplete(work_id: str, message: str) -> OOCViolation:
        return OOCViolation(
            severity="incomplete",
            character_id="",
            issue_type="configuration_unavailable",
            message=f"INCOMPLETE: {message}",
            suggestion=f"为作品 [{work_id}] 提供 work.yaml 的 ooc_rules 后重试。",
        )

    @staticmethod
    def _parse_model_result(raw: Any) -> dict[str, Any]:
        """严格解析声线模型结果，缺少 violations 时不得静默通过。"""
        try:
            data = raw if isinstance(raw, dict) else repair_json(raw, return_objects=True)
        except Exception as exc:
            raise RuntimeError(f"OOC 模型结果无法解析: {type(exc).__name__}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("violations"), list):
            raise RuntimeError("OOC 模型结果缺少 violations 列表")
        if any(not isinstance(item, Mapping) for item in data["violations"]):
            raise RuntimeError("OOC 模型 violations 含无效条目")
        return data

    def scan_draft(
        self,
        draft_text: str,
        work_id: str,
        narrative_order: int = 1,
        speaker_id: Optional[str] = None,
        characters: Optional[list[str]] = None,
        unrevealed_secrets: Optional[dict[str, str]] = None,  # {secret_keyword: claim_id}
        use_llm: bool = True,
    ) -> list[OOCViolation]:
        """
        扫描草稿文本，比对角色性格相态、机密认知、Voice Profile 与禁忌行为
        """
        violations: list[OOCViolation] = []
        unrevealed_secrets = unrevealed_secrets or {}
        rules, rules_error = self._ooc_rules(work_id)

        # 确定要核查的角色列表
        target_chars: list[str] = []
        if speaker_id:
            target_chars.append(speaker_id)
        if characters:
            for c in characters:
                if c not in target_chars:
                    target_chars.append(c)

        # 1. 检查机密早泄 (认知追踪)
        for char_id in target_chars:
            known_claims = self.tracker.get_known_claims(work_id, char_id, narrative_order)
            for keyword, cid in unrevealed_secrets.items():
                if cid not in known_claims and keyword in draft_text:
                    violations.append(OOCViolation(
                        severity="error",
                        character_id=char_id,
                        issue_type="secret_leak",
                        message=f"角色 [{char_id}] 在当前节点尚未获知机密 [{keyword}]，但在对白/正文中提及了该机密！",
                        line_snippet=keyword,
                        suggestion=f"删除该角色关于 [{keyword}] 的知情描述。"
                    ))

            phase = self.phase_mgr.get_active_phase(work_id, char_id, narrative_order)
            if phase:
                for anti_behavior in phase.get("anti_behaviors", []):
                    if anti_behavior and anti_behavior in draft_text:
                        violations.append(OOCViolation(
                            severity="warning",
                            character_id=char_id,
                            issue_type="anti_behavior",
                            message=(
                                f"角色言行违背当前阶段 [{phase.get('phase_name', '')}] "
                                f"禁忌行为: {anti_behavior}"
                            ),
                            line_snippet=anti_behavior,
                        ))

        # 2. 检查性格相态与启发式禁忌违规 (规则层)
        if rules_error:
            violations.append(self._incomplete(work_id, rules_error))
        elif rules is not None:
            for char_id in target_chars:
                v_rules = self._rule_based_ooc_check(
                    work_id, char_id, narrative_order, draft_text, rules
                )
                violations.extend(v_rules)
        # 3. 大模型深度声线口吻与 OOC 审查 (语义层)
        if use_llm and target_chars:
            for char_id in target_chars:
                v_llm = self._llm_voice_check(work_id, char_id, draft_text)
                violations.extend(v_llm)

        return violations

    def _rule_based_ooc_check(
        self,
        work_id: str,
        char_id: str,
        narrative_order: int,
        draft_text: str,
        rules: list[Mapping[str, Any]],
    ) -> list[OOCViolation]:
        """执行作品级配置提供的 OOC 规则，不包含任何固定角色或题材判断。"""
        violations: list[OOCViolation] = []
        for index, rule in enumerate(rules):
            patterns = rule.get("patterns")
            if patterns is None:
                patterns = [rule.get("pattern")] if rule.get("pattern") else []
            if not isinstance(patterns, list) or not patterns:
                return [self._incomplete(work_id, f"ooc_rules[{index}] 缺少 pattern")]
            matched = None
            for pattern in patterns:
                if not isinstance(pattern, str) or not pattern:
                    return [self._incomplete(work_id, f"ooc_rules[{index}] 的 pattern 无效")]
                try:
                    matched = re.search(pattern, draft_text, flags=re.MULTILINE)
                except re.error:
                    return [self._incomplete(work_id, f"ooc_rules[{index}] 的 pattern 无效")]
                if matched:
                    break
            if not matched:
                continue
            start = max(0, matched.start() - 15)
            end = min(len(draft_text), matched.end() + 15)
            violations.append(OOCViolation(
                severity=str(rule.get("severity") or "warning"),
                character_id=char_id,
                issue_type=str(rule.get("issue_type") or "configured_ooc_rule"),
                message=str(rule.get("message") or f"命中作品级 OOC 规则 {index + 1}"),
                line_snippet=draft_text[start:end],
                suggestion=(str(rule["suggestion"]) if rule.get("suggestion") is not None else None),
            ))
        return violations

    def _llm_voice_check(
        self,
        work_id: str,
        char_id: str,
        draft_text: str
    ) -> list[OOCViolation]:
        """大模型声线与口吻深度审查"""
        vp = self.entity_mgr.get_character_voice(work_id, char_id)
        if not vp:
            return []

        # 检查正文中是否有该角色的名字或提及
        entity = self.entity_mgr.get_entity(work_id, char_id)
        if isinstance(entity, dict):
            char_name = entity.get("name", char_id)
        elif hasattr(entity, "name"):
            char_name = getattr(entity, "name", char_id)
        else:
            char_name = char_id
        if char_name not in draft_text:
            return []

        tone = vp.get("tone", "")
        speech_style = vp.get("speech_style", "")
        catchphrases = json.dumps(vp.get("catchphrases", []), ensure_ascii=False)
        taboos = json.dumps(vp.get("taboos", []), ensure_ascii=False)
        samples = json.dumps(vp.get("dialogue_samples", []), ensure_ascii=False)

        prompt = f"""你是一个苛刻的小说角色性格与声线还原度（Anti-OOC）审查总监。
请审查以下正文草稿中角色【{char_name}】的言行表现、对白口吻、微动作和心理反应是否发生了严重 OOC（人设崩塌、机械生硬、说教或违背原著特质）。

【角色原生声线与人设 Profile】：
- 核心基调（Tone）：{tone}
- 语言风格（Speech Style）：{speech_style}
- 标志性口吻/口头禅：{catchphrases}
- 绝对禁忌行为（Taboos）：{taboos}
- 原著权威对白切片（Dialogue Samples）：
{samples}

【草稿文本】：
{draft_text[:2500]}

【审查标准】：
1. 语言节奏与句式：是否符合输入的角色 Profile 与已给出的对白样本？不要凭空套用其他作品或角色的性格。
2. 禁忌行为（Taboos）：角色是否做出了其人设绝对不会做出的事情？
3. 工具人化与机械感：角色的台词是否像是在走任务流程，失去了文本中已经给出的情感与行为特征？

请输出严格合法 JSON（绝不要 Markdown 以外的解释）：
{{
  "has_ooc": true/false,
  "ooc_score": 90,
  "violations": [
    {{
      "severity": "error"|"warning",
      "issue_type": "speech_tone_mismatch"|"taboo_breach"|"preachy_robot",
      "message": "具体 OOC 描述",
      "line_snippet": "草稿中违规的具体句子",
      "suggestion": "符合该角色声线的原著化修改建议"
    }}
  ]
}}
若无严重 OOC，"has_ooc" 为 false，"violations" 为 []。
"""
        violations: list[OOCViolation] = []
        try:
            raw_res = self.gateway.complete(
                task_type="deep_reasoning_ooc",
                prompt=prompt,
                temperature=0.1,
                use_cache=False,
            )
            data = self._parse_model_result(raw_res)
            for v in data["violations"]:
                violations.append(OOCViolation(
                    severity=str(v.get("severity") or "warning"),
                    character_id=char_id,
                    issue_type=str(v.get("issue_type") or "speech_tone_mismatch"),
                    message=str(v.get("message") or f"{char_name} 声线不符"),
                    line_snippet=(str(v["line_snippet"]) if v.get("line_snippet") is not None else None),
                    suggestion=(str(v["suggestion"]) if v.get("suggestion") is not None else None),
                ))
        except Exception as exc:
            violations.append(OOCViolation(
                severity="incomplete",
                character_id=char_id,
                issue_type="model_unavailable",
                message=f"INCOMPLETE: 角色声线模型审查失败 ({type(exc).__name__})",
                suggestion="检查模型网关、模型输出 JSON 和配置后重试；本次结果不可视为通过。",
            ))

        return violations
