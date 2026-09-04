"""
fxi.model_gateway.providers - 下游大模型供应商适配器
"""

import os
from abc import ABC, abstractmethod
from typing import Optional
import httpx

from fxi.core.exceptions import GatewayError, ModelTimeoutError


class BaseProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, model: str, temperature: float = 0.2, timeout: float = 30.0) -> str:
        """执行补全调用"""
        pass


class OpenAICompatibleProvider(BaseProvider):
    """适配 DeepSeek / OpenAI / OneAPI 等兼容接口"""

    def __init__(
        self,
        base_url: str = "https://api.deepseek.com/v1",
        api_key_env: str = "DEEPSEEK_API_KEY",
        api_key: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        timeout: float = 180.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout

    def complete(self, prompt: str, model: str, temperature: float = 0.2, timeout: Optional[float] = None) -> str:
        req_timeout = timeout or self.timeout
        api_key = (self.api_key or os.getenv(self.api_key_env, "")).strip()
        if not api_key:
            # 若未配置 key，抛出清晰异常或降级
            raise GatewayError(f"缺少环境变量 {self.api_key_env} 或未配置 API Key，无法调用模型接口")

        # 别名映射：5.6-Luna -> gpt-5.6-luna
        if model.lower() in ("5.6-luna", "luna"):
            model = "gpt-5.6-luna"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        try:
            with httpx.Client(timeout=req_timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                if resp.status_code != 200:
                    raise GatewayError(f"API 返回错误码 {resp.status_code}: {resp.text}")
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            raise ModelTimeoutError(f"调用下游模型接口超时 ({req_timeout}s)")
        except Exception as e:
            if isinstance(e, (GatewayError, ModelTimeoutError)):
                raise
            raise GatewayError(f"模型调用异常: {e}")


class MockProvider(BaseProvider):
    """单元测试与离线兜底专用 Mock 提供商"""

    def complete(self, prompt: str, model: str, temperature: float = 0.2, timeout: float = 30.0) -> str:
        # 返回通用的合法 JSON 响应，以便测试
        return '{"status": "mock_success", "model": "' + model + '", "message": "completed successfully"}'
