"""Tests for notifications/telegram.py."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.notifications.telegram import (
    AlertLevel,
    TelegramConfig,
    TelegramNotifier,
    TelegramPollingConflict,
)


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

    @pytest.mark.asyncio
    async def test_rate_limit_slot_claimed_before_send(self, notifier: TelegramNotifier) -> None:
        """Rate-limit timestamp is updated before yielding, so concurrent callers queue."""
        send_times: list[float] = []

        def make_mock_response(*_args: object, **_kwargs: object) -> MagicMock:
            mock_response = MagicMock()
            mock_response.status = 200

            async def _aenter(self_: object) -> MagicMock:
                send_times.append(asyncio.get_running_loop().time())
                return mock_response

            mock_response.__aenter__ = _aenter
            mock_response.__aexit__ = AsyncMock()
            return mock_response

        config = TelegramConfig(bot_token="t", chat_id="c", enabled=True, rate_limit_seconds=1)
        notifier2 = TelegramNotifier(config=config)

        async with notifier2:
            with patch.object(
                notifier2._session,
                "post",
                side_effect=make_mock_response,
            ):
                t1 = asyncio.create_task(notifier2.send_alert("msg1"))
                t2 = asyncio.create_task(notifier2.send_alert("msg2"))
                await asyncio.gather(t1, t2)

        # Both sent, but the second one must have been delayed by ≥1 s
        assert len(send_times) == 2
        assert send_times[1] - send_times[0] >= 1.0


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

    @pytest.mark.asyncio
    async def test_get_updates_conflict_raises(self, notifier: TelegramNotifier) -> None:
        with patch.object(
            notifier,
            "_fetch_updates",
            new=AsyncMock(side_effect=TelegramPollingConflict("Conflict")),
        ):
            with pytest.raises(TelegramPollingConflict):
                await notifier.get_updates()


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
    async def test_send_circuit_breaker_alert_no_blank_line_when_no_details(
        self, notifier: TelegramNotifier
    ) -> None:
        """Circuit breaker alert without details must not have a blank line mid-message."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_circuit_breaker_alert("max_drawdown")
            message: str = mock_send.call_args[0][0]
            # No consecutive blank lines should appear
            assert "\n\n\n" not in message

    @pytest.mark.asyncio
    async def test_send_circuit_breaker_alert_with_details(
        self, notifier: TelegramNotifier
    ) -> None:
        """Circuit breaker alert with details should include the details line."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_circuit_breaker_alert("max_drawdown", details="Loss 20%")
            message: str = mock_send.call_args[0][0]
            assert "<b>Details:</b> Loss 20%" in message

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
    async def test_send_trade_alert_includes_sl_tp(self, notifier: TelegramNotifier) -> None:
        """Trade alerts should include SL/TP when provided."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_trade_alert(
                symbol="SOLUSDT",
                side="SELL",
                quantity=64.45672191528547,
                price=92.94,
                market="paper-futures (3x)",
                stop_loss=99.1457,
                take_profit=78.9771,
            )
            mock_send.assert_called_once()
            message = mock_send.call_args[0][0]
            assert "<b>SL:</b> 99.1457" in message
            assert "<b>TP:</b> 78.9771" in message

    @pytest.mark.asyncio
    async def test_send_daily_summary(self, notifier: TelegramNotifier) -> None:
        """Test daily summary alert format."""
        with patch.object(notifier, "send_alert", new=AsyncMock(return_value=True)) as mock_send:
            await notifier.send_daily_summary(
                total_pnl=500.0,
                trades_count=10,
                win_rate=60.0,
                summary_date=date(2026, 3, 12),
            )
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert "Daily Trading Summary" in call_args[0][0]
            assert "+500.00" in call_args[0][0]
            assert "60.0%" in call_args[0][0]
            assert "2026-03-12" in call_args[0][0]


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


class TestMessageTruncation:
    """Test Telegram's 4096-character message-length limit enforcement."""

    @pytest.mark.asyncio
    async def test_long_message_is_truncated(self, notifier: TelegramNotifier) -> None:
        """Messages longer than 4096 chars must be truncated before being sent."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        long_message = "x" * 5000

        async with notifier:
            mock_post = patch.object(notifier._session, "post", return_value=mock_response)
            with mock_post as mocked:
                await notifier.send_alert(long_message, respect_rate_limit=False)
                _url, call_kwargs = mocked.call_args[0][0], mocked.call_args[1]

        sent_text: str = call_kwargs["json"]["text"]
        assert len(sent_text) <= 4096
        assert "[message truncated]" in sent_text

    @pytest.mark.asyncio
    async def test_short_message_is_not_truncated(self, notifier: TelegramNotifier) -> None:
        """Messages within the 4096-char limit must be sent unmodified."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        short_message = "hello world"

        async with notifier:
            mock_post = patch.object(notifier._session, "post", return_value=mock_response)
            with mock_post as mocked:
                await notifier.send_alert(short_message, respect_rate_limit=False)
                call_kwargs = mocked.call_args[1]

        sent_text: str = call_kwargs["json"]["text"]
        assert "[message truncated]" not in sent_text


class TestNotifierInit:
    """Tests for TelegramNotifier initialisation."""

    def test_no_message_queue_attribute(self, notifier: TelegramNotifier) -> None:
        """TelegramNotifier must not have the unused _message_queue attribute."""
        assert not hasattr(notifier, "_message_queue")
