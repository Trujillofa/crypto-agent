from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.overseer.xai import XAIClient


@pytest.mark.asyncio
async def test_chat_returns_trimmed_content() -> None:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="  market regime stable  \n"))]
    )
    create = AsyncMock(return_value=completion)
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client) as mock_openai:
        client = XAIClient(api_key="test-key", model="grok-3")
        response = await client.chat([{"role": "user", "content": "status?"}])

    mock_openai.assert_called_once_with(
        api_key="test-key",
        base_url="https://api.x.ai/v1",
        timeout=30.0,
    )
    create.assert_awaited_once_with(
        model="grok-3",
        messages=[{"role": "user", "content": "status?"}],
        temperature=0.2,
    )
    assert response == "market regime stable"


@pytest.mark.asyncio
async def test_chat_returns_fallback_when_content_missing() -> None:
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=[]))])
    create = AsyncMock(return_value=completion)
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with patch("src.overseer.xai.AsyncOpenAI", return_value=fake_client):
        client = XAIClient(api_key="test-key", model="grok-3")
        response = await client.chat([{"role": "user", "content": "status?"}])

    assert response == "No response content returned by xAI API."
