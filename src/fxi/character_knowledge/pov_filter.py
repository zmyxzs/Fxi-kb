"""
fxi.character_knowledge.pov_filter - 视点防穿帮过滤器 (POV Filter)
"""

from typing import Any, Set


class POVFilter:
    """观察者视点安全过滤器：物理剥离观察者尚未获知的机密底牌"""

    @staticmethod
    def sanitize_context_for_pov(
        context_bundle: dict[str, Any],
        observer_char_id: str,
        known_claim_ids: Set[str]
    ) -> dict[str, Any]:
        """
        根据观察者身份裁剪上下文：
        凡标记为 secret / classified 且不在 known_claim_ids 中的信息，物理剔除！
        """
        sanitized = dict(context_bundle)

        # 1. 过滤主张与秘密
        if "claims" in sanitized and isinstance(sanitized["claims"], list):
            filtered_claims = []
            for c in sanitized["claims"]:
                cid = c.get("claim_id")
                is_secret = c.get("is_secret", False)
                # 若非机密，或是机密但观察者已知，则保留
                if not is_secret or (cid and cid in known_claim_ids):
                    filtered_claims.append(c)
            sanitized["claims"] = filtered_claims

        # 2. 过滤主角隐藏底牌
        if "hidden_cards" in sanitized and isinstance(sanitized["hidden_cards"], list):
            visible_cards = []
            for card in sanitized["hidden_cards"]:
                cid = card.get("claim_id") or card.get("card_id")
                if cid and cid in known_claim_ids:
                    visible_cards.append(card)
            sanitized["hidden_cards"] = visible_cards

        sanitized["active_pov"] = observer_char_id
        return sanitized
