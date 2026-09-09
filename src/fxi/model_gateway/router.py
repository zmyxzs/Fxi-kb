"""
fxi.model_gateway.router - 任务分级路由器
"""

from pathlib import Path
from collections.abc import Mapping
from threading import Lock
from typing import Any, Optional, Tuple
import os

import yaml
from fxi.core.config import FxiConfig, load_config
from fxi.core.exceptions import GatewayError
from fxi.model_gateway.providers import (
    ApiKeyPool,
    BaseProvider,
    MockProvider,
    OpenAICompatibleProvider,
    UnsupportedCapabilityError,
)


_SUPPORTED_ADAPTERS = {
    "openai_compatible": OpenAICompatibleProvider,
    "google_genai": OpenAICompatibleProvider,
}
_UNSUPPORTED_ADAPTERS = frozenset(
    {
        "local_embedding",
        "embedding",
        "vector",
        "vector_search",
        "rrf",
        "offline_queue",
        "failover",
    }
)
_UNSUPPORTED_TASKS = frozenset(
    {
        "embed",
        "embedding",
        "vector",
        "vector_search",
        "rrf",
        "reciprocal_rank_fusion",
        "offline",
        "offline_queue",
        "failover",
    }
)
_SUPPORTED_TASK_CAPABILITIES = frozenset({"chat", "chat_completion", "completion", "text_completion"})


class TaskRouter:
    """依据 task_type 路由到最优模型与提供商"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.models_config_path = self.config.workspace_root / "config" / "models.yaml"
        self._key_pools: dict[tuple[Any, ...], ApiKeyPool] = {}
        self._key_pools_lock = Lock()
        self._load_dotenv()
        self._cfg = self._load_yaml()

    def _load_dotenv(self) -> None:
        """加载工作区根目录的 .env（密钥等敏感信息不入库）；已存在的环境变量优先"""
        path = self.config.workspace_root / ".env"
        if not path.is_file():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

    def _load_yaml(self) -> dict[str, Any]:
        candidates = [self.models_config_path, Path(__file__).resolve().parents[3] / "config" / "models.yaml"]
        for path in dict.fromkeys(candidates):
            if not path.is_file():
                continue
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise RuntimeError(f"模型路由配置不可读: {path}") from exc
            if not isinstance(loaded, dict):
                raise RuntimeError(f"模型路由配置必须是对象: {path}")
            return loaded
        raise FileNotFoundError(f"模型路由配置不存在: {self.models_config_path}")

    @staticmethod
    def _parse_key_pool_config(
        provider_name: str,
        providers_cfg: Mapping[str, Any],
    ) -> tuple[tuple[str, ...], bool, float]:
        if "api_key" in providers_cfg:
            raise GatewayError(
                f"provider '{provider_name}' 禁止内联 api_key；请改用 api_key_env 或 api_key_envs"
            )

        env_names: list[str] = []
        single_env = providers_cfg.get("api_key_env")
        if single_env is not None:
            if not isinstance(single_env, str) or not single_env.strip():
                raise GatewayError(f"provider '{provider_name}' api_key_env 配置无效")
            env_names.append(single_env.strip())

        multiple_envs = providers_cfg.get("api_key_envs")
        if multiple_envs is not None:
            if not isinstance(multiple_envs, (list, tuple)):
                raise GatewayError(f"provider '{provider_name}' api_key_envs 必须是列表")
            env_names.extend(multiple_envs)
        if not env_names:
            raise GatewayError(
                f"provider '{provider_name}' 未配置 api_key_env 或 api_key_envs"
            )

        rotate_on_each_call = providers_cfg.get("rotate_on_each_call", False)
        if not isinstance(rotate_on_each_call, bool):
            raise GatewayError(f"provider '{provider_name}' rotate_on_each_call 必须是布尔值")

        cooldown_value = providers_cfg.get("key_cooldown_seconds", 60.0)
        if isinstance(cooldown_value, bool):
            raise GatewayError(
                f"provider '{provider_name}' key_cooldown_seconds 必须是 0..3600 的数字"
            )
        try:
            cooldown_seconds = float(cooldown_value)
        except (TypeError, ValueError) as exc:
            raise GatewayError(
                f"provider '{provider_name}' key_cooldown_seconds 必须是 0..3600 的数字"
            ) from exc
        if not 0.0 <= cooldown_seconds <= 3600.0:
            raise GatewayError(
                f"provider '{provider_name}' key_cooldown_seconds 必须在 0..3600 之间"
            )

        # ApiKeyPool owns environment-name validation, deduplication and the hard size limit.
        validated_pool = ApiKeyPool(
            env_names,
            rotate_on_each_call=rotate_on_each_call,
            cooldown_seconds=cooldown_seconds,
        )
        return validated_pool.api_key_envs, rotate_on_each_call, cooldown_seconds

    def _get_key_pool(
        self,
        provider_name: str,
        base_url: str,
        api_key_envs: tuple[str, ...],
        rotate_on_each_call: bool,
        cooldown_seconds: float,
    ) -> ApiKeyPool:
        pool_key = (
            provider_name,
            base_url.rstrip("/"),
            api_key_envs,
            rotate_on_each_call,
            cooldown_seconds,
        )
        with self._key_pools_lock:
            pool = self._key_pools.get(pool_key)
            if pool is None:
                pool = ApiKeyPool(
                    api_key_envs,
                    rotate_on_each_call=rotate_on_each_call,
                    cooldown_seconds=cooldown_seconds,
                )
                self._key_pools[pool_key] = pool
            return pool

    def get_route(
        self,
        task_type: str,
        use_mock: bool = False,
        provider_override: Optional[str] = None,
        model_override: Optional[str] = None,
        temperature_override: Optional[float] = None,
    ) -> Tuple[BaseProvider, str, float]:
        """返回 (provider_instance, model_name, temperature)"""
        if use_mock:
            return MockProvider(), "mock-model", 0.0

        task_routes = self._cfg.get("task_routes", {})
        if not isinstance(task_routes, Mapping):
            raise GatewayError("模型路由配置的 task_routes 必须是对象")
        route = task_routes.get(task_type, {})
        if not isinstance(route, Mapping):
            raise GatewayError(f"任务路由配置无效: {task_type}")

        configured_capability = route.get("capability", route.get("operation"))
        if configured_capability is None:
            capability_name = task_type.strip().lower().replace("-", "_")
            capability_is_explicit = False
        else:
            if not isinstance(configured_capability, str):
                raise GatewayError(f"任务路由 capability 配置无效: {task_type}")
            capability_name = configured_capability.strip().lower().replace("-", "_")
            capability_is_explicit = True
        if capability_name in _UNSUPPORTED_TASKS or (
            capability_is_explicit and capability_name not in _SUPPORTED_TASK_CAPABILITIES
        ):
            raise UnsupportedCapabilityError(
                f"task '{task_type}' capability '{capability_name}' is unsupported"
            )

        # 解析 provider
        default_provider = self._cfg.get("default_provider")
        provider_name = provider_override if provider_override is not None else route.get("provider")
        if provider_name is None:
            provider_name = default_provider
        if provider_name == "default":
            provider_name = default_provider
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise GatewayError("模型路由未配置有效 provider")
        provider_name = provider_name.strip()

        providers = self._cfg.get("providers")
        if not isinstance(providers, Mapping):
            raise GatewayError("模型路由配置的 providers 必须是对象")
        if provider_name not in providers:
            raise GatewayError(f"provider 未配置: {provider_name}")
        providers_cfg = providers[provider_name]
        if not isinstance(providers_cfg, Mapping):
            raise GatewayError(f"provider 配置无效: {provider_name}")

        configured_adapter = providers_cfg.get("adapter", providers_cfg.get("type"))
        if configured_adapter is None:
            # 兼容现有旧配置：只有明确提供 base_url 的 provider 才能按兼容协议接入。
            configured_adapter = "openai_compatible" if providers_cfg.get("base_url") else None
        if not isinstance(configured_adapter, str) or not configured_adapter.strip():
            raise UnsupportedCapabilityError(
                f"provider '{provider_name}' is unsupported: no adapter is configured"
            )
        adapter_name = configured_adapter.strip().lower().replace("-", "_")
        if adapter_name in _UNSUPPORTED_ADAPTERS:
            raise UnsupportedCapabilityError(
                f"provider '{provider_name}' adapter '{adapter_name}' is unsupported"
            )
        provider_cls = _SUPPORTED_ADAPTERS.get(adapter_name)
        if provider_cls is None:
            raise UnsupportedCapabilityError(
                f"provider '{provider_name}' adapter '{adapter_name}' is unsupported"
            )

        base_url = providers_cfg.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            if adapter_name == "google_genai":
                raise UnsupportedCapabilityError(
                    f"provider '{provider_name}' adapter 'google_genai' is unsupported "
                    "without an explicit OpenAI-compatible base_url"
                )
            raise GatewayError(f"provider '{provider_name}' 未配置 base_url")
        base_url = base_url.strip()
        api_key_envs, rotate_on_each_call, cooldown_seconds = self._parse_key_pool_config(
            provider_name,
            providers_cfg,
        )
        key_pool = self._get_key_pool(
            provider_name,
            base_url,
            api_key_envs,
            rotate_on_each_call,
            cooldown_seconds,
        )
        reasoning_effort = route.get("reasoning_effort", providers_cfg.get("reasoning_effort"))
        try:
            timeout = float(
                route.get(
                    "timeout",
                    providers_cfg.get("timeout", providers_cfg.get("timeout_seconds", 180.0)),
                )
            )
        except (TypeError, ValueError) as exc:
            raise GatewayError(f"provider '{provider_name}' timeout 配置无效") from exc
        if timeout <= 0:
            raise GatewayError(f"provider '{provider_name}' timeout 必须大于 0")
        # 初次调用 + 最多两次额外重试；配置值代表总尝试次数。
        try:
            max_retries = min(max(int(providers_cfg.get("max_retries", 3)), 1), 3)
        except (TypeError, ValueError) as exc:
            raise GatewayError(f"provider '{provider_name}' max_retries 配置无效") from exc

        # 解析 model
        model = model_override if model_override is not None else route.get("model", "default")
        if model == "default":
            model = providers_cfg.get("default_model")
        if not isinstance(model, str) or not model.strip():
            raise GatewayError(f"provider '{provider_name}' 未配置有效 default_model")
        model = model.strip()

        # 解析 temperature
        if temperature_override is not None:
            temperature = float(temperature_override)
        else:
            try:
                temperature = float(route.get("temperature", 0.2))
            except (TypeError, ValueError) as exc:
                raise GatewayError(f"任务路由 temperature 配置无效: {task_type}") from exc

        provider = provider_cls(
            base_url=base_url,
            api_key_env=api_key_envs[0],
            api_key_envs=api_key_envs[1:],
            reasoning_effort=reasoning_effort,
            timeout=timeout,
            max_retries=max_retries,
            provider_name=provider_name,
            rotate_on_each_call=rotate_on_each_call,
            key_cooldown_seconds=cooldown_seconds,
            key_pool=key_pool,
        )
        return provider, model, temperature
