"""
fxi.materials_skills.skill_store - 写作技法与规则沉淀库
"""

from pathlib import Path
from typing import Optional
import yaml
from fxi.core.config import FxiConfig, load_config


class SkillStore:
    """写作技法与规则库管理器"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def load_rules(self, skill_slug: str) -> list[str]:
        """从 skills/<skill_slug>/rules.yaml 读取写作规则"""
        rule_file = self.config.skills_dir / skill_slug / "rules.yaml"
        if not rule_file.is_file():
            return []
        try:
            data = yaml.safe_load(rule_file.read_text(encoding="utf-8")) or {}
            rules = data.get("rules", [])
            return [r if isinstance(r, str) else r.get("rule", str(r)) for r in rules]
        except Exception:
            return []

    def list_skills(self) -> list[str]:
        """列出所有可用技能"""
        if not self.config.skills_dir.is_dir():
            return []
        return [d.name for d in self.config.skills_dir.iterdir() if d.is_dir()]
