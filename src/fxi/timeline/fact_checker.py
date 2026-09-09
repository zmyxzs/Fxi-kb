"""
fxi.timeline.fact_checker - 章节事实与时序前情门禁 (ContinuityFactChecker)
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from json_repair import repair_json
import yaml

from fxi.core.config import FxiConfig, load_config
from fxi.model_gateway.gateway import ModelGateway
from fxi.timeline.continuity import ContinuityManager


@dataclass
class ContinuityViolation:
    severity: str  # "error" | "warning" | "incomplete"
    issue_type: str  # configured rule, timeline conflict, or model/configuration failure
    message: str
    line_snippet: Optional[str] = None
    suggestion: Optional[str] = None


class ContinuityFactChecker:
    """章节时序连续性与前情事实门禁"""

    def __init__(
        self,
        config: Optional[FxiConfig] = None,
        gateway: Optional[ModelGateway] = None,
    ):
        self.config = config or load_config()
        self.continuity_mgr = ContinuityManager(self.config)
        self.gateway = gateway or ModelGateway(self.config)

    def _work_rules_path(self, work_id: str) -> Path:
        if not isinstance(work_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", work_id):
            raise ValueError("work_id 必须是安全的非空作品标识符")
        return self.config.projects_dir / work_id / "work.yaml"

    def _continuity_rules(self, work_id: str) -> tuple[Optional[list[Mapping[str, Any]]], Optional[str]]:
        """读取该作品自己的连续性规则；缺失时明确报告不可用。"""
        path = self._work_rules_path(work_id)
        if not path.is_file():
            return None, f"作品 [{work_id}] 未配置 work.yaml 的 continuity_rules"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            return None, f"作品 [{work_id}] 的连续性规则不可读取: {type(exc).__name__}"
        if not isinstance(raw, Mapping):
            return None, f"作品 [{work_id}] 的 work.yaml 必须是对象"
        rules: Any = raw.get("continuity_rules")
        if rules is None and isinstance(raw.get("rules"), Mapping):
            rules = raw["rules"].get("continuity")
        if isinstance(rules, Mapping):
            rules = rules.get("heuristics")
        if rules is None:
            return None, f"作品 [{work_id}] 未配置 continuity_rules"
        if not isinstance(rules, list) or any(not isinstance(rule, Mapping) for rule in rules):
            return None, f"作品 [{work_id}] 的 continuity_rules 必须是对象列表"
        return list(rules), None

    @staticmethod
    def _incomplete(work_id: str, message: str) -> ContinuityViolation:
        return ContinuityViolation(
            severity="incomplete",
            issue_type="configuration_unavailable",
            message=f"INCOMPLETE: {message}",
            suggestion=f"为作品 [{work_id}] 提供 work.yaml 的 continuity_rules 后重试。",
        )

    @staticmethod
    def _parse_model_result(raw: Any) -> dict[str, Any]:
        """严格解析连续性模型结果，缺少 violations 时不得静默通过。"""
        try:
            data = raw if isinstance(raw, dict) else repair_json(raw, return_objects=True)
        except Exception as exc:
            raise RuntimeError(f"连续性模型结果无法解析: {type(exc).__name__}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("violations"), list):
            raise RuntimeError("连续性模型结果缺少 violations 列表")
        if any(not isinstance(item, Mapping) for item in data["violations"]):
            raise RuntimeError("连续性模型 violations 含无效条目")
        return data

    def check_continuity(
        self,
        work_id: str,
        chapter_index: int,
        draft_text: str,
        planned_events: Optional[list[str]] = None,
        use_llm: bool = True,
    ) -> list[ContinuityViolation]:
        """
        检查草稿正文是否与前序章节结局台账和世界观既有事实存在冲突。
        """
        violations: list[ContinuityViolation] = []

        # 1. 规则与启发式硬约束核查 (100% 确定性拦截已知吃书大坑)
        violations.extend(self._heuristic_checks(work_id, chapter_index, draft_text))

        # 2. 读取前序章节（上一章）权威台账
        prev_index = chapter_index - 1
        prev_data = self.continuity_mgr.get_continuity(work_id, prev_index) if prev_index > 0 else None

        # 3. 大模型语义一致性核查
        if use_llm and prev_data:
            llm_violations = self._llm_semantic_check(
                work_id=work_id,
                chapter_index=chapter_index,
                prev_index=prev_index,
                prev_data=prev_data,
                draft_text=draft_text,
                planned_events=planned_events,
            )
            violations.extend(llm_violations)

        return violations

    def _heuristic_checks(self, work_id: str, chapter_index: int, draft_text: str) -> list[ContinuityViolation]:
        """执行作品级配置提供的确定性规则，不注入任何默认作品规则。"""
        violations: list[ContinuityViolation] = []
        rules, error = self._continuity_rules(work_id)
        if error:
            return [self._incomplete(work_id, error)]
        assert rules is not None
        for index, rule in enumerate(rules):
            pattern = rule.get("pattern")
            if pattern is None:
                patterns = rule.get("patterns")
                pattern = "|".join(str(value) for value in patterns) if isinstance(patterns, list) else None
            if not isinstance(pattern, str) or not pattern:
                return [self._incomplete(work_id, f"continuity_rules[{index}] 缺少 pattern")]
            try:
                match = re.search(pattern, draft_text, flags=re.MULTILINE)
            except re.error:
                return [self._incomplete(work_id, f"continuity_rules[{index}] 的 pattern 无效")]
            if not match:
                continue
            start = max(0, match.start() - 20)
            end = min(len(draft_text), match.end() + 20)
            violations.append(ContinuityViolation(
                severity=str(rule.get("severity") or "warning"),
                issue_type=str(rule.get("issue_type") or "configured_continuity_rule"),
                message=str(rule.get("message") or f"命中作品级连续性规则 {index + 1}"),
                line_snippet=draft_text[start:end],
                suggestion=(str(rule["suggestion"]) if rule.get("suggestion") is not None else None),
            ))
        return violations

    def _llm_semantic_check(
        self,
        work_id: str,
        chapter_index: int,
        prev_index: int,
        prev_data: dict[str, Any],
        draft_text: str,
        planned_events: Optional[list[str]] = None,
    ) -> list[ContinuityViolation]:
        """大模型时序与事实一致性语义审查"""
        violations: list[ContinuityViolation] = []

        prompt = f"""你是一个严谨的长篇小说时序与设定连续性审查专家（Continuity Gatekeeper）。
请审查即将发布的第 {chapter_index} 章正文草稿，比对上一章（第 {prev_index} 章）的权威收尾事实，找出任何剧情吃书、时序断裂、地点瞬间传送或设定矛盾。

【上一章（第 {prev_index} 章《{prev_data.get('title', '')}》）收尾权威台账】：
- 结尾物理地点：{prev_data.get('ending_location', '未知')}
- 结尾在场核心人物：{', '.join(prev_data.get('active_characters', []))}
- 结尾事件局面：{prev_data.get('ending_situation', '无')}
- 待承接的未决伏笔：{json.dumps(prev_data.get('unresolved_hooks', []), ensure_ascii=False)}
- 结尾原文切片：
{prev_data.get('tail_snippet', '')}

【第 {chapter_index} 章正文前 2500 字与关键脉络】：
{draft_text[:2500]}

【审查维度】：
1. 起始状态承接：第 {chapter_index} 章开篇的人物位置、心理与物理交互是否自然承接第 {prev_index} 章结局？
2. 既定事实一致性：是否存在对上一章发生事实的遗忘、颠倒或矛盾修改？
3. 设定与世界观冲突：是否存在凭空捏造的人物背景、荒谬的人际关系或时空错乱？

请务必输出严格的合法 JSON（不要包含 markdown 外的任何解释）：
{{
  "is_valid": true,
  "violations": [
    {{
      "severity": "error",
      "issue_type": "timeline_conflict",
      "message": "具体冲突描述",
      "line_snippet": "正文中的矛盾原句",
      "suggestion": "修正建议"
    }}
  ]
}}
如果没有任何冲突，"is_valid" 为 true，"violations" 为空数组 []。
"""
        try:
            raw_res = self.gateway.complete(
                task_type="fast_extraction",
                prompt=prompt,
                temperature=0.1,
                use_cache=False,
            )
            data = self._parse_model_result(raw_res)
            for v in data["violations"]:
                violations.append(ContinuityViolation(
                    severity=str(v.get("severity") or "warning"),
                    issue_type=str(v.get("issue_type") or "timeline_conflict"),
                    message=str(v.get("message") or "时序连续性冲突"),
                    line_snippet=(str(v["line_snippet"]) if v.get("line_snippet") is not None else None),
                    suggestion=(str(v["suggestion"]) if v.get("suggestion") is not None else None),
                ))
        except Exception as exc:
            violations.append(ContinuityViolation(
                severity="incomplete",
                issue_type="model_unavailable",
                message=f"INCOMPLETE: 连续性模型审查失败 ({type(exc).__name__})",
                suggestion="检查模型网关、模型输出 JSON 和配置后重试；本次结果不可视为通过。",
            ))

        return violations
