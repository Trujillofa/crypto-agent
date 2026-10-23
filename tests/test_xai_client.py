from datetime import UTC, datetime, timedelta
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
        content="xai-fallback-ok", provider="xai", model="grok-4-1-fast-reasoning"
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
        model="nvidia/nemotron-3.5-lightning-30b-a3b",
    )
    ollama_create.assert_awaited_once()
    nvidia_create.assert_awaited_once()
