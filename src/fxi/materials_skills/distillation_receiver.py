"""
fxi.materials_skills.distillation_receiver - novel-Skill 学习成果规范化入库接收器
"""

from pathlib import Path
from typing import Any, Optional
import yaml
from fxi.core.config import FxiConfig, load_config


class DistillationReceiver:
    """来自 novel-Skill 发布成果的持久化落地接收器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def ingest_skill_package(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        接收 novel-Skill 验证晋升发布的技法包
        写入 skills/<skill_slug>/rules.yaml 和 anti_patterns.yaml
        """
        slug = payload.get("slug")
        if not slug:
            return {"status": "error", "message": "缺少 slug 字段"}

        target_dir = self.config.skills_dir / slug
        target_dir.mkdir(parents=True, exist_ok=True)

        rules = payload.get("rules", [])
        anti_patterns = payload.get("anti_patterns", [])

        # 写入 rules.yaml
        rules_file = target_dir / "rules.yaml"
        rules_file.write_text(yaml.dump({"rules": rules}, allow_unicode=True), encoding="utf-8")

        # 写入 anti_patterns.yaml
        if anti_patterns:
            ap_file = target_dir / "anti_patterns.yaml"
            ap_file.write_text(yaml.dump({"anti_patterns": anti_patterns}, allow_unicode=True), encoding="utf-8")

        return {
            "status": "success",
            "skill_slug": slug,
            "rules_count": len(rules),
            "anti_patterns_count": len(anti_patterns),
        }
