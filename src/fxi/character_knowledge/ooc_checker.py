"""
fxi.character_knowledge.ooc_checker - 逻辑与角色性格防 OOC 审查器
"""

from dataclasses import dataclass
from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.domain.phases import PhaseManager
from fxi.character_knowledge.knowledge_tracker import KnowledgeTracker


@dataclass
class OOCViolation:
    severity: str  # error | warning
    character_id: str
    issue_type: str  # secret_leak | anti_behavior | phase_mismatch
    message: str
    line_snippet: Optional[str] = None


class OOCChecker:
    """角色防 OOC 与认知违规扫描引擎"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.phase_mgr = PhaseManager(self.config)
        self.tracker = KnowledgeTracker(self.config)

    def scan_draft(
        self,
        draft_text: str,
        work_id: str,
        narrative_order: int,
        speaker_id: Optional[str] = None,
        unrevealed_secrets: Optional[dict[str, str]] = None  # {secret_keyword: claim_id}
    ) -> list[OOCViolation]:
        """
        扫描草稿文本，比对角色性格相态与机密认知
        """
        violations: list[OOCViolation] = []
        unrevealed_secrets = unrevealed_secrets or {}

        # 1. 检查秘密早泄 (如角色说出了还不知道的凶手或真相)
        if speaker_id:
            known_claims = self.tracker.get_known_claims(work_id, speaker_id, narrative_order)
            for keyword, cid in unrevealed_secrets.items():
                if cid not in known_claims and keyword in draft_text:
                    violations.append(OOCViolation(
                        severity="error",
                        character_id=speaker_id,
                        issue_type="secret_leak",
                        message=f"角色 [{speaker_id}] 在当前节点尚未获知机密 [{keyword}]，但在对白/正文中提及了该机密！",
                        line_snippet=keyword
                    ))

        # 2. 检查性格相态违背 (Anti-behaviors)
        if speaker_id:
            phase = self.phase_mgr.get_active_phase(work_id, speaker_id, narrative_order)
            if phase:
                anti_behaviors = phase.get("anti_behaviors", [])
                for ab in anti_behaviors:
                    if ab in draft_text:
                        violations.append(OOCViolation(
                            severity="warning",
                            character_id=speaker_id,
                            issue_type="anti_behavior",
                            message=f"角色言行违背当前阶段 [{phase['phase_name']}] 禁忌行为: {ab}",
                            line_snippet=ab
                        ))

        return violations
