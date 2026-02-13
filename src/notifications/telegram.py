"""Telegram notification service for trading alerts.

Usage:
    Set environment variables:
        TELEGRAM_BOT_TOKEN: Your Telegram bot token from @BotFather
        TELEGRAM_CHAT_ID: Your chat ID (use @userinfobot to get it)

    Example:
        notifier = TelegramNotifier()
        await notifier.send_alert("Kill switch activated!")
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import aiohttp

from src.utils.logger import get_logger


class AlertLevel(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot configuration."""

    bot_token: str
    chat_id: str
    enabled: bool = True
    rate_limit_seconds: int = 5  # Minimum seconds between messages


class TelegramNotifier:
    """Async Telegram notification service."""

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._config = config or self._load_config_from_env()
        self._last_message_time: float = 0
        self._session: aiohttp.ClientSession | None = None
        self._message_queue: asyncio.Queue[tuple[str, AlertLevel]] = asyncio.Queue()

        if not self._config.enabled:
            self._logger.info("Telegram notifications disabled")
        elif not self._config.bot_token or not self._config.chat_id:
            self._logger.warning(
                "Telegram credentials not configured. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables."
            )

    @staticmethod
    def _load_config_from_env() -> TelegramConfig:
        """Load configuration from environment variables."""
        return TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            enabled=os.getenv("TELEGRAM_ENABLED", "true").lower() == "true",
            rate_limit_seconds=int(os.getenv("TELEGRAM_RATE_LIMIT", "5")),
        )

    async def __aenter__(self) -> "TelegramNotifier":
        """Initialize aiohttp session."""
        timeout = aiohttp.ClientTimeout(total=30)
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    def is_configured(self) -> bool:
        """Check if Telegram is properly configured."""
        return bool(
            self._config.enabled and self._config.bot_token and self._config.chat_id
        )

    async def send_alert(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        parse_mode: str = "HTML",
    ) -> bool:
        """Send an alert message to Telegram.

        Args:
            message: The message to send
            level: Alert severity level
            parse_mode: Telegram parse mode (HTML or Markdown)

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self.is_configured():
            self._logger.debug("Telegram not configured, skipping alert")
            return False

        # Rate limiting
        now = asyncio.get_event_loop().time()
        time_since_last = now - self._last_message_time
        if time_since_last < self._config.rate_limit_seconds:
            wait_time = self._config.rate_limit_seconds - time_since_last
            await asyncio.sleep(wait_time)

        formatted_message = self._format_message(message, level)

        try:
            success = await self._send_message(formatted_message, parse_mode)
            self._last_message_time = asyncio.get_event_loop().time()
            return success
        except Exception as exc:
            self._logger.error(f"Failed to send Telegram alert: {exc}")
            return False

    async def send_kill_switch_alert(self, reason: str) -> bool:
        """Send kill switch activation alert.

        Args:
            reason: Reason for kill switch activation

        Returns:
            True if message was sent successfully
        """
        message = f"""
<b>KILL SWITCH ACTIVATED</b>

<b>Reason:</b> {reason}
<b>Time:</b> {datetime.now(timezone.utc).isoformat()}

All trading has been halted. Manual intervention required.
        """.strip()

        return await self.send_alert(message, AlertLevel.CRITICAL)

    async def send_circuit_breaker_alert(
        self,
        breaker_name: str,
        details: str = "",
    ) -> bool:
        """Send circuit breaker activation alert.

        Args:
            breaker_name: Name of the triggered circuit breaker
            details: Additional details about the trigger

        Returns:
            True if message was sent successfully
        """
        message = f"""
<b>CIRCUIT BREAKER TRIGGERED</b>

<b>Breaker:</b> {breaker_name}
<b>Time:</b> {datetime.now(timezone.utc).isoformat()}
{f"<b>Details:</b> {details}" if details else ""}

Trading may be paused until conditions normalize.
        """.strip()

        return await self.send_alert(message, AlertLevel.WARNING)

    async def send_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        pnl: float | None = None,
    ) -> bool:
        """Send trade execution alert.

        Args:
            symbol: Trading pair symbol
            side: Trade side (BUY/SELL)
            quantity: Trade quantity
            price: Execution price
            pnl: Profit/loss if closing position

        Returns:
            True if message was sent successfully
        """
        pnl_text = ""
        if pnl is not None:
            pnl_emoji = "+" if pnl >= 0 else ""
            pnl_text = f"\n<b>PnL:</b> {pnl_emoji}{pnl:.2f} USDT"

        message = f"""
<b>Trade Executed</b>

<b>Symbol:</b> {symbol}
<b>Side:</b> {side}
<b>Quantity:</b> {quantity}
<b>Price:</b> {price}{pnl_text}
        """.strip()

        return await self.send_alert(message, AlertLevel.INFO)

    async def send_daily_summary(
        self,
        total_pnl: float,
        trades_count: int,
        win_rate: float,
    ) -> bool:
        """Send daily trading summary.

        Args:
            total_pnl: Total profit/loss for the day
            trades_count: Number of trades executed
            win_rate: Winning trade percentage

        Returns:
            True if message was sent successfully
        """
        pnl_emoji = "+" if total_pnl >= 0 else ""

        message = f"""
<b>Daily Trading Summary</b>

<b>Total PnL:</b> {pnl_emoji}{total_pnl:.2f} USDT
<b>Trades:</b> {trades_count}
<b>Win Rate:</b> {win_rate:.1f}%
<b>Date:</b> {datetime.now(timezone.utc).date().isoformat()}
        """.strip()

        return await self.send_alert(message, AlertLevel.INFO)

    def _format_message(self, message: str, level: AlertLevel) -> str:
        """Format message with level indicator."""
        level_emoji = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.CRITICAL: "🚨",
        }
        emoji = level_emoji.get(level, "")
        return f"{emoji} {message}" if emoji else message

    async def _send_message(self, text: str, parse_mode: str) -> bool:
        """Send message via Telegram Bot API."""
        if not self._session:
            async with aiohttp.ClientSession() as session:
                return await self._do_send(session, text, parse_mode)
        return await self._do_send(self._session, text, parse_mode)

    async def _do_send(
        self,
        session: aiohttp.ClientSession,
        text: str,
        parse_mode: str,
    ) -> bool:
        """Execute the actual HTTP request to Telegram API."""
        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        async with session.post(url, json=payload) as response:
            if response.status == 200:
                self._logger.info("Telegram alert sent successfully")
                return True

            error_text = await response.text()
            self._logger.error(f"Telegram API error: {response.status} - {error_text}")
            return False
