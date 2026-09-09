"""Regression coverage for the model gateway safety boundaries."""

import pytest
import yaml
from pathlib import Path

from fxi.core.config import FxiConfig
from fxi.core.exceptions import GatewayError
from fxi.model_gateway.gateway import ModelGateway
from fxi.model_gateway.providers import OpenAICompatibleProvider


def test_model_config_has_no_inline_api_keys():
    config = yaml.safe_load(Path("config/models.yaml").read_text(encoding="utf-8"))
    for provider in config.get("providers", {}).values():
        assert "api_key" not in provider


def test_health_check_is_local_and_missing_credentials_is_unhealthy(
    temp_workspace: FxiConfig, monkeypatch
):
    for name in (
        "AGNES_API_KEY",
        "TOKENRHYTHM_API_KEY",
        "BAI_API_KEY",
        "LUNA_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    gateway = ModelGateway(temp_workspace)
    assert gateway.is_healthy() is False


def test_reasoning_only_response_is_rejected(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "", "reasoning_content": "internal"}}]}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("fxi.model_gateway.providers.httpx.Client", lambda **kwargs: FakeClient())
    monkeypatch.setenv("FXI_TEST_MISSING_KEY", "configured-for-test")
    provider = OpenAICompatibleProvider(
        api_key_env="FXI_TEST_MISSING_KEY",
        max_retries=1,
    )
    with pytest.raises(GatewayError, match="reasoning_content"):
        provider.complete("test", model="test-model")
