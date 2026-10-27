from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
import yaml

from src.main import _build_zai_sentiment_client, load_settings
from src.overseer.xai import XAIClient
from src.sentiment_sources import is_answered_sentiment_source
from src.strategy.sentiment_mean_reversion import SentimentScorer, _decode_sentiment_score


def fake_client(effect: object) -> tuple[SimpleNamespace, AsyncMock]:
    create = (
        AsyncMock(side_effect=effect)
        if isinstance(effect, Exception)
        else AsyncMock(return_value=effect)
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


def completion(content: object) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def rate_limit(message: str) -> openai.RateLimitError:
    response = MagicMock(status_code=429, headers={})
    return openai.RateLimitError(message, response=response, body=None)


def test_env_opt_in_and_zai_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMANDCODE_API_KEY", "test-only-key")
    monkeypatch.delenv("COMMANDCODE_ENABLED", raising=False)
    monkeypatch.setenv("COMMANDCODE_MODEL", "cheap-model")
    monkeypatch.setenv("ZAI_API_KEY", "test-primary")
    settings = load_settings(Path("config/settings.sentiment_macro.yaml")).ai
    assert settings.commandcode_enabled is False
    assert "test-only-key" not in repr(settings)
    with patch("src.main.XAIClient") as factory:
        _build_zai_sentiment_client(settings)
        assert factory.call_args.kwargs["commandcode_api_key"] == ""
        monkeypatch.setenv("COMMANDCODE_ENABLED", "true")
        settings = load_settings(Path("config/settings.sentiment_macro.yaml")).ai
        _build_zai_sentiment_client(settings)
        assert factory.call_args.kwargs["commandcode_api_key"] == "test-only-key"
        assert factory.call_args.kwargs["commandcode_model"] == "cheap-model"
        monkeypatch.setenv("COMMANDCODE_MODEL", "  ")
        assert (
            load_settings(Path("config/settings.sentiment_macro.yaml")).ai.commandcode_model == ""
        )
        monkeypatch.delenv("COMMANDCODE_API_KEY")
        _build_zai_sentiment_client(load_settings(Path("config/settings.sentiment_macro.yaml")).ai)
        assert factory.call_args.kwargs["commandcode_api_key"] == ""


def test_compose_uses_scoped_overlay() -> None:
    for filename in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load(Path(filename).read_text())
        assert ".env" in compose["services"]["agent_sentiment_macro"]["env_file"]
    overlay = yaml.safe_load(Path("docker-compose.commandcode.yml").read_text())
    assert list(overlay["services"]) == ["agent_sentiment_macro"]
    assert overlay["services"]["agent_sentiment_macro"]["env_file"] == [
        "${COMMANDCODE_ENV_FILE:-/etc/crypto-agent/commandcode.env}"
    ]


async def test_last_fallback_success_order_and_attribution() -> None:
    calls = []
    clients = []
    for name, value in [("primary", "bad"), ("deepseek", "bad"), ("commandcode", '{"score": 67}')]:

        async def create(*, _name=name, _value=value, **kwargs):
            calls.append((_name, kwargs))
            return completion(_value)

        clients.append(
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=AsyncMock(side_effect=create))
                )
            )
        )
    recorder = AsyncMock()
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients) as factory:
        client = XAIClient(
            api_key="p",
            model="primary",
            fallback_api_key="d",
            commandcode_api_key="c",
            commandcode_model="chosen-model",
        )
        score = await SentimentScorer(xai_client=client, observation_recorder=recorder).get_score(
            "SOLUSDT"
        )
    assert [name for name, _ in calls] == ["primary", "deepseek", "commandcode"]
    assert score == 67
    assert recorder.await_args.args[0] == {
        "symbol": "SOLUSDT",
        "score": 67.0,
        "source": "commandcode_live",
        "provider": "commandcode",
        "model": "chosen-model",
    }
    assert is_answered_sentiment_source("commandcode_live")
    assert factory.call_args.kwargs["base_url"] == "https://api.commandcode.ai/provider/v1"
    assert factory.call_args.kwargs["max_retries"] == 0
    assert calls[-1][1]["timeout"] == 10.0
    assert calls[-1][1]["max_tokens"] == 256
    assert calls[-1][1]["model"] == "chosen-model"


async def test_earlier_success_or_general_chat_never_calls_commandcode() -> None:
    primary, _ = fake_client(completion('{"score": 64}'))
    command, create = fake_client(completion('{"score": 70}'))
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]):
        client = XAIClient(api_key="p", model="m", commandcode_api_key="c", commandcode_model="m")
        assert (await client.chat_with_validator([], _decode_sentiment_score)).provider == "xai"
        await client.chat([])
    create.assert_not_awaited()


async def test_full_fallback_order() -> None:
    order = []

    def make(name: str) -> SimpleNamespace:
        async def create(**kwargs):
            order.append(name)
            return completion('{"score": 62}' if name == "commandcode" else "invalid")

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    primary, deepseek, opencode, xai, ollama, nvidia, commandcode = (
        make(name)
        for name in ("primary", "deepseek", "opencode", "xai", "ollama", "nvidia", "commandcode")
    )
    opencode.models = SimpleNamespace(
        list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="model-free")]))
    )
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary, deepseek, opencode, xai, ollama, nvidia, commandcode],
    ):
        client = XAIClient(
            api_key="p",
            model="m",
            provider="zai",
            fallback_api_key="d",
            opencode_go_api_key="o",
            xai_fallback_api_key="x",
            ollama_api_key="l",
            nvidia_api_key="n",
            commandcode_api_key="c",
            commandcode_model="m",
        )
        assert (
            await client.chat_with_validator([], _decode_sentiment_score)
        ).provider == "commandcode"
    assert order == ["primary", "opencode", "ollama", "nvidia", "xai", "deepseek", "commandcode"]


async def test_no_key_and_general_chat_do_not_attempt_paid_fallback() -> None:
    primary, _ = fake_client(completion("bad"))
    with patch("src.overseer.xai.AsyncOpenAI", return_value=primary) as factory:
        client = XAIClient(api_key="p", model="m")
        assert client._commandcode_client is None
        with pytest.raises(ValueError):
            await client.chat_with_validator([], _decode_sentiment_score)
        factory.assert_called_once()

    primary, _ = fake_client(RuntimeError("unavailable"))
    command, create = fake_client(completion("unexpected"))
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]):
        client = XAIClient(api_key="p", model="m", commandcode_api_key="c", commandcode_model="m")
        with pytest.raises(RuntimeError, match="unavailable"):
            await client.chat([])
    create.assert_not_awaited()


@pytest.mark.parametrize("bad", ["not json", '{"score": 101}', '{"score": true}', None])
async def test_bad_response_fails_conservatively_and_is_attributed(bad: object) -> None:
    primary, _ = fake_client(completion("invalid"))
    command, _ = fake_client(completion(bad))
    recorder = AsyncMock()
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]):
        client = XAIClient(api_key="p", model="m", commandcode_api_key="c", commandcode_model="m")
        score = await SentimentScorer(xai_client=client, observation_recorder=recorder).get_score(
            "SOLUSDT"
        )
    assert score == 30
    payload = recorder.await_args.args[0]
    assert payload["source"] == "error_fallback"
    assert payload["provider"] == "commandcode"
    assert payload["model"] == "m"


@pytest.mark.parametrize(
    "message,permanent", [("quota exceeded", False), ("insufficient balance", True)]
)
async def test_quota_cooldown_and_safe_errors(message: str, permanent: bool) -> None:
    primary, _ = fake_client(completion("bad"))
    command, create = fake_client(rate_limit(message + " test-secret"))
    recorder = AsyncMock()
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]):
        client = XAIClient(
            api_key="p", model="m", commandcode_api_key="test-secret", commandcode_model="m"
        )
        scorer = SentimentScorer(
            xai_client=client, observation_recorder=recorder, cache_ttl_seconds=0
        )
        assert await scorer.get_score("SOLUSDT") == 30
        assert "test-secret" not in str(recorder.await_args.args[0])
        assert await scorer.get_score("SOLUSDT") == 30
    assert create.await_count == 1
    if permanent:
        assert client._provider_disabled_dates["commandcode"] == datetime.now(UTC).date()
        client._provider_disabled_dates["commandcode"] -= timedelta(days=1)
    else:
        assert client._provider_retry_after["commandcode"] > datetime.now(UTC)
        client._provider_retry_after["commandcode"] = datetime.now(UTC) - timedelta(seconds=1)
    assert client._provider_available("commandcode")


async def test_chain_budget_cancels_slow_commandcode() -> None:
    primary, _ = fake_client(completion("bad"))
    command, create = fake_client(completion("unused"))

    async def slow(**kwargs):
        await asyncio.sleep(1)
        return completion('{"score": 50}')

    create.side_effect = slow
    with (
        patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]),
        patch("src.overseer.xai._SENTIMENT_CHAIN_TIMEOUT_SECONDS", 0.02),
        patch("src.overseer.xai._COMMANDCODE_RESERVED_SECONDS", 0.01),
    ):
        client = XAIClient(api_key="p", model="m", commandcode_api_key="c", commandcode_model="m")
        with pytest.raises(TimeoutError, match="chain exceeded") as raised:
            await client.chat_with_validator([], _decode_sentiment_score)
    assert create.await_count == 1
    assert (raised.value.provider, raised.value.model) == ("commandcode", "m")
    assert not client._provider_available("commandcode")


@pytest.mark.parametrize("model", ["", "   "])
def test_no_model_never_creates_paid_client(model: str) -> None:
    with patch("src.overseer.xai.AsyncOpenAI") as factory:
        client = XAIClient(api_key="p", model="m", commandcode_api_key="c", commandcode_model=model)
    assert client._commandcode_client is None
    factory.assert_called_once()


async def test_slow_prior_chain_reserves_time_and_cools_nvidia() -> None:
    calls = []

    def make(name: str, delay: float) -> SimpleNamespace:
        async def create(**kwargs):
            calls.append(name)
            await asyncio.sleep(delay)
            return completion('{"score": 60}' if name == "commandcode" else "bad")

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    primary, deepseek, opencode, xai, ollama, nvidia, command = [
        make(name, delay)
        for name, delay in (
            ("primary", 0.005),
            ("deepseek", 0.005),
            ("opencode", 0.005),
            ("xai", 0.005),
            ("ollama", 0.005),
            ("nvidia", 1),
            ("commandcode", 0),
        )
    ]
    opencode.models = SimpleNamespace(
        list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="model-free")]))
    )
    with (
        patch(
            "src.overseer.xai.AsyncOpenAI",
            side_effect=[primary, deepseek, opencode, xai, ollama, nvidia, command],
        ),
        patch("src.overseer.xai._SENTIMENT_CHAIN_TIMEOUT_SECONDS", 0.12),
        patch("src.overseer.xai._COMMANDCODE_RESERVED_SECONDS", 0.04),
    ):
        client = XAIClient(
            api_key="p",
            model="m",
            provider="zai",
            fallback_api_key="d",
            opencode_go_api_key="o",
            xai_fallback_api_key="x",
            ollama_api_key="o",
            nvidia_api_key="n",
            commandcode_api_key="c",
            commandcode_model="chosen",
        )
        start = asyncio.get_running_loop().time()
        result = await client.chat_with_validator([], _decode_sentiment_score)
        elapsed = asyncio.get_running_loop().time() - start
    assert calls == ["primary", "opencode", "ollama", "nvidia", "commandcode"]
    assert result.provider == "commandcode"
    assert elapsed < 0.12
    assert not client._provider_available("nvidia")


async def test_caller_cancellation_propagates_without_cooldown() -> None:
    started = asyncio.Event()
    primary, create = fake_client(completion("unused"))

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(10)

    create.side_effect = slow
    with patch("src.overseer.xai.AsyncOpenAI", return_value=primary):
        client = XAIClient(api_key="p", model="m")
        task = asyncio.create_task(client.chat_with_validator([], _decode_sentiment_score))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client._provider_available("primary")


async def test_total_deadline_attributes_active_provider() -> None:
    primary, create = fake_client(completion("unused"))
    recorder = AsyncMock()

    async def slow(**kwargs):
        await asyncio.sleep(1)

    create.side_effect = slow
    with (
        patch("src.overseer.xai.AsyncOpenAI", return_value=primary),
        patch("src.overseer.xai._SENTIMENT_CHAIN_TIMEOUT_SECONDS", 0.02),
    ):
        client = XAIClient(api_key="p", model="chosen-primary", provider="zai")
        assert (
            await SentimentScorer(xai_client=client, observation_recorder=recorder).get_score(
                "SOLUSDT"
            )
            == 30
        )
    payload = recorder.await_args.args[0]
    assert (payload["provider"], payload["model"]) == ("zai", "chosen-primary")
    assert not client._provider_available("primary")


async def test_previous_provider_secret_is_not_logged_or_recorded(caplog) -> None:
    primary, _ = fake_client(RuntimeError("api-key-secret and private prompt"))
    recorder = AsyncMock()
    with patch("src.overseer.xai.AsyncOpenAI", return_value=primary):
        client = XAIClient(api_key="p", model="m")
        assert (
            await SentimentScorer(xai_client=client, observation_recorder=recorder).get_score(
                "SOLUSDT"
            )
            == 30
        )
    assert "api-key-secret" not in caplog.text
    assert "private prompt" not in caplog.text
    assert "api-key-secret" not in str(recorder.await_args.args[0])
    assert recorder.await_args.args[0]["error"] == "RuntimeError"


async def test_immediate_provider_timeout_is_not_chain_deadline() -> None:
    primary, _ = fake_client(TimeoutError("provider timeout"))
    with patch("src.overseer.xai.AsyncOpenAI", return_value=primary):
        client = XAIClient(api_key="p", model="primary", provider="zai")
        with pytest.raises(TimeoutError, match="provider timeout") as raised:
            await client.chat_with_validator([], _decode_sentiment_score)
    assert (raised.value.provider, raised.value.model) == ("zai", "primary")
    assert not client._provider_available("primary")


async def test_immediate_provider_timeout_uses_fallback_without_false_deadline_cooldown() -> None:
    primary, _ = fake_client(TimeoutError("provider timeout"))
    command, create = fake_client(completion('{"score": 55}'))
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]):
        client = XAIClient(api_key="p", model="m", commandcode_api_key="c", commandcode_model="m")
        with patch.object(client, "_cooldown_timed_out_provider") as cooldown:
            assert (
                await client.chat_with_validator([], _decode_sentiment_score)
            ).provider == "commandcode"
    cooldown.assert_not_called()
    create.assert_awaited_once()


async def test_close_all_transports_even_when_one_fails(caplog) -> None:
    clients = [SimpleNamespace(close=AsyncMock()) for _ in range(7)]
    clients[1].close.side_effect = RuntimeError("sensitive error body")
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients):
        client = XAIClient(
            api_key="p",
            model="m",
            provider="zai",
            fallback_api_key="d",
            opencode_go_api_key="o",
            xai_fallback_api_key="x",
            ollama_api_key="l",
            nvidia_api_key="n",
            commandcode_api_key="c",
            commandcode_model="m",
        )
        await client.close()
    for transport in clients:
        transport.close.assert_awaited_once()
    assert "sensitive error body" not in caplog.text
