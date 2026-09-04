"""
fxi.model_gateway.router - 任务分级路由器
"""

from pathlib import Path
from typing import Any, Optional, Tuple
import yaml
from fxi.core.config import FxiConfig, load_config
from fxi.model_gateway.providers import BaseProvider, MockProvider, OpenAICompatibleProvider


class TaskRouter:
    """依据 task_type 路由到最优模型与提供商"""

    def __init__(self, config: Optional[FxiConfig] = None):
        self.config = config or load_config()
        self.models_config_path = self.config.workspace_root / "config" / "models.yaml"
        self._cfg = self._load_yaml()

    def _load_yaml(self) -> dict[str, Any]:
        if self.models_config_path.is_file():
            try:
                return yaml.safe_load(self.models_config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
        return {}

    def get_route(self, task_type: str, use_mock: bool = False) -> Tuple[BaseProvider, str, float]:
        """返回 (provider_instance, model_name, temperature)"""
        if use_mock:
            return MockProvider(), "mock-model", 0.0

        task_routes = self._cfg.get("task_routes", {})
        route = task_routes.get(task_type, {})

        model = route.get("model", "deepseek-chat")
        temperature = float(route.get("temperature", 0.2))

        # 默认使用 OpenAICompatible
        provider_name = route.get("provider", "openai_compatible")
        providers_cfg = self._cfg.get("providers", {}).get(provider_name, {})
        base_url = providers_cfg.get("base_url", "https://api.deepseek.com/v1")
        key_env = providers_cfg.get("api_key_env", "DEEPSEEK_API_KEY")

        provider = OpenAICompatibleProvider(base_url=base_url, api_key_env=key_env)
        return provider, model, temperature
