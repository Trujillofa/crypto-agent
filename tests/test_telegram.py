"""Tests for notifications/telegram.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.notifications.telegram import AlertLevel, TelegramConfig, TelegramNotifier


@pytest.fixture
def telegram_config() -> TelegramConfig:
    """Create test Telegram config."""
    return TelegramConfig(
        bot_token="test_token",
        chat_id="test_chat_id",
        enabled=True,
        rate_limit_seconds=0,
    )


@pytest.fixture
def notifier(telegram_config: TelegramConfig) -> TelegramNotifier:
    """Create test notifier instance."""
    return TelegramNotifier(config=telegram_config)


class TestTelegramConfig:
    """Test suite for TelegramConfig."""

    def test_default_values(self) -> None:
        """Test default config values."""
        config = TelegramConfig(bot_token="token", chat_id="chat")
        assert config.enabled is True
        assert config.rate_limit_seconds == 5
        assert config.allowed_updates == ("message",)

    def test_custom_values(self) -> None:
        """Test custom config values."""
        config = TelegramConfig(
            bot_token="token",
            chat_id="chat",
            enabled=False,
            rate_limit_seconds=10,
            allowed_updates=("message", "callback_query"),
        )
        assert config.enabled is False
        assert config.rate_limit_seconds == 10
        assert config.allowed_updates == ("message", "callback_query")


class TestTelegramNotifier:
    """Test suite for TelegramNotifier."""

    def test_init_with_config(self, telegram_config: TelegramConfig) -> None:
        """Test initialization with config."""
        notifier = TelegramNotifier(config=telegram_config)
        assert notifier._config == telegram_config

    def test_init_from_env(self) -> None:
        """Test initialization from environment."""
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "env_token",
                "TELEGRAM_CHAT_ID": "env_chat",
                "TELEGRAM_ENABLED": "true",
            },
        ):
            notifier = TelegramNotifier()
            assert notifier._config.bot_token == "env_token"
            assert notifier._config.chat_id == "env_chat"

    def test_is_configured_true(self, notifier: TelegramNotifier) -> None:
        """Test is_configured returns True when configured."""
        assert notifier.is_configured() is True

    def test_is_configured_false_no_token(self) -> None:
        """Test is_configured returns False without token."""
        config = TelegramConfig(bot_token="", chat_id="chat", enabled=True)
        notifier = TelegramNotifier(config=config)
        assert notifier.is_configured() is False

    def test_is_configured_false_disabled(self) -> None:
        """Test is_configured returns False when disabled."""
        config = TelegramConfig(
            bot_token="token",
            chat_id="chat",
            enabled=False,
        )
        notifier = TelegramNotifier(config=config)
        assert notifier.is_configured() is False


class TestAsyncContextManager:
    """Test suite for async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_creates_session(self, notifier: TelegramNotifier) -> None:
        """Test __aenter__ creates aiohttp session."""
        assert notifier._session is None
        async with notifier:
            assert notifier._session is not None

    @pytest.mark.asyncio
    async def test_aexit_closes_session(self, notifier: TelegramNotifier) -> None:
        """Test __aexit__ closes aiohttp session."""
        async with notifier:
            assert notifier._session is not None
        assert notifier._session is None


class TestSendAlert:
    """Test suite for send_alert method."""

    @pytest.mark.asyncio
    async def test_send_alert_not_configured(self) -> None:
        """Test send_alert returns False when not configured."""
        config = TelegramConfig(bot_token="", chat_id="", enabled=False)
        notifier = TelegramNotifier(config=config)
        result = await notifier.send_alert("test message")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_alert_success(self, notifier: TelegramNotifier) -> None:
        """Test successful alert sending."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        async with notifier:
            with patch.object(notifier._session, "post", return_value=mock_response):
                result = await notifier.send_alert("test message")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_alert_failure(self, notifier: TelegramNotifier) -> None:
        """Test failed alert sending."""
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad Request")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        async with notifier:
            with patch.object(notifier._session, "post", return_value=mock_response):
                result = await notifier.send_alert("test message")

        assert result is False


class TestGetUpdates:
    @pytest.mark.asyncio
    async def test_get_updates_not_configured(self) -> None:
        config = TelegramConfig(bot_token="", chat_id="", enabled=False)
        notifier = TelegramNotifier(config=config)
        result = await notifier.get_updates()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_updates_success(self, notifier: TelegramNotifier) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"ok": True, "result": [{"update_id": 7}]})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        async with notifier:
            with patch.object(notifier._session, "post", return_value=mock_response):
                result = await notifier.get_updates(offset=6, timeout=5)

        assert result == [{"update_id": 7}]

    @pytest.mark.asyncio
    async def test_get_updates_api_error(self, notifier: TelegramNotifier) -> None:
        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        async with notifier:
            with patch.object(notifier._session, "post", return_value=mock_response):
                result = await notifier.get_updates()

        assert result == []


class TestSpecializedAlerts:
    """Test suite for specialized alert methods."""

    @pytest.mark.asyncio
    async def test_send_kill_switch_alert(self, notifier: TelegramNotifier) -> None:
        """Test kill switch alert format."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_kill_switch_alert("Test reason")
            mock_send.assert_called_once()
            args, _kwargs = mock_send.call_args
            assert "KILL SWITCH ACTIVATED" in args[0]
            assert "Test reason" in args[0]
            assert args[1] == AlertLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_send_circuit_breaker_alert(self, notifier: TelegramNotifier) -> None:
        """Test circuit breaker alert format."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_circuit_breaker_alert("consecutive_losses")
            mock_send.assert_called_once()
            args, _kwargs = mock_send.call_args
            assert "CIRCUIT BREAKER TRIGGERED" in args[0]
            assert "consecutive_losses" in args[0]
            assert args[1] == AlertLevel.WARNING

    @pytest.mark.asyncio
    async def test_send_trade_alert(self, notifier: TelegramNotifier) -> None:
        """Test trade alert format."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_trade_alert(
                symbol="BTCUSDT",
                side="BUY",
                quantity=0.1,
                price=45000.0,
                pnl=100.0,
            )
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert "Trade Executed" in call_args[0][0]
            assert "BTCUSDT" in call_args[0][0]
            assert "BUY" in call_args[0][0]
            assert "+100.00" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_daily_summary(self, notifier: TelegramNotifier) -> None:
        """Test daily summary alert format."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_daily_summary(
                total_pnl=500.0,
                trades_count=10,
                win_rate=60.0,
            )
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert "Daily Trading Summary" in call_args[0][0]
            assert "+500.00" in call_args[0][0]
            assert "60.0%" in call_args[0][0]


class TestFormatMessage:
    """Test suite for message formatting."""

    def test_format_info_message(self, notifier: TelegramNotifier) -> None:
        """Test INFO level formatting."""
        result = notifier._format_message("test", AlertLevel.INFO)
        assert "ℹ️" in result

    def test_format_warning_message(self, notifier: TelegramNotifier) -> None:
        """Test WARNING level formatting."""
        result = notifier._format_message("test", AlertLevel.WARNING)
        assert "⚠️" in result

    def test_format_critical_message(self, notifier: TelegramNotifier) -> None:
        """Test CRITICAL level formatting."""
        result = notifier._format_message("test", AlertLevel.CRITICAL)
        assert "🚨" in result
