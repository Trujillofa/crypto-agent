from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from src.overseer.xai import ChatResult, XAIClient


def _make_completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _make_connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=MagicMock())


def _make_permission_denied() -> openai.PermissionDeniedError:
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.headers = {}
    return openai.PermissionDeniedError("forbidden", response=mock_response, body=None)


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
async def test_chat_returns_fallback_when_content_missing() -> None:
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=[]))])
    create = AsyncMock(return_value=completion)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="grok-3")
        response = await client.chat([{"role": "user", "content": "status?"}])

    assert response == ChatResult(
        content="No response content returned by xAI API.",
        provider="xai",
        model="grok-3",
    )


@pytest.mark.asyncio
async def test_chat_retries_on_transient_error_then_succeeds() -> None:
    """Fails once with a connection error then succeeds on the second attempt."""
    create = AsyncMock(side_effect=[_make_connection_error(), _make_completion("recovered")])
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(api_key="test-key", model="grok-3")
            response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(content="recovered", provider="xai", model="grok-3")
    assert create.await_count == 2
    mock_sleep.assert_awaited_once_with(1)  # 2**0 = 1s after first failure


@pytest.mark.asyncio
async def test_chat_raises_after_max_retries_exhausted() -> None:
    """Raises the last exception after all 3 attempts fail."""
    create = AsyncMock(
        side_effect=[_make_connection_error(), _make_connection_error(), _make_connection_error()]
    )
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            client = XAIClient(api_key="test-key", model="grok-3")
            with pytest.raises(openai.APIConnectionError):
                await client.chat([{"role": "user", "content": "ping"}])

    assert create.await_count == 3
    # Slept after attempt 1 (1s) and attempt 2 (2s), not after attempt 3
    assert mock_sleep.await_count == 2
    mock_sleep.assert_any_await(1)
    mock_sleep.assert_any_await(2)


@pytest.mark.asyncio
async def test_chat_uses_fallback_provider_after_xai_retries_exhausted() -> None:
    primary_create = AsyncMock(
        side_effect=[_make_connection_error(), _make_connection_error(), _make_connection_error()]
    )
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
            response = await client.chat([{"role": "user", "content": "ping"}])

    assert response == ChatResult(content="fallback-ok", provider="deepseek", model="deepseek-chat")
    assert primary_create.await_count == 3
    fallback_create.assert_awaited_once()
    assert mock_openai.call_count == 2
    mock_sleep.assert_any_await(1)
    mock_sleep.assert_any_await(2)


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
