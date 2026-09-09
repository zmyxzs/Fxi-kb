"""
fxi.model_gateway.providers - 下游大模型供应商适配器
"""

import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Optional
import httpx

from fxi.core.exceptions import GatewayError, ModelTimeoutError


_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_KEY_POOL_SIZE = 32


@dataclass(frozen=True)
class ApiCredential:
    """Resolved credential whose secret value must never appear in repr/logs."""

    env_name: str
    value: str = field(repr=False)


class ApiKeyPool:
    """Thread-safe, workspace-local round-robin pool of environment credentials."""

    def __init__(
        self,
        api_key_envs: Sequence[str],
        *,
        rotate_on_each_call: bool = False,
        cooldown_seconds: float = 60.0,
    ) -> None:
        if isinstance(api_key_envs, (str, bytes)):
            raise GatewayError("api_key_envs 必须是环境变量名列表")
        if not isinstance(rotate_on_each_call, bool):
            raise GatewayError("rotate_on_each_call 必须是布尔值")
        if isinstance(cooldown_seconds, bool):
            raise GatewayError("key_cooldown_seconds 必须是 0..3600 的数字")
        try:
            normalized_cooldown = float(cooldown_seconds)
        except (TypeError, ValueError) as exc:
            raise GatewayError("key_cooldown_seconds 必须是 0..3600 的数字") from exc
        if not 0.0 <= normalized_cooldown <= 3600.0:
            raise GatewayError("key_cooldown_seconds 必须在 0..3600 之间")

        normalized_envs: list[str] = []
        for raw_name in api_key_envs:
            if not isinstance(raw_name, str) or not _ENV_NAME_PATTERN.fullmatch(raw_name.strip()):
                raise GatewayError("api_key_envs 包含无效的环境变量名")
            name = raw_name.strip()
            if name not in normalized_envs:
                normalized_envs.append(name)
        if not normalized_envs:
            raise GatewayError("api_key_envs 至少需要一个环境变量名")
        if len(normalized_envs) > _MAX_KEY_POOL_SIZE:
            raise GatewayError(f"api_key_envs 最多允许 {_MAX_KEY_POOL_SIZE} 项")

        self.api_key_envs = tuple(normalized_envs)
        self.rotate_on_each_call = rotate_on_each_call
        self.cooldown_seconds = normalized_cooldown
        self._cursor = 0
        self._cooldowns: dict[str, float] = {}
        self._lock = Lock()

    def _resolved_credentials_locked(self) -> list[ApiCredential]:
        now = time.monotonic()
        credentials: list[ApiCredential] = []
        seen_values: set[str] = set()
        for env_name in self.api_key_envs:
            value = os.getenv(env_name, "").strip()
            if not value or value in seen_values:
                continue
            seen_values.add(value)
            unavailable_until = self._cooldowns.get(env_name, 0.0)
            if unavailable_until and unavailable_until <= now:
                self._cooldowns.pop(env_name, None)
                unavailable_until = 0.0
            if unavailable_until:
                continue
            credentials.append(ApiCredential(env_name=env_name, value=value))
        return credentials

    def available_count(self) -> int:
        with self._lock:
            return len(self._resolved_credentials_locked())

    def has_available_credentials(self) -> bool:
        return self.available_count() > 0

    def acquire(
        self,
        exclude: Collection[str] = (),
        *,
        advance: Optional[bool] = None,
    ) -> ApiCredential:
        excluded = set(exclude)
        should_advance = self.rotate_on_each_call if advance is None else advance
        if not isinstance(should_advance, bool):
            raise GatewayError("密钥池 advance 参数必须是布尔值")

        with self._lock:
            credentials = {
                credential.env_name: credential
                for credential in self._resolved_credentials_locked()
            }
            start_index = self._cursor if should_advance else 0
            for offset in range(len(self.api_key_envs)):
                index = (start_index + offset) % len(self.api_key_envs)
                env_name = self.api_key_envs[index]
                credential = credentials.get(env_name)
                if credential is None or env_name in excluded:
                    continue
                if should_advance:
                    self._cursor = (index + 1) % len(self.api_key_envs)
                return credential

            configured = ", ".join(self.api_key_envs)
            raise GatewayError(
                f"密钥池没有可用凭据；请检查环境变量或等待冷却结束: {configured}"
            )

    def mark_unavailable(self, env_name: str) -> None:
        if env_name not in self.api_key_envs:
            return
        with self._lock:
            self._cooldowns[env_name] = time.monotonic() + self.cooldown_seconds


class UnsupportedCapabilityError(GatewayError):
    """Raised when a provider capability has no implemented adapter."""


class BaseProvider(ABC):
    """Common provider contract; chat completion is the only implemented capability."""

    capabilities = frozenset({"chat_completion"})

    @staticmethod
    def _raise_unsupported(capability: str) -> None:
        raise UnsupportedCapabilityError(
            f"provider capability '{capability}' is unsupported; no adapter is implemented"
        )

    def embed(self, *args: Any, **kwargs: Any) -> list[float]:
        self._raise_unsupported("embedding")

    def vector_search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._raise_unsupported("vector_search")

    def rrf(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self._raise_unsupported("rrf")

    def enqueue_offline(self, *args: Any, **kwargs: Any) -> None:
        self._raise_unsupported("offline_queue")

    def failover(self, *args: Any, **kwargs: Any) -> str:
        self._raise_unsupported("failover")

    @abstractmethod
    def complete(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
        timeout: Optional[float] = None,
        max_tokens: int = 4000,
    ) -> str:
        """执行补全调用"""
        pass

    def complete_messages(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.2,
        timeout: Optional[float] = None,
        max_tokens: int = 4000,
    ) -> str:
        """执行基于对话消息列表的补全调用"""
        combined = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
        return self.complete(combined, model=model, temperature=temperature, timeout=timeout, max_tokens=max_tokens)


class OpenAICompatibleProvider(BaseProvider):
    """适配 Agnes / TokenRhythm / DeepSeek / OpenAI / OneAPI 等标准兼容接口"""

    def __init__(
        self,
        base_url: str = "https://api.deepseek.com/v1",
        api_key_env: str = "DEEPSEEK_API_KEY",
        api_key_envs: Optional[Sequence[str]] = None,
        api_key: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        timeout: float = 180.0,
        max_retries: int = 3,
        provider_name: str = "",
        rotate_on_each_call: bool = False,
        key_cooldown_seconds: float = 60.0,
        key_pool: Optional[ApiKeyPool] = None,
    ):
        self.base_url = base_url.rstrip("/")
        env_names: list[str] = []
        if api_key_env:
            env_names.append(api_key_env)
        if api_key_envs is not None:
            if isinstance(api_key_envs, (str, bytes)):
                raise GatewayError("api_key_envs 必须是环境变量名列表")
            env_names.extend(api_key_envs)

        self.key_pool = key_pool
        if self.key_pool is None and env_names:
            self.key_pool = ApiKeyPool(
                env_names,
                rotate_on_each_call=rotate_on_each_call,
                cooldown_seconds=key_cooldown_seconds,
            )
        if self.key_pool is not None:
            self.api_key_envs = self.key_pool.api_key_envs
            self.rotate_on_each_call = self.key_pool.rotate_on_each_call
        else:
            self.api_key_envs = tuple()
            self.rotate_on_each_call = rotate_on_each_call
        self.api_key_env = self.api_key_envs[0] if self.api_key_envs else ""
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.max_retries = max_retries
        self.provider_name = provider_name

    def has_credentials(self) -> bool:
        if isinstance(self.api_key, str) and self.api_key.strip():
            return True
        return bool(self.key_pool and self.key_pool.has_available_credentials())

    def _get_credential(
        self,
        exclude: Collection[str] = (),
        *,
        advance: Optional[bool] = None,
    ) -> ApiCredential:
        if isinstance(self.api_key, str) and self.api_key.strip():
            return ApiCredential(env_name="<inline>", value=self.api_key.strip())
        if self.key_pool is None:
            raise GatewayError("未配置 API key 环境变量，无法调用模型接口")
        return self.key_pool.acquire(exclude, advance=advance)

    def _get_api_key(self) -> str:
        return self._get_credential().value

    def _alternate_credential(
        self,
        current: ApiCredential,
        attempted_envs: set[str],
    ) -> Optional[ApiCredential]:
        if self.key_pool is None or current.env_name == "<inline>":
            return None
        self.key_pool.mark_unavailable(current.env_name)
        try:
            return self.key_pool.acquire(attempted_envs, advance=True)
        except GatewayError:
            # The original HTTP failure remains the authoritative error when no spare key exists.
            return None

    def complete(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
        timeout: Optional[float] = None,
        max_tokens: int = 4000,
    ) -> str:
        return self.complete_messages(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    def complete_messages(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.2,
        timeout: Optional[float] = None,
        max_tokens: int = 4000,
    ) -> str:
        req_timeout = self.timeout if timeout is None else timeout
        credential = self._get_credential(advance=self.rotate_on_each_call)
        attempted_envs = {credential.env_name}
        credential_count = self.key_pool.available_count() if self.key_pool else 1
        total_attempt_limit = self.max_retries + max(credential_count - 1, 0)

        # 别名映射
        if model.lower() in ("5.6-luna", "luna"):
            model = "gpt-5.6-luna"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        url = f"{self.base_url}/chat/completions"
        last_error = None
        general_failures = 0
        total_attempts = 0

        is_local = any(h in self.base_url for h in ("localhost", "127.0.0.1"))
        while total_attempts < total_attempt_limit:
            total_attempts += 1
            headers = {
                "Authorization": f"Bearer {credential.value}",
                "Content-Type": "application/json",
            }
            try:
                with httpx.Client(trust_env=not is_local, timeout=httpx.Timeout(req_timeout, connect=5.0)) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        choices = data.get("choices", [])
                        if not choices:
                            raise GatewayError("模型响应为空 choices", details=data)
                        msg = choices[0].get("message", {})
                        content = msg.get("content", "") or ""
                        # 推理内容不是正文；空 content 必须显式失败，不能伪造正文。
                        if not content.strip():
                            reasoning = msg.get("reasoning_content", "") or ""
                            if reasoning.strip():
                                raise GatewayError("模型仅返回 reasoning_content，缺少正文 content")
                            raise GatewayError("模型响应缺少正文 content")
                        return content.strip()
                    elif resp.status_code in (401, 403):
                        last_error = GatewayError(
                            f"大模型认证失败 ({resp.status_code})，凭据环境变量: {credential.env_name}"
                        )
                        alternate = self._alternate_credential(credential, attempted_envs)
                        if alternate is None:
                            break
                        credential = alternate
                        attempted_envs.add(credential.env_name)
                        continue
                    elif resp.status_code == 429:
                        last_error = GatewayError(
                            f"大模型被限流 429，凭据环境变量: {credential.env_name}"
                        )
                        alternate = self._alternate_credential(credential, attempted_envs)
                        if alternate is None:
                            break
                        credential = alternate
                        attempted_envs.add(credential.env_name)
                        continue
                    else:
                        last_error = GatewayError(f"大模型返回 HTTP {resp.status_code}: {resp.text[:200]}")
            except httpx.TimeoutException:
                last_error = ModelTimeoutError(f"调用大模型接口超时 ({req_timeout}s)")
            except GatewayError:
                raise
            except Exception as e:
                last_error = GatewayError(f"模型调用网络异常: {e}")

            general_failures += 1
            if general_failures >= self.max_retries or total_attempts >= total_attempt_limit:
                break
            time.sleep(1.5 * general_failures)

        raise last_error or GatewayError("大模型重试耗尽后仍然失败")


class MockProvider(BaseProvider):
    """单元测试与离线兜底专用 Mock 提供商"""

    def complete(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
        timeout: Optional[float] = 30.0,
        max_tokens: int = 4000,
    ) -> str:
        # 返回通用的合法 JSON 响应，以便测试
        return '{"status": "mock_success", "model": "' + model + '", "message": "completed successfully"}'
