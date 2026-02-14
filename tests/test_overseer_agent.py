from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.overseer.agent import OverseerAgent
from src.portfolio.models import PortfolioSummary


def _risk_summary() -> dict[str, object]:
    return {
        "kill_switch_active": False,
        "daily_pnl": 0.0,
        "consecutive_losses": 0,
        "api_errors": 0,
        "avg_latency_ms": 0.0,
        "circuit_breakers": {
            "drawdown": False,
            "consecutive_losses": False,
        },
    }


def _make_agent(
    telegram: object,
    *,
    max_history: int = 10,
    max_tracked_chats: int = 3,
) -> OverseerAgent:
    portfolio = AsyncMock()
    portfolio.get_portfolio_summary = AsyncMock(return_value=PortfolioSummary())
    portfolio.get_all_positions = AsyncMock(return_value=[])

    risk = AsyncMock()
    risk.get_risk_summary.return_value = _risk_summary()

    return OverseerAgent(
        mode="paper",
        poll_interval_seconds=0.2,
        max_history=max_history,
        allowed_chat_ids=["100"],
        telegram=telegram,
        portfolio_manager=portfolio,
        risk_manager=risk,
        xai_client=None,
        max_tracked_chats=max_tracked_chats,
    )


@pytest.mark.asyncio
async def test_unauthorized_chat_is_ignored() -> None:
    telegram = AsyncMock()
    telegram.send_alert = AsyncMock()
    telegram.get_updates = AsyncMock(return_value=[])
    agent = _make_agent(telegram)

    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": 999},
            "from": {"is_bot": False},
            "text": "/status",
        },
    }

    await agent._handle_update(update)
    telegram.send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_reply_bypasses_rate_limit() -> None:
    telegram = AsyncMock()
    telegram.send_alert = AsyncMock(return_value=True)
    telegram.get_updates = AsyncMock(return_value=[])
    agent = _make_agent(telegram)

    await agent._reply("100", "ok", as_html=True)

    telegram.send_alert.assert_called_once()
    _args, kwargs = telegram.send_alert.call_args
    assert kwargs["respect_rate_limit"] is False


def test_chat_history_evicts_oldest_chats() -> None:
    telegram = AsyncMock()
    telegram.send_alert = AsyncMock(return_value=True)
    telegram.get_updates = AsyncMock(return_value=[])
    agent = _make_agent(telegram, max_history=2, max_tracked_chats=2)

    agent._append_history("100", "user", "a")
    agent._append_history("200", "user", "b")
    agent._append_history("300", "user", "c")

    assert "100" not in agent._chat_history
    assert "200" in agent._chat_history
    assert "300" in agent._chat_history


@pytest.mark.asyncio
async def test_run_no_backoff_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = AsyncMock()
    telegram.send_alert = AsyncMock(return_value=True)

    agent = _make_agent(telegram)

    async def get_updates(*_args, **_kwargs):
        agent.stop()
        return []

    telegram.get_updates = get_updates

    sleep_mock = AsyncMock()
    monkeypatch.setattr("src.overseer.agent.asyncio.sleep", sleep_mock)

    await agent.run()
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_backoff_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = AsyncMock()
    telegram.send_alert = AsyncMock(return_value=True)

    agent = _make_agent(telegram)
    call_count = 0

    async def get_updates(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        agent.stop()
        return []

    telegram.get_updates = get_updates

    sleep_mock = AsyncMock()
    monkeypatch.setattr("src.overseer.agent.asyncio.sleep", sleep_mock)

    await agent.run()
    sleep_mock.assert_called_once()
