"""
兼容入口：旧的《斩神》专用清理脚本。

该模块已弃用，不再持有作品名、路径或 SQLite 删除逻辑。新调用必须显式
提供 ``work_id``，并统一转发到受控的 ``clear_parsing_work`` 服务。
"""

from __future__ import annotations

import argparse
import warnings
from typing import Optional

from fxi.core.config import FxiConfig, load_config


def run_cleanup(
    config: Optional[FxiConfig] = None,
    *,
    work_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """弃用兼容包装；不再默认任何作品，也不直接操作数据库或文件。"""
    warnings.warn(
        "fxi.tools.clear_zhanshen_parsing 已弃用，请使用 project clear-parsing；"
        "该兼容入口将在下一个大版本移除。",
        DeprecationWarning,
        stacklevel=2,
    )
    if not work_id:
        raise ValueError("旧清理入口必须显式提供 work_id；请使用 project clear-parsing")
    from fxi.cli.commands_project import clear_parsing_work

    return clear_parsing_work(config or load_config(), work_id, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="已弃用的通用解析清理兼容入口")
    parser.add_argument("--work-id", required=True, help="显式作品 ID")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行修改")
    args = parser.parse_args()
    result = run_cleanup(work_id=args.work_id, dry_run=args.dry_run)
    print(result)
