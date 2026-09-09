"""Regression coverage for model-provider credential pools and rotation."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from fxi.core.config import FxiConfig
from fxi.core.exceptions import GatewayError
from fxi.model_gateway.gateway import ModelGateway
from fxi.model_gateway.providers import ApiKeyPool, OpenAICompatibleProvider
from fxi.model_gateway.router import TaskRouter


class _FakeResponse:
    text = "test response"

    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


def _install_fake_client(monkeypatch, statuses: list[int], authorization_headers: list[str]) -> None:
    remaining = iter(statuses)

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            authorization_headers.append(kwargs["headers"]["Authorization"])
            return _FakeResponse(next(remaining))

    monkeypatch.setattr(
        "fxi.model_gateway.providers.httpx.Client",
        lambda **kwargs: FakeClient(),
    )


def _workspace_with_models(tmp_path: Path, provider: dict) -> FxiConfig:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "default_provider": "test_provider",
                "providers": {"test_provider": provider},
                "task_routes": {
                    "chat": {
                        "provider": "default",
                        "model": "default",
                        "temperature": 0.2,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return FxiConfig(
        workspace_root=tmp_path,
        data_dir=tmp_path / "data",
        projects_dir=tmp_path / "projects",
        skills_dir=tmp_path / "skills",
        sources_dir=tmp_path / "sources",
        materials_dir=tmp_path / "materials",
        sqlite_path=tmp_path / "data" / "manifest.sqlite",
        cache_db_path=tmp_path / "data" / "cache.sqlite",
        jieba_custom_dict_path=tmp_path / "data" / "project_lexicon.txt",
    )


def _provider(
    env_names: list[str],
    *,
    rotate_on_each_call: bool,
    max_retries: int = 1,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://example.test/v1",
        api_key_env="",
        api_key_envs=env_names,
        rotate_on_each_call=rotate_on_each_call,
        key_cooldown_seconds=60.0,
        max_retries=max_retries,
        provider_name="test_provider",
    )


def test_rotate_on_each_call_cycles_before_any_rate_limit(monkeypatch):
    env_names = ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B", "FXI_TEST_KEY_C"]
    for index, env_name in enumerate(env_names, start=1):
        monkeypatch.setenv(env_name, f"credential-{index}-for-test")
    headers: list[str] = []
    _install_fake_client(monkeypatch, [200, 200, 200, 200], headers)

    provider = _provider(env_names, rotate_on_each_call=True)
    for _ in range(4):
        assert provider.complete("prompt", model="test-model") == "ok"

    assert headers == [
        "Bearer credential-1-for-test",
        "Bearer credential-2-for-test",
        "Bearer credential-3-for-test",
        "Bearer credential-1-for-test",
    ]


def test_rotation_switch_off_keeps_using_primary_key(monkeypatch):
    monkeypatch.setenv("FXI_TEST_KEY_A", "credential-a-for-test")
    monkeypatch.setenv("FXI_TEST_KEY_B", "credential-b-for-test")
    headers: list[str] = []
    _install_fake_client(monkeypatch, [200, 200], headers)

    provider = _provider(
        ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B"],
        rotate_on_each_call=False,
    )
    assert provider.complete("first", model="test-model") == "ok"
    assert provider.complete("second", model="test-model") == "ok"

    assert headers == [
        "Bearer credential-a-for-test",
        "Bearer credential-a-for-test",
    ]


def test_rate_limited_rotating_key_preserves_configured_order(monkeypatch):
    env_names = ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B", "FXI_TEST_KEY_C"]
    for index, env_name in enumerate(env_names, start=1):
        monkeypatch.setenv(env_name, f"credential-{index}-for-test")
    headers: list[str] = []
    _install_fake_client(monkeypatch, [429, 200, 200], headers)

    provider = _provider(env_names, rotate_on_each_call=True, max_retries=1)

    assert provider.complete("first", model="test-model") == "ok"
    assert provider.complete("second", model="test-model") == "ok"
    assert headers == [
        "Bearer credential-1-for-test",
        "Bearer credential-2-for-test",
        "Bearer credential-3-for-test",
    ]


@pytest.mark.parametrize("failure_status", [401, 403, 429])
def test_credential_failure_immediately_uses_spare_key(monkeypatch, failure_status):
    monkeypatch.setenv("FXI_TEST_KEY_A", "credential-a-for-test")
    monkeypatch.setenv("FXI_TEST_KEY_B", "credential-b-for-test")
    headers: list[str] = []
    _install_fake_client(monkeypatch, [failure_status, 200], headers)

    provider = _provider(
        ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B"],
        rotate_on_each_call=False,
        max_retries=1,
    )

    assert provider.complete("prompt", model="test-model") == "ok"
    assert headers == [
        "Bearer credential-a-for-test",
        "Bearer credential-b-for-test",
    ]


def test_duplicate_secret_values_are_not_counted_as_spare_keys(monkeypatch):
    monkeypatch.setenv("FXI_TEST_KEY_A", "same-credential-for-test")
    monkeypatch.setenv("FXI_TEST_KEY_B", "same-credential-for-test")
    pool = ApiKeyPool(
        ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B"],
        rotate_on_each_call=True,
        cooldown_seconds=60.0,
    )

    credential = pool.acquire()
    assert credential.env_name == "FXI_TEST_KEY_A"
    assert pool.available_count() == 1
    pool.mark_unavailable(credential.env_name)
    assert pool.has_available_credentials() is False


def test_round_robin_selection_is_thread_safe(monkeypatch):
    env_names = ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B", "FXI_TEST_KEY_C"]
    for index, env_name in enumerate(env_names, start=1):
        monkeypatch.setenv(env_name, f"credential-{index}-for-test")
    pool = ApiKeyPool(env_names, rotate_on_each_call=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        selected = list(executor.map(lambda _: pool.acquire().env_name, range(30)))

    assert Counter(selected) == Counter({env_name: 10 for env_name in env_names})


def test_router_shares_rotation_pool_across_provider_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("FXI_TEST_KEY_A", "credential-a-for-test")
    monkeypatch.setenv("FXI_TEST_KEY_B", "credential-b-for-test")
    config = _workspace_with_models(
        tmp_path,
        {
            "adapter": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key_envs": ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B"],
            "rotate_on_each_call": True,
            "default_model": "test-model",
            "max_retries": 1,
        },
    )
    router = TaskRouter(config)
    first_provider, _, _ = router.get_route("chat")
    second_provider, _, _ = router.get_route("chat")
    headers: list[str] = []
    _install_fake_client(monkeypatch, [200, 200], headers)

    assert first_provider.complete("first", model="test-model") == "ok"
    assert second_provider.complete("second", model="test-model") == "ok"
    assert headers == [
        "Bearer credential-a-for-test",
        "Bearer credential-b-for-test",
    ]


def test_google_adapter_uses_configured_openai_compatible_endpoint(tmp_path):
    config = _workspace_with_models(
        tmp_path,
        {
            "adapter": "google_genai",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key_envs": ["FXI_TEST_GEMINI_KEY_A", "FXI_TEST_GEMINI_KEY_B"],
            "rotate_on_each_call": True,
            "default_model": "gemini-test",
        },
    )

    provider, model, _ = TaskRouter(config).get_route("chat")

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert provider.api_key_envs == (
        "FXI_TEST_GEMINI_KEY_A",
        "FXI_TEST_GEMINI_KEY_B",
    )
    assert model == "gemini-test"


def test_gateway_health_accepts_any_configured_pool_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FXI_TEST_KEY_A", raising=False)
    monkeypatch.setenv("FXI_TEST_KEY_B", "credential-b-for-test")
    config = _workspace_with_models(
        tmp_path,
        {
            "adapter": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key_envs": ["FXI_TEST_KEY_A", "FXI_TEST_KEY_B"],
            "rotate_on_each_call": True,
            "default_model": "test-model",
        },
    )

    assert ModelGateway(config).is_healthy("chat") is True


def test_router_rejects_inline_key_without_disclosing_it(tmp_path):
    config = _workspace_with_models(
        tmp_path,
        {
            "adapter": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key": "configured-inline-for-test",
            "default_model": "test-model",
        },
    )

    with pytest.raises(GatewayError) as exc_info:
        TaskRouter(config).get_route("chat")

    assert "禁止内联 api_key" in str(exc_info.value)
    assert "configured-inline-for-test" not in str(exc_info.value)


def test_router_rejects_non_boolean_rotation_switch(tmp_path):
    config = _workspace_with_models(
        tmp_path,
        {
            "adapter": "openai_compatible",
            "base_url": "https://example.test/v1",
            "api_key_envs": ["FXI_TEST_KEY_A"],
            "rotate_on_each_call": "true",
            "default_model": "test-model",
        },
    )

    with pytest.raises(GatewayError, match="rotate_on_each_call 必须是布尔值"):
        TaskRouter(config).get_route("chat")
