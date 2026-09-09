"""
fxi.materials_skills.anti_patterns - 负向行文禁令库
"""

from typing import Optional
import yaml
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import CorruptedDataError


class AntiPatternRepository:
    """负向行文禁令与反面教条库"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()

    def get_negative_constraints(
        self,
        skill_slug: Optional[str] = None,
        scene_type: Optional[str] = None,
        work_id: Optional[str] = None,
    ) -> list[str]:
        """获取需要在写作 Prompt 中显式规避的负面教条清单"""
        constraints = []
        target_dirs = []

        if skill_slug:
            target_dirs.append(self.config.skills_dir / skill_slug)
        elif self.config.skills_dir.is_dir():
            target_dirs.extend([d for d in self.config.skills_dir.iterdir() if d.is_dir()])

        # 扫描作品专属 style 目录 (projects/<work_id>/style/anti_patterns.yaml)
        if work_id:
            p_dir = self.config.projects_dir / work_id / "style"
            if p_dir.is_dir() and p_dir not in target_dirs:
                target_dirs.append(p_dir)

        for sdir in target_dirs:
            ap_file = sdir / "anti_patterns.yaml"
            if ap_file.is_file():
                try:
                    data = yaml.safe_load(ap_file.read_text(encoding="utf-8")) or {}
                except (OSError, UnicodeError, yaml.YAMLError) as exc:
                    raise CorruptedDataError(
                        f"无法读取反模式配置 [{ap_file}]: {type(exc).__name__}"
                    ) from exc
                if not isinstance(data, dict):
                    raise CorruptedDataError(
                        f"反模式配置 [{ap_file}] 必须是 YAML 对象"
                    )

                # 支持旧版 anti_patterns 格式
                items = data.get("anti_patterns", [])
                if not isinstance(items, list):
                    raise CorruptedDataError(
                        f"反模式配置 [{ap_file}] 的 anti_patterns 必须是列表"
                    )
                for item in items:
                    if isinstance(item, str):
                        constraints.append(item)
                    elif isinstance(item, dict):
                        rule_scene = item.get("scene_type")
                        if not scene_type or not rule_scene or rule_scene == scene_type:
                            constraints.append(item.get("constraint", item.get("rule", str(item))))

                # 支持新版结构化 patterns 格式
                patterns = data.get("patterns", [])
                if not isinstance(patterns, list):
                    raise CorruptedDataError(
                        f"反模式配置 [{ap_file}] 的 patterns 必须是列表"
                    )
                for p in patterns:
                    if isinstance(p, dict):
                        st_list = p.get("scene_types", [])
                        if not scene_type or "all" in st_list or scene_type in st_list:
                            desc = p.get("description", "")
                            corr = p.get("correction_principle", "")
                            name = p.get("name", p.get("id", "反模式"))
                            text = f"【严禁{name}】: {desc}"
                            if corr:
                                text += f" (纠正法则: {corr})"
                            constraints.append(text)

        return constraints
