"""
fxi.materials_skills.anti_patterns - 负向行文禁令库
"""

from typing import Optional
import yaml
from fxi.core.config import FxiConfig, load_config


class AntiPatternRepository:
    """负向行文禁令与反面教条库"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def get_negative_constraints(self, skill_slug: Optional[str] = None, scene_type: Optional[str] = None) -> list[str]:
        """获取需要在写作 Prompt 中显式规避的负面教条清单"""
        constraints = []
        target_dirs = []

        if skill_slug:
            target_dirs.append(self.config.skills_dir / skill_slug)
        elif self.config.skills_dir.is_dir():
            target_dirs.extend([d for d in self.config.skills_dir.iterdir() if d.is_dir()])

        for sdir in target_dirs:
            ap_file = sdir / "anti_patterns.yaml"
            if ap_file.is_file():
                try:
                    data = yaml.safe_load(ap_file.read_text(encoding="utf-8")) or {}
                    items = data.get("anti_patterns", [])
                    for item in items:
                        if isinstance(item, str):
                            constraints.append(item)
                        elif isinstance(item, dict):
                            rule_scene = item.get("scene_type")
                            if not scene_type or not rule_scene or rule_scene == scene_type:
                                constraints.append(item.get("constraint", item.get("rule", str(item))))
                except Exception:
                    pass

        return constraints
