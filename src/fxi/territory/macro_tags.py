"""
fxi.territory.macro_tags - 宏观数值向剧作张力语义标签转化器 (全题材通用)
"""

from typing import Optional
from fxi.core.config import FxiConfig, load_config
from fxi.storage.sqlite_client import DatabaseClient


class MacroTagGenerator:
    """宏观数据语义标签化：将几十项报表数字转换为高张力剧作提示"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.db_client = DatabaseClient(self.config.sqlite_path)

    def generate_semantic_brief(self, work_id: str, territory_id: str) -> list[str]:
        """
        生成注入写作 Prompt 的宏观局势语义标签
        """
        tags: list[str] = []

        with self.db_client.get_connection() as conn:
            # 1. 检查领地人口与民心
            cur_pop = conn.execute(
                "SELECT loyalty_score, security_score, population_total FROM territory_ledgers WHERE work_id = ? AND territory_id = ?",
                (work_id, territory_id)
            )
            prow = cur_pop.fetchone()
            if prow:
                loyalty = float(prow["loyalty_score"])
                security = float(prow["security_score"])
                if loyalty < 60.0:
                    tags.append("【民心动荡】：领民对政令与赋税怨声载道，士气低迷，暗流涌动。")
                if security < 50.0:
                    tags.append("【治安危局】：防备空虚，间谍流民混杂，随时面临内部暴乱或劫掠。")

            # 2. 检查关键资源紧缺度
            cur_res = conn.execute(
                "SELECT resource_type, current_amount, daily_net_yield FROM territory_resources WHERE work_id = ? AND territory_id = ?",
                (work_id, territory_id)
            )
            for r in cur_res.fetchall():
                rtype = r["resource_type"]
                amt = float(r["current_amount"])
                dy = float(r["daily_net_yield"])
                if amt <= 0.0 or (dy < 0 and (amt / abs(dy)) <= 7):
                    days_left = max(1, int(amt / abs(dy))) if dy < 0 else 0
                    tags.append(f"【{rtype}告急】：储备仅余约 {days_left} 日用度，若不及时补给将引发停工断粮危机！")

            # 3. 检查核心工程进度
            cur_bld = conn.execute(
                "SELECT name, status, level FROM territory_buildings WHERE work_id = ? AND territory_id = ? AND status != 'completed'",
                (work_id, territory_id)
            )
            for b in cur_bld.fetchall():
                tags.append(f"【施工工程】：重点设施【{b['name']}】({b['status']}) 正在紧急营建中。")

        if not tags:
            tags.append("【据点态势】：各部运转井然，仓廪充盈，内政稳固。")

        return tags
