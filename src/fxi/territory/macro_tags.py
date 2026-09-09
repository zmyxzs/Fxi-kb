"""
fxi.territory.macro_tags - 作品级宏观数据语义标签生成器
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.domain.entities import (
    get_worldview_genre,
    load_work_config,
    select_work_section,
    validate_work_id,
)
from fxi.storage.sqlite_client import DatabaseClient


class MacroTagGenerator:
    """将已读取的据点数据转换为可执行的局势提示。"""

    _DEFAULT_POLICY = {
        "loyalty_threshold": 60.0,
        "security_threshold": 50.0,
        "resource_days_threshold": 7.0,
        "labels": {
            "loyalty_low": "【民心动荡】：据点的民心指标低于安全阈值，存在治理阻力。",
            "security_low": "【治安危局】：据点的治安指标低于安全阈值，存在内部风险。",
            "resource_low": "【{resource_type}告急】：储备仅余约 {days_left} 日用度，若不及时补给将影响据点运转。",
            "building_in_progress": "【施工工程】：重点设施【{name}】({status}) 正在营建中。",
            "stable": "【据点态势】：已读取的人口、民心与治安指标暂无异常。",
            "missing": "INCOMPLETE: 作品 [{work_id}] 未找到据点 [{territory_id}] 数据，无法生成宏观态势。",
        },
    }

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)
        self.last_status: dict[str, Any] = {"status": "UNINITIALIZED"}

    def _policy(self, work_id: str) -> tuple[dict[str, Any], Optional[str]]:
        settings, error_code = load_work_config(self.config, work_id)
        if settings is None:
            if error_code == "WORK_CONFIG_MISSING":
                self.last_status = {"work_id": work_id, "status": "AVAILABLE", "source": "generic_metrics"}
                return {
                    "loyalty_threshold": self._DEFAULT_POLICY["loyalty_threshold"],
                    "security_threshold": self._DEFAULT_POLICY["security_threshold"],
                    "resource_days_threshold": self._DEFAULT_POLICY["resource_days_threshold"],
                    "labels": dict(self._DEFAULT_POLICY["labels"]),
                }, None
            self.last_status = {"work_id": work_id, "status": "INCOMPLETE", "error_code": error_code}
            return {}, error_code

        genre = get_worldview_genre(settings)
        raw_policy = select_work_section(settings, "macro_tags", genre)
        if raw_policy is None:
            raw_policy = select_work_section(settings, "territory_rules", genre)
        policy = {
            "loyalty_threshold": self._DEFAULT_POLICY["loyalty_threshold"],
            "security_threshold": self._DEFAULT_POLICY["security_threshold"],
            "resource_days_threshold": self._DEFAULT_POLICY["resource_days_threshold"],
            "labels": dict(self._DEFAULT_POLICY["labels"]),
        }
        if raw_policy is not None and not isinstance(raw_policy, Mapping):
            return {}, "MACRO_TAG_POLICY_INVALID"
        if isinstance(raw_policy, Mapping):
            thresholds = raw_policy.get("thresholds")
            if not isinstance(thresholds, Mapping):
                thresholds = raw_policy
            for key in ("loyalty_threshold", "security_threshold", "resource_days_threshold"):
                if key in thresholds:
                    try:
                        policy[key] = float(thresholds[key])
                    except (TypeError, ValueError) as exc:
                        return {}, "MACRO_TAG_POLICY_INVALID"
            labels = raw_policy.get("labels")
            if labels is not None:
                if not isinstance(labels, Mapping):
                    return {}, "MACRO_TAG_POLICY_INVALID"
                for key, value in labels.items():
                    if isinstance(key, str) and isinstance(value, str) and value.strip():
                        policy["labels"][key] = value
        self.last_status = {
            "work_id": work_id,
            "worldview_genre": genre,
            "status": "AVAILABLE",
        }
        return policy, None

    @staticmethod
    def _render(template: str, **values: Any) -> str:
        try:
            return template.format(**values)
        except (KeyError, ValueError, IndexError):
            return "INCOMPLETE: 宏观标签模板缺少有效占位参数。"

    def generate_semantic_brief(self, work_id: str, territory_id: str) -> list[str]:
        """生成注入写作 Prompt 的宏观局势语义标签。"""
        work_id = validate_work_id(work_id)
        policy, policy_error = self._policy(work_id)
        if policy_error:
            return [f"INCOMPLETE: 作品 [{work_id}] 的宏观标签规则不可用 ({policy_error})。"]
        labels = policy["labels"]
        tags: list[str] = []

        with self.db_client.get_connection() as conn:
            cur_pop = conn.execute(
                "SELECT loyalty_score, security_score, population_total FROM territory_ledgers WHERE work_id = ? AND territory_id = ?",
                (work_id, territory_id)
            )
            prow = cur_pop.fetchone()
            if not prow:
                self.last_status = {
                    "work_id": work_id,
                    "territory_id": territory_id,
                    "status": "INCOMPLETE",
                    "error_code": "TERRITORY_DATA_MISSING",
                }
                return [self._render(labels["missing"], work_id=work_id, territory_id=territory_id)]

            loyalty = float(prow["loyalty_score"])
            security = float(prow["security_score"])
            if loyalty < float(policy["loyalty_threshold"]):
                tags.append(self._render(labels["loyalty_low"], loyalty=loyalty))
            if security < float(policy["security_threshold"]):
                tags.append(self._render(labels["security_low"], security=security))

            cur_res = conn.execute(
                "SELECT resource_type, current_amount, daily_net_yield FROM territory_resources WHERE work_id = ? AND territory_id = ?",
                (work_id, territory_id)
            )
            for row in cur_res.fetchall():
                resource_type = row["resource_type"]
                amount = float(row["current_amount"])
                daily_yield = float(row["daily_net_yield"])
                if amount <= 0.0 or (
                    daily_yield < 0.0
                    and amount / abs(daily_yield) <= float(policy["resource_days_threshold"])
                ):
                    days_left = max(0, int(amount / abs(daily_yield))) if daily_yield < 0 else 0
                    tags.append(self._render(
                        labels["resource_low"],
                        resource_type=resource_type,
                        days_left=days_left,
                    ))

            cur_bld = conn.execute(
                "SELECT name, status, level FROM territory_buildings WHERE work_id = ? AND territory_id = ? AND status != 'completed'",
                (work_id, territory_id)
            )
            for building in cur_bld.fetchall():
                tags.append(self._render(
                    labels["building_in_progress"],
                    name=building["name"],
                    status=building["status"],
                    level=building["level"],
                ))

        if not tags:
            tags.append(self._render(labels["stable"], population_total=prow["population_total"]))
        return tags
