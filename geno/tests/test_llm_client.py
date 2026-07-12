"""
Tests for multi-provider LLM client request construction.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("yaml", reason="pyyaml required for experiment tooling tests")

from experiment import llm_client
from experiment.llm_client import (
    AnthropicClient,
    GeminiClient,
    OpenAIClient,
    create_client,
)


def _anthropic_response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _openai_response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


class _RecordingEndpoint:
    """Record keyword arguments passed to an SDK create() call."""

    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self.response


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, response: SimpleNamespace
) -> _RecordingEndpoint:
    endpoint = _RecordingEndpoint(response)
    client = SimpleNamespace(messages=endpoint)
    module = SimpleNamespace(Anthropic=lambda: client)
    monkeypatch.setattr(llm_client, "anthropic", module)
    return endpoint


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch, response: SimpleNamespace
) -> _RecordingEndpoint:
    endpoint = _RecordingEndpoint(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=endpoint))
    module = SimpleNamespace(OpenAI=lambda: client)
    monkeypatch.setattr(llm_client, "openai", module)
    return endpoint


def _install_fake_genai(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    module = SimpleNamespace(configure=lambda **kwargs: None)
    monkeypatch.setattr(llm_client, "genai", module)
    return module


class TestCreateClient:
    def test_routes_models_to_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_anthropic(monkeypatch, _anthropic_response())
        _install_fake_openai(monkeypatch, _openai_response())
        _install_fake_genai(monkeypatch)

        assert isinstance(create_client("claude-sonnet-4-6"), AnthropicClient)
        assert isinstance(create_client("gpt-5.4"), OpenAIClient)
        assert isinstance(create_client("o3-mini"), OpenAIClient)
        assert isinstance(create_client("gemini-2.5-pro"), GeminiClient)

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown model provider"):
            create_client("llama-3")

    def test_missing_sdk_raises_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_client, "anthropic", None)
        with pytest.raises(ImportError, match="anthropic package required"):
            create_client("claude-sonnet-4-6")


class TestAnthropicRequestParams:
    def test_temperature_sent_for_sampling_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _install_fake_anthropic(monkeypatch, _anthropic_response())
        client = AnthropicClient()

        assert client.generate("claude-sonnet-4-6", "prompt", "python") == "ok"

        (call,) = endpoint.calls
        assert call["model"] == "claude-sonnet-4-6"
        assert call["max_tokens"] == 2048
        assert call["temperature"] == 0.0

    @pytest.mark.parametrize(
        "model",
        ["claude-opus-4-7", "claude-opus-4-8", "claude-fable-5", "claude-mythos-5"],
    )
    def test_temperature_omitted_for_no_sampling_models(
        self, monkeypatch: pytest.MonkeyPatch, model: str
    ) -> None:
        endpoint = _install_fake_anthropic(monkeypatch, _anthropic_response())
        client = AnthropicClient()

        client.generate(model, "prompt", "python")

        (call,) = endpoint.calls
        assert "temperature" not in call

    def test_geno_system_prompt_is_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _install_fake_anthropic(monkeypatch, _anthropic_response())
        client = AnthropicClient()

        client.generate("claude-sonnet-4-6", "prompt", "geno")

        (call,) = endpoint.calls
        assert call["system"][0]["cache_control"] == {"type": "ephemeral"}


class TestOpenAIRequestParams:
    def test_uses_max_completion_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _install_fake_openai(monkeypatch, _openai_response())
        client = OpenAIClient()

        assert client.generate("gpt-5.4", "prompt", "python") == "ok"

        (call,) = endpoint.calls
        assert call["max_completion_tokens"] == 2048
        assert "max_tokens" not in call

    @pytest.mark.parametrize("model", ["gpt-5.4", "gpt-5.5", "o1-preview", "o3-mini"])
    def test_temperature_omitted_for_reasoning_models(
        self, monkeypatch: pytest.MonkeyPatch, model: str
    ) -> None:
        endpoint = _install_fake_openai(monkeypatch, _openai_response())
        client = OpenAIClient()

        client.generate(model, "prompt", "python")

        (call,) = endpoint.calls
        assert "temperature" not in call

    def test_temperature_sent_for_legacy_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _install_fake_openai(monkeypatch, _openai_response())
        client = OpenAIClient()

        client.generate("gpt-4.1", "prompt", "python")

        (call,) = endpoint.calls
        assert call["temperature"] == 0.0
