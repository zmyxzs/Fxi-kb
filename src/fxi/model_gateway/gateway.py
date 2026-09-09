"""
fxi.model_gateway.gateway - 知识库统一大模型调用网关
"""

import os
from typing import Any, Optional, Type, TypeVar
from pydantic import BaseModel

from fxi.core.config import FxiConfig, load_config
from fxi.model_gateway.cache import LLMCache
from fxi.model_gateway.cost_tracker import CostTracker
from fxi.model_gateway.providers import MockProvider
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

    def is_healthy(
        self,
        task_type: str = "fast_extraction",
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> bool:
        """只做无副作用的路由与凭据预检，不发起付费模型请求。"""
        try:
            provider, _, _ = self.router.get_route(
                task_type,
                provider_override=provider_override,
                model_override=model_override,
            )
            if isinstance(provider, MockProvider):
                return True
            has_credentials = getattr(provider, "has_credentials", None)
            if callable(has_credentials):
                return bool(has_credentials())
            api_key_env = getattr(provider, "api_key_env", "")
            configured_key = getattr(provider, "api_key", None)
            return bool((configured_key or os.getenv(api_key_env, "")).strip())
        except Exception:
            return False

    def complete(
        self,
        task_type: str,
        prompt: str,
        schema: Optional[Type[T]] = None,
        use_cache: bool = True,
        use_mock: bool = False,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4000,
    ) -> Any:
        """统一单段 Prompt 模型请求接口"""
        messages = [{"role": "user", "content": prompt}]
        return self.complete_chat(
            task_type=task_type,
            messages=messages,
            schema=schema,
            use_cache=use_cache,
            use_mock=use_mock,
            provider_override=provider_override,
            model_override=model_override,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete_chat(
        self,
        task_type: str,
        messages: Optional[list[dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        schema: Optional[Type[T]] = None,
        use_cache: bool = False,
        use_mock: bool = False,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: int = 4000,
    ) -> Any:
        """
        支持 system + user 分离的对话级补全调用
        """
        final_messages: list[dict[str, str]] = []
        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            final_messages.append({"role": "user", "content": user_prompt})
        if messages:
            final_messages.extend(messages)

        if not final_messages:
            raise ValueError("complete_chat 必须提供 messages 或 system_prompt/user_prompt")

        provider, model_name, route_temp = self.router.get_route(
            task_type=task_type,
            use_mock=use_mock,
            provider_override=provider_override,
            model_override=model_override,
            temperature_override=temperature,
        )
        final_temp = temperature if temperature is not None else route_temp

        prompt_str = "\n".join(f"{m['role']}: {m['content']}" for m in final_messages)
        schema_name = ""
        if schema is not None:
            schema_name = f"{schema.__module__}.{schema.__qualname__}"
        provider_name = f"{provider.__class__.__module__}.{provider.__class__.__qualname__}:{getattr(provider, 'base_url', '')}"
        cache_key = self.cache.compute_key(
            prompt_str,
            model_name,
            task_type,
            provider=provider_name,
            temperature=final_temp,
            schema=schema_name,
            max_tokens=max_tokens,
        )

        # 1. 优先查缓存
        if use_cache:
            cached_text = self.cache.get(cache_key)
            if cached_text is not None:
                if schema is not None:
                    return StructuredOutputRepairer.parse_to_schema(cached_text, schema)
                return cached_text

        # 2. 执行模型调用
        raw_output = provider.complete_messages(
            messages=final_messages,
            model=model_name,
            temperature=final_temp,
            max_tokens=max_tokens,
        )

        # 3. 结构化修复与校验
        result = raw_output
        if schema is not None:
            result = StructuredOutputRepairer.parse_to_schema(raw_output, schema)

        # 4. 回填缓存与审计用量
        if use_cache:
            self.cache.set(cache_key, prompt_str, model_name, raw_output)

        prompt_tokens = len(prompt_str) // 2
        completion_tokens = len(raw_output) // 2
        cost_cny = (prompt_tokens * 0.001 + completion_tokens * 0.002) / 1000.0
        self.tracker.record_usage(task_type, model_name, prompt_tokens, completion_tokens, cost_cny)

        return result
