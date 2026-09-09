"""
fxi.game_engine.attributes - 作品级属性公式执行器
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from typing import Any, Optional

from fxi.core.config import FxiConfig, load_config
from fxi.domain.entities import (
    get_worldview_genre,
    load_work_config,
    select_work_section,
    validate_work_id,
)


class _FormulaError(ValueError):
    """Raised for an invalid or unavailable configured formula."""


class AttributeCalculator:
    """Execute explicitly configured derived-stat formulas without using ``eval``."""

    _ALIASES = {
        "str": "strength",
        "力量": "strength",
        "agi": "agility",
        "敏捷": "agility",
        "int": "intelligence",
        "智力": "intelligence",
        "con": "constitution",
        "体质": "constitution",
    }
    _FUNCTIONS = {
        "abs": abs,
        "float": float,
        "int": int,
        "max": max,
        "min": min,
        "round": round,
    }

    @classmethod
    def _incomplete(
        cls,
        error_code: str,
        message: str,
        *,
        work_id: Optional[str] = None,
        worldview_genre: Optional[str] = None,
        missing_stats: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "INCOMPLETE",
            "error_code": error_code,
            "message": message,
        }
        if work_id is not None:
            result["work_id"] = work_id
        if worldview_genre is not None:
            result["worldview_genre"] = worldview_genre
        if missing_stats:
            result["missing_stats"] = sorted(set(missing_stats))
        return result

    @classmethod
    def _normalise_stats(cls, base_stats: Mapping[str, Any]) -> tuple[dict[str, float], Optional[str]]:
        values: dict[str, float] = {}
        for key, raw_value in base_stats.items():
            if not isinstance(key, str) or not key.strip() or isinstance(raw_value, bool):
                return {}, "base_stats 必须是非空字符串键和有限数值"
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                return {}, f"基础属性 [{key}] 不是数值"
            if not math.isfinite(value):
                return {}, f"基础属性 [{key}] 必须是有限数值"
            key_text = key.strip()
            lower_key = key_text.lower()
            values[lower_key] = value
            values[key_text] = value
            canonical_key = cls._ALIASES.get(lower_key, cls._ALIASES.get(key_text))
            if canonical_key:
                values[canonical_key] = value
        return values, None

    @classmethod
    def _formula_expression(cls, raw_formula: Any) -> Optional[str]:
        if isinstance(raw_formula, (int, float)) and not isinstance(raw_formula, bool):
            return repr(raw_formula)
        if isinstance(raw_formula, str) and raw_formula.strip():
            return raw_formula.strip()
        if isinstance(raw_formula, Mapping):
            for key in ("expression", "formula"):
                expression = raw_formula.get(key)
                if isinstance(expression, str) and expression.strip():
                    return expression.strip()
        return None

    @classmethod
    def _evaluate(cls, node: ast.AST, values: Mapping[str, float]) -> float:
        if isinstance(node, ast.Expression):
            return cls._evaluate(node.body, values)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in values:
            return float(values[node.id])
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)):
            left = cls._evaluate(node.left, values)
            right = cls._evaluate(node.right, values)
            try:
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                if isinstance(node.op, ast.Pow):
                    return left ** right
                return left % right
            except (ArithmeticError, OverflowError) as exc:
                raise _FormulaError("公式计算发生数值错误") from exc
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = cls._evaluate(node.operand, values)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in cls._FUNCTIONS:
            if node.keywords:
                raise _FormulaError("公式不允许关键字参数")
            args = [cls._evaluate(arg, values) for arg in node.args]
            try:
                return float(cls._FUNCTIONS[node.func.id](*args))
            except (TypeError, ValueError, ArithmeticError, OverflowError) as exc:
                raise _FormulaError("公式函数参数无效") from exc
        if isinstance(node, ast.Name):
            raise _FormulaError(f"缺少基础属性 [{node.id}]")
        raise _FormulaError("公式包含不允许的表达式")

    @classmethod
    def _load_formulas(
        cls,
        config: FxiConfig,
        work_id: str,
        genre_type: Optional[str],
    ) -> tuple[Optional[dict[str, Any]], Optional[str], Optional[str]]:
        settings, error_code = load_work_config(config, work_id)
        if settings is None:
            return None, error_code or "WORK_CONFIG_UNAVAILABLE", None
        genre = genre_type.strip() if isinstance(genre_type, str) and genre_type.strip() else get_worldview_genre(settings)
        raw_formulas = select_work_section(settings, "attribute_formulas", genre)
        if raw_formulas is None:
            return None, "ATTRIBUTE_FORMULAS_MISSING", genre
        if not isinstance(raw_formulas, Mapping):
            return None, "ATTRIBUTE_FORMULAS_INVALID", genre
        formulas = {
            key.strip(): value
            for key, value in raw_formulas.items()
            if isinstance(key, str) and key.strip()
        }
        if not formulas or any(cls._formula_expression(value) is None for value in formulas.values()):
            return None, "ATTRIBUTE_FORMULAS_INVALID", genre
        return formulas, None, genre

    @classmethod
    def compute_derived_stats(
        cls,
        base_stats: dict[str, float],
        genre_type: Optional[str] = None,
        *,
        work_id: Optional[str] = None,
        config: Optional[FxiConfig] = None,
    ) -> dict[str, Any]:
        """Compute stats from the requested work's configured formulas.

        ``genre_type`` remains a compatible explicit override. It never supplies
        formulas by itself; a work scope and its configuration are still required.
        """
        if not isinstance(base_stats, Mapping):
            return cls._incomplete("BASE_STATS_INVALID", "base_stats 必须是对象")
        if work_id is None:
            return cls._incomplete("WORK_SCOPE_REQUIRED", "计算派生属性必须显式提供 work_id")
        work_id = validate_work_id(work_id)
        formulas, error_code, genre = cls._load_formulas(config or load_config(), work_id, genre_type)
        if formulas is None:
            return cls._incomplete(
                error_code or "ATTRIBUTE_FORMULAS_UNAVAILABLE",
                "作品未提供可执行的题材属性公式，未生成派生属性",
                work_id=work_id,
                worldview_genre=genre,
            )

        values, stats_error = cls._normalise_stats(base_stats)
        if stats_error:
            return cls._incomplete("BASE_STATS_INVALID", stats_error, work_id=work_id, worldview_genre=genre)

        result: dict[str, Any] = {}
        missing_stats: list[str] = []
        try:
            for output_name, raw_formula in formulas.items():
                expression = cls._formula_expression(raw_formula)
                if expression is None:
                    raise _FormulaError(f"派生属性 [{output_name}] 公式为空")
                try:
                    tree = ast.parse(expression, mode="eval")
                    value = cls._evaluate(tree, values)
                except SyntaxError as exc:
                    raise _FormulaError(f"派生属性 [{output_name}] 公式语法无效") from exc
                if not math.isfinite(value):
                    raise _FormulaError(f"派生属性 [{output_name}] 结果不是有限数值")
                result[output_name] = int(value) if value.is_integer() else round(value, 6)
        except _FormulaError as exc:
            message = str(exc)
            if message.startswith("缺少基础属性 [") and message.endswith("]"):
                missing_stats.append(message[8:-1])
            return cls._incomplete(
                "ATTRIBUTE_FORMULA_UNAVAILABLE" if missing_stats else "ATTRIBUTE_FORMULA_INVALID",
                message,
                work_id=work_id,
                worldview_genre=genre,
                missing_stats=missing_stats,
            )
        return result
