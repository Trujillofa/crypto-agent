from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.notifications.telegram import TelegramPollingConflict
from src.overseer.agent import OverseerAgent


@pytest.fixture
def mock_telegram():
    telegram = AsyncMock()
    telegram.get_updates = AsyncMock(return_value=[])
    telegram.send_alert = AsyncMock()
    return telegram


@pytest.fixture
def mock_portfolio_manager():
    pm = MagicMock()
    pm.get_portfolio_summary = AsyncMock(
        return_value=MagicMock(
            open_positions=2,
            total_trades=10,
            total_realized_pnl=150.5,
        )
    )
    pm.get_all_positions = MagicMock(
        return_value=[
            MagicMock(symbol="BTCUSDT", quantity=0.5, entry_price=45000.0),
            MagicMock(symbol="ETHUSDT", quantity=2.0, entry_price=3000.0),
        ]
    )
    return pm


@pytest.fixture
def mock_risk_manager():
    rm = MagicMock()
    rm.get_risk_summary = MagicMock(
        return_value={
            "kill_switch_active": False,
            "circuit_breakers": {"daily_loss": False, "consecutive_losses": False},
            "daily_pnl": 100.0,
            "consecutive_losses": 0,
            "api_errors": 0,
            "avg_latency_ms": 50.0,
        }
    )
    rm.clear_trading_blocks = MagicMock()
    return rm


@pytest.fixture
def overseer(mock_telegram, mock_portfolio_manager, mock_risk_manager):
    return OverseerAgent(
        mode="paper",
        poll_interval_seconds=1.0,
        max_history=10,
        allowed_chat_ids=["123456"],
        telegram=mock_telegram,
        portfolio_manager=mock_portfolio_manager,
        risk_manager=mock_risk_manager,
        xai_client=None,
    )


class TestOverseerAgent:
    def test_initialization(self, overseer):
        assert overseer._mode == "paper"
        assert overseer._poll_interval_seconds == 1.0
        assert overseer._max_history == 10
        assert overseer._running is False

    def test_stop(self, overseer):
        overseer._running = True
        overseer.stop()
        assert overseer._running is False

    @pytest.mark.asyncio
    async def test_handle_update_unauthorized_chat(self, overseer, mock_telegram):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 999999},
                "from": {"is_bot": False},
                "text": "/status",
            },
        }
        await overseer._handle_update(update)
        mock_telegram.send_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_update_bot_message_ignored(self, overseer, mock_telegram):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123456},
                "from": {"is_bot": True},
                "text": "/status",
            },
        }
        await overseer._handle_update(update)
        mock_telegram.send_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_status(self, overseer, mock_telegram):
        timestamp = datetime(2026, 3, 4, 15, 25, tzinfo=UTC)
        overseer._portfolio_manager.get_portfolio_summary.return_value.last_trade_time = timestamp
        result = await overseer._cmd_status()
        assert "default" in result
        assert "PAPER" in result
        assert "Kill Switch: OFF" in result
        assert "Positions: 2 open" in result
        assert "2026-03-04 15:25 UTC" in result
        assert "10 trades" in result
        assert "+150.50 USDT" in result

    @pytest.mark.asyncio
    async def test_cmd_risk(self, overseer):
        result = overseer._cmd_risk()
        assert "Risk Snapshot" in result
        assert "Kill Switch: OFF" in result

    @pytest.mark.asyncio
    async def test_cmd_positions(self, overseer):
        result = overseer._cmd_positions()
        assert "Open Positions" in result
        assert "BTCUSDT" in result
        assert "ETHUSDT" in result

    @pytest.mark.asyncio
    async def test_cmd_reset_no_blocks(self, overseer, mock_risk_manager):
        mock_risk_manager.get_risk_summary.return_value = {
            "kill_switch_active": False,
            "circuit_breakers": {},
        }
        result = overseer._cmd_reset()
        assert "already allowed" in result
        mock_risk_manager.clear_trading_blocks.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_reset_with_blocks(self, overseer, mock_risk_manager):
        mock_risk_manager.get_risk_summary.return_value = {
            "kill_switch_active": True,
            "circuit_breakers": {"daily_loss": True},
        }
        result = overseer._cmd_reset()
        assert "Risk Reset Complete" in result
        mock_risk_manager.clear_trading_blocks.assert_called_once()

    def test_help_text(self, overseer):
        result = overseer._help_text()
        assert "Crypto Agent Commands" in result
        assert "/status" in result
        assert "/risk" in result
        assert "/ask" in result
        assert "/help" in result

    @pytest.mark.asyncio
    async def test_handle_status_command(self, overseer, mock_telegram):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123456},
                "from": {"is_bot": False},
                "text": "/status",
            },
        }
        await overseer._handle_update(update)
        mock_telegram.send_alert.assert_called_once()
        call_args = mock_telegram.send_alert.call_args
        assert call_args[1]["chat_id"] == "123456"
        assert "default" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_help_command(self, overseer, mock_telegram):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123456},
                "from": {"is_bot": False},
                "text": "/help",
            },
        }
        await overseer._handle_update(update)
        mock_telegram.send_alert.assert_called_once()
        call_args = mock_telegram.send_alert.call_args
        assert "Crypto Agent Commands" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self, overseer, mock_telegram):
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 123456},
                "from": {"is_bot": False},
                "text": "hello world",
            },
        }
        await overseer._handle_update(update)
        mock_telegram.send_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_stops_on_telegram_polling_conflict(self, overseer, mock_telegram):
        mock_telegram.get_updates.side_effect = TelegramPollingConflict("conflict")

        await overseer.run()

        assert overseer._running is False
        mock_telegram.get_updates.assert_awaited_once()
