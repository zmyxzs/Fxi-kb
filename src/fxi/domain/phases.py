"""
fxi.domain.phases - 实体性格阶段与时序相态演变管理 (性格黑化、心性蜕变、法宝进阶)
"""

import json
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class PhaseManager:
    """实体性格相态管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def add_phase(
        self,
        work_id: str,
        entity_id: str,
        phase_id: str,
        phase_name: str,
        valid_from_order: int = 0,
        valid_to_order: Optional[int] = None,
        traits: Optional[list[str]] = None,
        anti_behaviors: Optional[list[str]] = None,
        tone_examples: Optional[list[str]] = None,
    ) -> None:
        """注册一个性格相态"""
        traits = traits or []
        anti_behaviors = anti_behaviors or []
        tone_examples = tone_examples or []

        from fxi.storage.sqlite_client import ensure_entity
        with self.db_client.transaction() as cur:
            ensure_entity(cur, work_id, entity_id)
            cur.execute(
                """
                INSERT OR REPLACE INTO entity_phases
                (phase_id, work_id, entity_id, phase_name, valid_from_order, valid_to_order, traits_json, anti_behaviors_json, tone_examples_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    phase_id,
                    work_id,
                    entity_id,
                    phase_name,
                    valid_from_order,
                    valid_to_order,
                    json.dumps(traits, ensure_ascii=False),
                    json.dumps(anti_behaviors, ensure_ascii=False),
                    json.dumps(tone_examples, ensure_ascii=False),
                )
            )

    def get_active_phase(
        self,
        work_id: str,
        entity_id: str,
        narrative_order: int
    ) -> Optional[dict[str, Any]]:
        """
        检索在当前叙事步长下，实体处于的活跃性格阶段
        """
        sql = """
        SELECT * FROM entity_phases
        WHERE work_id = ? AND entity_id = ?
          AND valid_from_order <= ?
          AND (valid_to_order IS NULL OR valid_to_order >= ?)
        ORDER BY valid_from_order DESC
        LIMIT 1
        """
        with self.db_client.get_connection() as conn:
            cur = conn.execute(sql, (work_id, entity_id, narrative_order, narrative_order))
            row = cur.fetchone()
            if not row:
                return None

            return {
                "phase_id": row["phase_id"],
                "phase_name": row["phase_name"],
                "valid_from_order": row["valid_from_order"],
                "valid_to_order": row["valid_to_order"],
                "traits": json.loads(row["traits_json"] or "[]"),
                "anti_behaviors": json.loads(row["anti_behaviors_json"] or "[]"),
                "tone_examples": json.loads(row["tone_examples_json"] or "[]"),
            }
