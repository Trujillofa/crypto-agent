import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from src.overseer.xai import XAIClient
from src.strategy.sentiment_mean_reversion import _decode_sentiment_score


def response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def provider(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def error(status):
    cls = openai.RateLimitError if status == 429 else openai.InternalServerError
    return cls("transient", response=MagicMock(status_code=status, headers={}), body=None)


def make_client(command_create):
    primary = provider(AsyncMock(return_value=response("invalid")))
    command = provider(command_create)
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, command]):
        return XAIClient(api_key="p", model="p", commandcode_api_key="c", commandcode_model="m")


async def test_503_once_recovers_and_preserves_attribution():
    create = AsyncMock(side_effect=[error(503), response('{"score": 68}')])
    client = make_client(create)
    with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock) as sleep:
        result = await client.chat_with_validator([], _decode_sentiment_score)
    assert (result.provider, result.model, _decode_sentiment_score(result.content)) == (
        "commandcode",
        "m",
        68,
    )
    assert create.await_count == 2
    sleep.assert_awaited_once_with(1)
    assert "commandcode" not in client._provider_retry_after


@pytest.mark.parametrize("status,attempts", [(503, 2), (429, 1), (500, 1)])
async def test_only_503_retries_and_failure_cools_down(status, attempts):
    create = AsyncMock(side_effect=error(status))
    client = make_client(create)
    with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="Command Code fallback failed"):
            await client.chat_with_validator([], _decode_sentiment_score)
    assert create.await_count == attempts
    assert not client._provider_available("commandcode")


async def test_deadline_during_503_backoff_attributes_and_cools_provider():
    create = AsyncMock(side_effect=error(503))
    client = make_client(create)
    with (
        patch("src.overseer.xai._SENTIMENT_CHAIN_TIMEOUT_SECONDS", 0.05),
        patch("src.overseer.xai._COMMANDCODE_RESERVED_SECONDS", 0.02),
    ):
        with pytest.raises(TimeoutError) as raised:
            await client.chat_with_validator([], _decode_sentiment_score)
    assert (raised.value.provider, raised.value.model) == ("commandcode", "m")
    assert create.await_count == 1
    assert not client._provider_available("commandcode")


async def test_caller_cancellation_during_backoff_does_not_cool_provider():
    create = AsyncMock(side_effect=error(503))
    client = make_client(create)
    entered = asyncio.Event()

    async def wait(delay):
        entered.set()
        await asyncio.Event().wait()

    with patch("src.overseer.xai.asyncio.sleep", side_effect=wait):
        task = asyncio.create_task(client.chat_with_validator([], _decode_sentiment_score))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client._provider_available("commandcode")
    assert create.await_count == 1
