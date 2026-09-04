"""
fxi.claims.triage - 主张审核分流引擎
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TriageResult:
    status: str  # auto_accepted | requires_review | rejected
    confidence: float
    reason: str


class TriageEngine:
    """主张审核防疲劳分流器"""

    def classify(self, statement: str, has_conflict: bool, is_canon_quote: bool) -> TriageResult:
        """
        纯事实引用且无冲突项自动标记 auto_accepted；
        涉及重大冲突项进入待审队列
        """
        if has_conflict:
            return TriageResult(
                status="requires_review",
                confidence=0.5,
                reason="与既有主张存在冲突，需作者人工仲裁"
            )

        if is_canon_quote and len(statement) > 5:
            return TriageResult(
                status="auto_accepted",
                confidence=0.98,
                reason="原著原文直接引用且无任何冲突，自动接纳入库"
            )

        return TriageResult(
            status="requires_review",
            confidence=0.7,
            reason="候选推断主张，建议核验证据"
        )
