"""
fxi.model_gateway.gateway - 知识库统一大模型调用网关
"""

from typing import Any, Optional, Type, TypeVar
from pydantic import BaseModel

from fxi.core.config import FxiConfig, load_config
from fxi.model_gateway.cache import LLMCache
from fxi.model_gateway.cost_tracker import CostTracker
from fxi.model_gateway.repair import StructuredOutputRepairer
from fxi.model_gateway.router import TaskRouter

T = TypeVar("T", bound=BaseModel)


class ModelGateway:
    """全库统一大模型调用门面：集成路由、缓存、重试、修复与记账"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.router = TaskRouter(self.config)
        self.cache = LLMCache(self.config)
        self.tracker = CostTracker(self.config)

    def complete(
        self,
        task_type: str,
        prompt: str,
        schema: Optional[Type[T]] = None,
        use_cache: bool = True,
        use_mock: bool = False
    ) -> Any:
        """
        统一模型请求接口
        """
        provider, model_name, temperature = self.router.get_route(task_type, use_mock=use_mock)
        cache_key = self.cache.compute_key(prompt, model_name, task_type)

        # 1. 优先查缓存 (0ms, 0 费用)
        if use_cache:
            cached_text = self.cache.get(cache_key)
            if cached_text is not None:
                if schema is not None:
                    return StructuredOutputRepairer.parse_to_schema(cached_text, schema)
                return cached_text

        # 2. 执行模型调用
        raw_output = provider.complete(prompt=prompt, model=model_name, temperature=temperature)

        # 3. 结构化修复与校验
        result = raw_output
        if schema is not None:
            result = StructuredOutputRepairer.parse_to_schema(raw_output, schema)

        # 4. 回填缓存与审计用量
        if use_cache:
            self.cache.set(cache_key, prompt, model_name, raw_output)

        # 估算并记录 token 成本 (以 deepseek-chat 基础费率计)
        prompt_tokens = len(prompt) // 2
        completion_tokens = len(raw_output) // 2
        cost_cny = (prompt_tokens * 0.001 + completion_tokens * 0.002) / 1000.0
        self.tracker.record_usage(task_type, model_name, prompt_tokens, completion_tokens, cost_cny)

        return result
