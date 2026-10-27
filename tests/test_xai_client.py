import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from openai import AsyncOpenAI

from src.overseer.xai import ChatResult, XAIClient, _is_permanent_provider_error
from src.strategy.sentiment_mean_reversion import _decode_sentiment_score


def _make_completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _make_connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=MagicMock())


def _make_permission_denied() -> openai.PermissionDeniedError:
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.headers = {}
    return openai.PermissionDeniedError("forbidden", response=mock_response, body=None)


def _make_insufficient_balance() -> openai.RateLimitError:
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    return openai.RateLimitError(
        "Insufficient balance or no resource package",
        response=mock_response,
        body={"error": {"message": "Insufficient balance or no resource package"}},
    )


@pytest.mark.asyncio
async def test_chat_returns_trimmed_content() -> None:
    create = AsyncMock(return_value=_make_completion("  market regime stable  \n"))
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client) as mock_openai:
        client = XAIClient(api_key="test-key", model="grok-3")
        response = await client.chat([{"role": "user", "content": "status?"}])

    mock_openai.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.x.ai/v1",
        timeout=30.0,
        max_retries=0,
    )
    create.assert_awaited_once_with(
        model="grok-3",
        messages=[{"role": "user", "content": "status?"}],
        temperature=0.2,
    )
    assert response == ChatResult(content="market regime stable", provider="xai", model="grok-3")


@pytest.mark.asyncio
async def test_chat_raises_when_content_missing() -> None:
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=[]))])
    create = AsyncMock(return_value=completion)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="grok-3")
        with pytest.raises(RuntimeError, match="No response content returned by xAI API"):
            await client.chat([{"role": "user", "content": "status?"}])

    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_general_chat_retries_transient_primary_error() -> None:
    create = AsyncMock(side_effect=[_make_connection_error(), _make_completion("recovered")])
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(api_key="test-key", model="grok-3")
            response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(content="recovered", provider="xai", model="grok-3")
    assert create.await_count == 2
    mock_sleep.assert_awaited_once_with(1)
    assert client._provider_retry_after == {}


@pytest.mark.asyncio
async def test_normal_chat_ignores_sentiment_transient_cooldown() -> None:
    create = AsyncMock(return_value=_make_completion("chat is available"))
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="grok-3")
        client._provider_retry_after["primary"] = datetime.now(UTC) + timedelta(minutes=2)
        response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(content="chat is available", provider="xai", model="grok-3")
    create.assert_awaited_once()
    assert "primary" in client._provider_retry_after


@pytest.mark.asyncio
async def test_normal_chat_transient_exhaustion_does_not_set_sentiment_cooldown() -> None:
    create = AsyncMock(
        side_effect=[_make_connection_error(), _make_connection_error(), _make_connection_error()]
    )
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock):
            client = XAIClient(api_key="test-key", model="grok-3")
            with pytest.raises(openai.APIConnectionError):
                await client.chat([{"role": "user", "content": "ping"}])

    assert create.await_count == 3
    assert client._provider_retry_after == {}


@pytest.mark.asyncio
async def test_chat_with_validator_uses_one_primary_attempt() -> None:
    """Sentiment validation falls through after a single bad provider attempt."""
    create = AsyncMock(side_effect=_make_connection_error())
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(api_key="test-key", model="grok-3")
            with pytest.raises(openai.APIConnectionError):
                await client.chat_with_validator(
                    [{"role": "user", "content": "ping"}], lambda content: None
                )

    create.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_validator_reports_provider_unavailability_during_cooldown() -> None:
    create = AsyncMock()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="glm-5.3-flash", provider="zai")
        client._provider_retry_after["primary"] = datetime.now(UTC) + timedelta(minutes=2)
        with pytest.raises(
            RuntimeError, match="No configured AI provider is currently available"
        ) as raised:
            await client.chat_with_validator(
                [{"role": "user", "content": "sentiment"}], lambda _: None
            )

    assert raised.value.provider == "provider-chain"
    assert raised.value.model == "none"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_sentiment_chain_has_overall_deadline() -> None:
    fake_client = SimpleNamespace()

    async def slow_request(*args, **kwargs) -> str:
        await asyncio.sleep(1)
        return "{}"

    with patch("src.overseer.xai._SENTIMENT_CHAIN_TIMEOUT_SECONDS", 0.05):
        with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
            client = XAIClient(api_key="test-key", model="glm-5.3-flash", provider="zai")
            slow_call = AsyncMock(side_effect=slow_request)
            with patch.object(client, "_chat_with_client", slow_call):
                with pytest.raises(TimeoutError, match="provider chain exceeded") as raised:
                    await client.chat_with_validator(
                        [{"role": "user", "content": "sentiment"}], lambda _: None
                    )

    slow_call.assert_awaited_once()
    assert raised.value.provider == "provider-chain"
    assert raised.value.model == "chain"


@pytest.mark.asyncio
async def test_chat_uses_fallback_provider_after_primary_transient_failure() -> None:
    primary_create = AsyncMock(side_effect=_make_connection_error())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )

    fallback_create = AsyncMock(return_value=_make_completion("fallback-ok"))
    fallback_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, fallback_client],
    ) as mock_openai:
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(
                api_key="xai-key",
                model="grok-3",
                fallback_api_key="deepseek-key",
                fallback_model="deepseek-chat",
            )
            response = await client.chat_with_validator(
                [{"role": "user", "content": "ping"}], lambda content: None
            )

    assert response == ChatResult(content="fallback-ok", provider="deepseek", model="deepseek-chat")
    primary_create.assert_awaited_once()
    fallback_create.assert_awaited_once()
    assert mock_openai.call_count == 2
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_does_not_retry_on_auth_error() -> None:
    """Authentication errors are not retryable and should propagate immediately."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.headers = {}
    auth_error = openai.AuthenticationError("invalid api key", response=mock_response, body=None)
    create = AsyncMock(side_effect=auth_error)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(api_key="bad-key", model="grok-3")
            with pytest.raises(openai.AuthenticationError):
                await client.chat([{"role": "user", "content": "ping"}])

    assert create.await_count == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_chat_retries_transient_fallback_error() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    fallback_create = AsyncMock(
        side_effect=[_make_connection_error(), _make_completion("fallback recovered")]
    )
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    fallback_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary_client, fallback_client]):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(
                api_key="primary-key",
                model="primary-model",
                fallback_api_key="deepseek-key",
                fallback_model="deepseek-chat",
            )
            response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(
        content="fallback recovered", provider="deepseek", model="deepseek-chat"
    )
    primary_create.assert_awaited_once()
    assert fallback_create.await_count == 2
    mock_sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_chat_falls_back_on_permission_denied_without_retry() -> None:
    """403 PermissionDenied is not retried; DeepSeek fallback is used when configured."""
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    fallback_create = AsyncMock(return_value=_make_completion("deepseek-ok"))
    fallback_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, fallback_client],
    ):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(
                api_key="xai-key",
                model="grok-3",
                fallback_api_key="deepseek-key",
                fallback_model="deepseek-v4-pro",
            )
            response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(
        content="deepseek-ok", provider="deepseek", model="deepseek-v4-pro"
    )
    assert primary_create.await_count == 1
    fallback_create.assert_awaited_once()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_skips_xai_after_permission_denied() -> None:
    """A 403 disables the xAI hop for later calls in this process."""
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    fallback_create = AsyncMock(side_effect=[_make_completion("first"), _make_completion("second")])
    fallback_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, fallback_client],
    ):
        client = XAIClient(
            api_key="xai-key",
            model="grok-3",
            fallback_api_key="deepseek-key",
            fallback_model="deepseek-v4-pro",
        )
        first = await client.chat([{"role": "user", "content": "one"}])
        second = await client.chat([{"role": "user", "content": "two"}])

    assert first == ChatResult(content="first", provider="deepseek", model="deepseek-v4-pro")
    assert second == ChatResult(content="second", provider="deepseek", model="deepseek-v4-pro")
    assert primary_create.await_count == 1
    assert fallback_create.await_count == 2


@pytest.mark.asyncio
async def test_invalid_response_uses_next_provider() -> None:
    primary_create = AsyncMock(return_value=_make_completion('{"score": true}'))
    fallback_create = AsyncMock(return_value=_make_completion('{"score": 42}'))
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    fallback_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, fallback_client],
    ):
        client = XAIClient(
            api_key="primary-key",
            model="primary-model",
            fallback_api_key="deepseek-key",
            fallback_model="deepseek-chat",
        )
        result = await client.chat_with_validator(
            [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
        )

    assert result == ChatResult(content='{"score": 42}', provider="deepseek", model="deepseek-chat")
    primary_create.assert_awaited_once()
    fallback_create.assert_awaited_once()
    assert primary_create.await_args is not None
    assert primary_create.await_args.kwargs["timeout"] == 10.0
    assert fallback_create.await_args is not None
    assert fallback_create.await_args.kwargs["timeout"] == 10.0
    assert "primary" in client._provider_retry_after


@pytest.mark.asyncio
async def test_zai_chain_uses_configured_deepseek_after_other_fallbacks_fail() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    ollama_create = AsyncMock(return_value=_make_completion("not-json"))
    nvidia_create = AsyncMock(side_effect=_make_connection_error())
    deepseek_create = AsyncMock(return_value=_make_completion('{"score": 61}'))
    clients = [
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        for create in (primary_create, deepseek_create, ollama_create, nvidia_create)
    ]

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            fallback_api_key="deepseek-key",
            fallback_model="deepseek-v4-pro",
            ollama_api_key="ollama-key",
            nvidia_api_key="nvidia-key",
        )
        result = await client.chat_with_validator(
            [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
        )

    assert result == ChatResult(
        content='{"score": 61}', provider="deepseek", model="deepseek-v4-pro"
    )
    primary_create.assert_awaited_once()
    ollama_create.assert_awaited_once()
    nvidia_create.assert_awaited_once()
    deepseek_create.assert_awaited_once()
    assert primary_create.await_args is not None
    assert "response_format" not in primary_create.await_args.kwargs
    assert ollama_create.await_args is not None
    assert ollama_create.await_args.kwargs["response_format"] == {"type": "json_object"}
    assert ollama_create.await_args.kwargs["max_tokens"] == 256
    assert deepseek_create.await_args is not None
    assert deepseek_create.await_args.kwargs["response_format"] == {"type": "json_object"}
    assert deepseek_create.await_args.kwargs["max_tokens"] == 256


@pytest.mark.asyncio
async def test_exhausted_chain_preserves_last_failing_provider_identity() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    ollama_create = AsyncMock(return_value=_make_completion("not-json"))
    nvidia_error = _make_connection_error()
    nvidia_create = AsyncMock(side_effect=nvidia_error)
    clients = [
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        for create in (primary_create, ollama_create, nvidia_create)
    ]

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            ollama_api_key="ollama-key",
            nvidia_api_key="nvidia-key",
        )
        with pytest.raises(openai.APIConnectionError) as raised:
            await client.chat_with_validator(
                [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
            )

    assert raised.value.provider == "nvidia"
    assert raised.value.model == "minimaxai/minimax-m3.1-flash"
    assert str(raised.value) == str(nvidia_error)


@pytest.mark.asyncio
async def test_disabled_opencode_does_not_construct_catalog_client() -> None:
    fake_client = SimpleNamespace()
    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client) as mock_openai:
        client = XAIClient(
            api_key="primary-key",
            model="primary-model",
            provider="zai",
            opencode_go_api_key="",
        )

    mock_openai.assert_called_once()
    assert client._opencode_client is None


@pytest.mark.asyncio
async def test_sentiment_timeout_bounds_opencode_catalog_lookup() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    catalog_list = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="free-model")]))
    opencode_create = AsyncMock(return_value=_make_completion('{"score": 42}'))
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    opencode_client = SimpleNamespace(
        models=SimpleNamespace(list=catalog_list),
        chat=SimpleNamespace(completions=SimpleNamespace(create=opencode_create)),
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, opencode_client],
    ):
        client = XAIClient(
            api_key="primary-key",
            model="primary-model",
            provider="zai",
            opencode_go_api_key="opencode-key",
        )
        result = await client.chat_with_validator(
            [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
        )

    assert result == ChatResult(content='{"score": 42}', provider="opencode-go", model="free-model")
    catalog_list.assert_awaited_once_with(timeout=10.0)
    assert opencode_create.await_args is not None
    assert opencode_create.await_args.kwargs["timeout"] == 10.0


@pytest.mark.asyncio
async def test_sentiment_nvidia_request_timeout_leaves_room_for_alternate() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    nvidia_create = AsyncMock(return_value=_make_completion('{"score": 67}'))
    clients = [
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        for create in (primary_create, nvidia_create)
    ]

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            nvidia_api_key="nvidia-key",
        )
        response = await client.chat_with_validator(
            [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
        )

    assert response == ChatResult(
        content='{"score": 67}', provider="nvidia", model="minimaxai/minimax-m3.1-flash"
    )
    primary_create.assert_awaited_once()
    nvidia_create.assert_awaited_once()
    assert primary_create.await_args is not None
    assert primary_create.await_args.kwargs["timeout"] == 10.0
    assert nvidia_create.await_args is not None
    assert nvidia_create.await_args.kwargs["timeout"] == 20.0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "invalid-json"])
async def test_nvidia_model_specific_failure_uses_one_alternate_model(failure: str) -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    first_nvidia_create = AsyncMock(
        side_effect=[
            openai.APITimeoutError(request=MagicMock())
            if failure == "timeout"
            else _make_completion("not-json"),
            _make_completion('{"score": 73}'),
        ]
    )
    clients = [
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        for create in (primary_create, first_nvidia_create)
    ]

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            nvidia_api_key="nvidia-key",
        )
        result = await client.chat_with_validator(
            [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
        )

    assert result == ChatResult(
        content='{"score": 73}', provider="nvidia", model="nvidia/nemotron-3.5-bolt-e4b"
    )
    primary_create.assert_awaited_once()
    assert first_nvidia_create.await_count == 2
    assert first_nvidia_create.await_args_list[0].kwargs["model"] == "minimaxai/minimax-m3.1-flash"
    assert first_nvidia_create.await_args_list[1].kwargs["model"] == "nvidia/nemotron-3.5-bolt-e4b"
    assert all(call.kwargs["timeout"] == 20.0 for call in first_nvidia_create.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rate-limit", "auth", "network"])
async def test_nvidia_account_or_network_failure_does_not_try_another_model(failure: str) -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    if failure == "rate-limit":
        response = MagicMock()
        response.status_code = 429
        response.headers = {}
        nvidia_error = openai.RateLimitError("rate limited", response=response, body=None)
    elif failure == "auth":
        response = MagicMock()
        response.status_code = 401
        response.headers = {}
        nvidia_error = openai.AuthenticationError("invalid key", response=response, body=None)
    else:
        nvidia_error = _make_connection_error()
    nvidia_create = AsyncMock(side_effect=nvidia_error)
    clients = [
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        for create in (primary_create, nvidia_create)
    ]

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=clients):
        client = XAIClient(
            api_key="zai-key", model="glm-5.3-flash", provider="zai", nvidia_api_key="nvidia-key"
        )
        with pytest.raises(type(nvidia_error)):
            await client.chat_with_validator(
                [{"role": "user", "content": "sentiment"}], _decode_sentiment_score
            )

    primary_create.assert_awaited_once()
    nvidia_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_with_client_supports_single_attempt_fallback() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    rate_limit = openai.RateLimitError(
        "rate limit", response=mock_response, body={"error": {"message": "rate limit"}}
    )
    create = AsyncMock(side_effect=rate_limit)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="test-model")

    with pytest.raises(openai.RateLimitError):
        await client._chat_with_client(
            cast(AsyncOpenAI, fake_client),
            "test-model",
            [],
            provider_name="fallback",
            max_attempts=1,
        )

    create.assert_awaited_once()


def test_provider_failures_recover_and_rolling_quota_is_not_permanent() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    rate_limit = openai.RateLimitError(
        "quota exceeded", response=mock_response, body={"error": {"message": "quota exceeded"}}
    )

    assert _is_permanent_provider_error(rate_limit) is False
    assert _is_permanent_provider_error(_make_insufficient_balance()) is True

    class StatusError(Exception):
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            super().__init__(f"HTTP {status_code}")

    assert _is_permanent_provider_error(StatusError(400)) is False
    assert _is_permanent_provider_error(StatusError(404)) is False

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
    )
    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="test-model")

    client._record_provider_failure("primary", rate_limit)
    assert "primary" not in client._provider_disabled_dates
    retry_delay = client._provider_retry_after["primary"] - datetime.now(UTC)
    assert 119 <= retry_delay.total_seconds() <= 120
    assert client._provider_available("primary") is False
    client._provider_retry_after["primary"] = datetime.now(UTC) - timedelta(seconds=1)
    assert client._provider_available("primary") is True

    client._record_provider_failure("primary", _make_insufficient_balance())
    assert client._provider_available("primary") is False
    client._provider_disabled_dates["primary"] = datetime.now(UTC).date() - timedelta(days=1)
    assert client._provider_available("primary") is True


@pytest.mark.asyncio
async def test_deepseek_primary_does_not_call_xai() -> None:
    """Configured DeepSeek primary talks only to the DeepSeek base URL."""
    create = AsyncMock(return_value=_make_completion("deepseek-primary"))
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client) as mock_openai:
        client = XAIClient(
            api_key="deepseek-key",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            provider="deepseek",
        )
        response = await client.chat([{"role": "user", "content": "status?"}])

    mock_openai.assert_called_once_with(
        api_key="deepseek-key",
        base_url="https://api.deepseek.com/v1",
        timeout=30.0,
        max_retries=0,
    )
    assert response == ChatResult(
        content="deepseek-primary", provider="deepseek", model="deepseek-v4-pro"
    )


@pytest.mark.asyncio
async def test_chat_uses_daily_discovered_opencode_free_fallback() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    fallback_create = AsyncMock(return_value=_make_completion("free-fallback-ok"))
    fallback_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create)),
        models=SimpleNamespace(
            list=AsyncMock(
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(id="paid-model"),
                        SimpleNamespace(id="beam-free"),
                    ]
                )
            )
        ),
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, fallback_client],
    ):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            base_url="https://api.z.ai/api/paas/v4",
            opencode_go_api_key="opencode-test-key",
        )
        response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(
        content="free-fallback-ok", provider="opencode-go", model="beam-free"
    )
    fallback_client.models.list.assert_awaited_once()
    fallback_create.assert_awaited_once_with(
        model="beam-free",
        messages=[{"role": "user", "content": "ping"}],
        temperature=0.2,
    )


@pytest.mark.asyncio
async def test_opencode_catalog_refreshes_daily_and_never_selects_paid_models() -> None:
    model_list = AsyncMock(
        side_effect=[
            SimpleNamespace(data=[SimpleNamespace(id="beam-free"), SimpleNamespace(id="paid")]),
            SimpleNamespace(data=[SimpleNamespace(id="new-free-model")]),
        ]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
        models=SimpleNamespace(list=model_list),
    )

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            opencode_go_api_key="opencode-test-key",
        )
        first = await client._resolve_opencode_free_model()
        assert await client._resolve_opencode_free_model() == first
        assert model_list.await_count == 1
        client._opencode_model_date = datetime.now(UTC).date() - timedelta(days=1)
        second = await client._resolve_opencode_free_model()

    assert first == "beam-free"
    assert second == "new-free-model"
    assert model_list.await_count == 2


@pytest.mark.asyncio
async def test_opencode_catalog_without_free_models_fails_closed() -> None:
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="paid-model")]))
        ),
    )
    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            opencode_go_api_key="opencode-test-key",
        )
        with pytest.raises(RuntimeError, match="no advertised free models"):
            await client._resolve_opencode_free_model()
        with pytest.raises(RuntimeError, match="no advertised free models"):
            await client._resolve_opencode_free_model()

    fake_client.models.list.assert_awaited_once()
    fake_client.chat.completions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_zai_tries_opencode_then_xai_fallback() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    opencode_create = AsyncMock(side_effect=_make_permission_denied())
    opencode_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=opencode_create)),
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="beam-free")]))
        ),
    )
    xai_create = AsyncMock(return_value=_make_completion("xai-fallback-ok"))
    xai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=xai_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, opencode_client, xai_client],
    ):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            opencode_go_api_key="opencode-test-key",
            xai_fallback_api_key="xai-test-key",
        )
        response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(
        content="xai-fallback-ok", provider="xai-fallback", model="grok-4-1-fast-reasoning"
    )
    primary_create.assert_awaited_once()
    opencode_create.assert_awaited_once()
    xai_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_primary_insufficient_balance_skips_retries_and_opencode_model_list_is_cached():
    primary_create = AsyncMock(side_effect=_make_insufficient_balance())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    fallback_create = AsyncMock(
        side_effect=[_make_completion("free-one"), _make_completion("free-two")]
    )
    catalog_list = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="beam-free")]))
    opencode_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create)),
        models=SimpleNamespace(list=catalog_list),
    )

    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary_client, opencode_client]):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(
                api_key="zai-key",
                model="glm-5.3-flash",
                provider="zai",
                opencode_go_api_key="opencode-test-key",
            )
            first = await client.chat([{"role": "user", "content": "one"}])
            second = await client.chat([{"role": "user", "content": "two"}])

    assert first.provider == "opencode-go"
    assert second.provider == "opencode-go"
    primary_create.assert_awaited_once()
    catalog_list.assert_awaited_once()
    assert fallback_create.await_count == 2
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_zai_falls_back_to_ollama_cloud_before_nvidia_nim() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    ollama_create = AsyncMock(return_value=_make_completion("ollama-answer"))
    ollama_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=ollama_create))
    )
    nvidia_create = AsyncMock(return_value=_make_completion("nvidia-answer"))
    nvidia_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=nvidia_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, ollama_client, nvidia_client],
    ):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            ollama_api_key="ollama-test-key",
            nvidia_api_key="nvidia-test-key",
        )
        response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(content="ollama-answer", provider="ollama", model="gemma4:31b")
    primary_create.assert_awaited_once()
    ollama_create.assert_awaited_once()
    nvidia_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_nvidia_nim_fallback_is_used_when_ollama_fails() -> None:
    primary_create = AsyncMock(side_effect=_make_permission_denied())
    primary_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create))
    )
    ollama_create = AsyncMock(side_effect=_make_permission_denied())
    ollama_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=ollama_create))
    )
    nvidia_create = AsyncMock(return_value=_make_completion("nvidia-answer"))
    nvidia_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=nvidia_create))
    )

    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[primary_client, ollama_client, nvidia_client],
    ):
        client = XAIClient(
            api_key="zai-key",
            model="glm-5.3-flash",
            provider="zai",
            ollama_api_key="ollama-test-key",
            nvidia_api_key="nvidia-test-key",
        )
        response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(
        content="nvidia-answer",
        provider="nvidia",
        model="minimaxai/minimax-m3.1-flash",
    )
    ollama_create.assert_awaited_once()
    nvidia_create.assert_awaited_once()


def _make_http_error(error_type, status: int):
    response = MagicMock()
    response.status_code = status
    response.headers = {}
    return error_type("model unavailable", response=response, body=None)


@pytest.mark.asyncio
async def test_nvidia_model_not_found_tries_catalog_verified_alternative() -> None:
    response = MagicMock(status_code=404, headers={})
    model_not_found = openai.NotFoundError(
        "Function missing/model Not found for account test-account",
        response=response,
        body={"error": {"message": "Function missing/model Not found for account test-account"}},
    )
    create = AsyncMock(side_effect=[model_not_found, _make_completion("answer")])
    client_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            client_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="missing/model"
        )
        result = await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    assert result == ChatResult("answer", "nvidia", "nvidia/nemotron-3.5-bolt-e4b")
    assert [call.kwargs["model"] for call in create.await_args_list] == [
        "missing/model",
        "nvidia/nemotron-3.5-bolt-e4b",
    ]
    assert model_not_found.response is response


@pytest.mark.asyncio
async def test_nvidia_generic_route_404_does_not_try_other_models() -> None:
    response = MagicMock(status_code=404, headers={})
    route_error = openai.NotFoundError(
        "Not Found", response=response, body={"error": {"message": "route not found"}}
    )
    create = AsyncMock(side_effect=route_error)
    nvidia_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            nvidia_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="configured/model"
        )
        with pytest.raises(openai.NotFoundError) as raised:
            await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    assert raised.value is route_error
    assert raised.value.response is response
    create.assert_awaited_once()
    assert create.await_args is not None
    assert create.await_args.kwargs["model"] == "configured/model"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "malformed"])
async def test_nvidia_timeout_and_malformed_response_do_not_try_other_models(failure: str) -> None:
    if failure == "timeout":
        create = AsyncMock(side_effect=openai.APITimeoutError(request=MagicMock()))
        expected_error = openai.APITimeoutError
    else:
        create = AsyncMock(return_value=_make_completion("  "))
        expected_error = RuntimeError
    nvidia_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            nvidia_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="configured/model"
        )
        with pytest.raises(expected_error):
            await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    create.assert_awaited_once()
    assert create.await_args is not None
    assert create.await_args.kwargs["model"] == "configured/model"


@pytest.mark.asyncio
async def test_nvidia_request_wide_bad_request_does_not_try_other_models() -> None:
    request_error = openai.BadRequestError(
        "Invalid request: prompt is too long",
        response=MagicMock(status_code=400, headers={}),
        body={"error": {"code": "invalid_request", "message": "prompt is too long"}},
    )
    create = AsyncMock(side_effect=request_error)
    nvidia_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            nvidia_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="configured/model"
        )
        with pytest.raises(openai.BadRequestError):
            await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    create.assert_awaited_once()
    assert create.await_args is not None
    assert create.await_args.kwargs["model"] == "configured/model"


@pytest.mark.asyncio
async def test_nvidia_model_specific_bad_request_tries_alternative() -> None:
    model_error = openai.BadRequestError(
        "Model is not supported",
        response=MagicMock(status_code=400, headers={}),
        body={"error": {"code": "model_not_supported", "message": "Model is not supported"}},
    )
    create = AsyncMock(side_effect=[model_error, _make_completion("answer")])
    nvidia_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            nvidia_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="configured/model"
        )
        result = await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    assert result.provider == "nvidia"
    assert [call.kwargs["model"] for call in create.await_args_list] == [
        "configured/model",
        "nvidia/nemotron-3.5-bolt-e4b",
    ]


@pytest.mark.asyncio
async def test_nvidia_auth_error_does_not_try_other_models() -> None:
    auth_error = _make_permission_denied()
    create = AsyncMock(side_effect=auth_error)
    nvidia_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            nvidia_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="configured/model"
        )
        with pytest.raises(openai.PermissionDeniedError):
            await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    create.assert_awaited_once()
    assert create.await_args is not None
    assert create.await_args.kwargs["model"] == "configured/model"


@pytest.mark.asyncio
async def test_nvidia_account_rate_limit_does_not_try_other_models() -> None:
    create = AsyncMock(side_effect=_make_insufficient_balance())
    nvidia_stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with patch(
        "src.overseer.xai.AsyncOpenAI",
        side_effect=[
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=AsyncMock(side_effect=_make_permission_denied())
                    )
                )
            ),
            nvidia_stub,
        ],
    ):
        client = XAIClient(
            api_key="x", model="primary", nvidia_api_key="n", nvidia_model="configured/model"
        )
        with pytest.raises(openai.RateLimitError):
            await client.chat([{"role": "user", "content": "ping"}], max_attempts=1)
    create.assert_awaited_once()
