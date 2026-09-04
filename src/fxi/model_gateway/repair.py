"""
fxi.model_gateway.repair - 结构化 JSON 容错与自动修复器
"""

import json
import re
from typing import Any, Type, TypeVar
from pydantic import BaseModel
import json_repair

from fxi.core.exceptions import SchemaRepairFailedError

T = TypeVar("T", bound=BaseModel)


class StructuredOutputRepairer:
    """输出格式容错与 Pydantic 修复解析器"""

    @staticmethod
    def clean_text(raw_text: str) -> str:
        """剥离 Markdown 代码围栏与前后杂质"""
        text = raw_text.strip()
        # 匹配 ```json ... ``` 或 ``` ... ```
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence_match:
            text = fence_match.group(1).strip()
        return text

    @classmethod
    def parse_to_schema(cls, raw_output: str, schema: Type[T]) -> T:
        """
        清洗文本，通过 json_repair 修复瑕疵并执行强类型 Pydantic 验证
        """
        cleaned = cls.clean_text(raw_output)

        # 1. 尝试直接标准解析
        try:
            data = json.loads(cleaned)
            return schema.model_validate(data)
        except Exception:
            pass

        # 2. 尝试 json_repair 修复
        try:
            repaired_str = json_repair.repair_json(cleaned)
            data = json.loads(repaired_str)
            return schema.model_validate(data)
        except Exception as e:
            raise SchemaRepairFailedError(f"结构化输出修复失败，无法映射到目标 Schema {schema.__name__}: {e}\n原始文本:\n{raw_output[:200]}")
