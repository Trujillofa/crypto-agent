from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.main import AISettings, _build_zai_sentiment_client
from src.overseer.agent import OverseerAgent
from src.overseer.xai import XAIClient
from src.strategy.sentiment_mean_reversion import _decode_sentiment_score


def transport(fail=False):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("synthetic failure") if fail else None)
            )
        ),
        models=SimpleNamespace(list=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_ask_primary_failure_never_sends_context_to_go():
    primary, go = transport(True), transport()
    with patch("src.overseer.xai.AsyncOpenAI", side_effect=[primary, go]):
        client = XAIClient(api_key="fake", model="fake", opencode_go_api_key="fake")
    agent = OverseerAgent(
        mode="paper",
        poll_interval_seconds=1,
        max_history=2,
        allowed_chat_ids=[],
        telegram=None,
        portfolio_manager=None,
        risk_manager=None,
        xai_client=client,
    )
    agent._build_context = AsyncMock(return_value={"portfolio": "synthetic private position"})
    with patch("src.overseer.xai.asyncio.sleep", new_callable=AsyncMock):
        reply = await agent._cmd_ask("fake", "synthetic private question")
    assert reply == "AI request failed. Try again in a few seconds."
    assert primary.chat.completions.create.await_count > 0
    go.models.list.assert_not_awaited()
    go.chat.completions.create.assert_not_awaited()
    assert agent._chat_history == {}


@pytest.mark.asyncio
async def test_enabled_config_still_blocks_go_during_sentiment_primary_failure():
    primary = transport(True)
    settings = AISettings(
        enabled=True,
        provider="zai",
        model="fake",
        polling_interval=60,
        max_history=0,
        allowed_chat_ids=[],
        api_key="",
        zai_api_key="fake",
        opencode_go_api_key="fake",
        opencode_go_enabled=True,
    )
    with patch("src.overseer.xai.AsyncOpenAI", return_value=primary) as factory:
        client = _build_zai_sentiment_client(settings)
    assert client is not None
    assert client._opencode_client is None
    assert factory.call_count == 1
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await client.chat_with_validator(
            [{"role": "user", "content": "synthetic"}], _decode_sentiment_score
        )
    assert primary.chat.completions.create.await_count == 1
