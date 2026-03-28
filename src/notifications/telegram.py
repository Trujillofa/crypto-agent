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
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import escape
from enum import Enum
from typing import Any

import aiohttp

from src.utils.logger import get_logger


class AlertLevel(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class TelegramPollingConflict(RuntimeError):
    """Raised when another consumer is already polling getUpdates for this bot."""


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot configuration."""

    bot_token: str
    chat_id: str
    enabled: bool = True
    rate_limit_seconds: int = 5  # Minimum seconds between messages
    allowed_updates: tuple[str, ...] = ("message",)
    daily_summary_enabled: bool = True
    daily_summary_send_empty: bool = False


_TELEGRAM_MAX_MESSAGE_LENGTH = 4096


class TelegramNotifier:
    """Async Telegram notification service."""

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._config = config or self._load_config_from_env()
        self._last_message_time: float = 0
        self._session: aiohttp.ClientSession | None = None

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
            allowed_updates=("message",),
            daily_summary_enabled=os.getenv("TELEGRAM_DAILY_SUMMARY_ENABLED", "true").lower() == "true",
            daily_summary_send_empty=os.getenv("TELEGRAM_DAILY_SUMMARY_SEND_EMPTY", "false").lower() == "true",
        )

    async def __aenter__(self) -> TelegramNotifier:
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
        return bool(self._config.enabled and self._config.bot_token and self._config.chat_id)

    async def send_alert(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        parse_mode: str | None = "HTML",
        chat_id: str | None = None,
        respect_rate_limit: bool = True,
    ) -> bool:
        """Send an alert message to Telegram.

        Args:
            message: The message to send
            level: Alert severity level
            parse_mode: Telegram parse mode (HTML or Markdown)

        Returns:
            True if message was sent successfully, False otherwise
        """
        target_chat_id = chat_id or self._config.chat_id
        if not self._config.enabled or not self._config.bot_token or not target_chat_id:
            self._logger.debug("Telegram not configured, skipping alert")
            return False

        # Rate limiting
        if respect_rate_limit:
            loop = asyncio.get_running_loop()
            now = loop.time()
            time_since_last = now - self._last_message_time
            if time_since_last < self._config.rate_limit_seconds:
                wait_time = self._config.rate_limit_seconds - time_since_last
                await asyncio.sleep(wait_time)
            # Claim the send slot before yielding to the event loop so that
            # concurrent callers don't both pass the rate-limit check.
            self._last_message_time = asyncio.get_running_loop().time()

        formatted_message = self._format_message(message, level)

        try:
            success = await self._send_message(
                formatted_message,
                parse_mode,
                target_chat_id,
            )
            return success
        except Exception as exc:
            self._logger.error(f"Failed to send Telegram alert: {exc}")
            return False

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        if not self._config.enabled or not self._config.bot_token:
            return []

        allowed_updates = list(self._config.allowed_updates)
        try:
            updates = await self._fetch_updates(offset, timeout, allowed_updates)
            return updates
        except (RuntimeError, TelegramPollingConflict):
            raise  # Auth errors must propagate for caller backoff
        except Exception as exc:
            self._logger.error("Failed to fetch Telegram updates: %s", exc)
            return []

    async def send_kill_switch_alert(
        self,
        reason: str,
        auto_reset_minutes: int = 0,
    ) -> bool:
        """Send kill switch activation alert."""
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if auto_reset_minutes > 0:
            action = f"⏱ Auto-reset in {auto_reset_minutes} min (paper mode)"
        else:
            action = "⏱ All trading halted — manual /reset required"
        message = f"🚨 <b>KILL SWITCH ACTIVATED</b>\n\n🔒 Reason: {reason}\n{action}\n🕐 {ts}"
        return await self.send_alert(message, AlertLevel.CRITICAL)

    async def send_circuit_breaker_alert(
        self,
        breaker_name: str,
        details: str = "",
    ) -> bool:
        """Send circuit breaker activation alert."""
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        details_line = f"\n🔒 Details: {details}" if details else ""
        message = (
            f"⚠️ <b>CIRCUIT BREAKER</b> — {breaker_name}\n"
            f"{details_line}\n"
            f"⏱ Trading paused until conditions normalize\n"
            f"🕐 {ts}"
        )
        return await self.send_alert(message, AlertLevel.WARNING)

    async def send_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        pnl: float | None = None,
        market: str | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        entry_price: float | None = None,
        close_reason: str | None = None,
        balance: float | None = None,
    ) -> bool:
        """Send trade execution alert (entry or close).

        When pnl is provided, formats as a CLOSE message.
        Otherwise, formats as an OPEN/entry message.
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        market_label = self._format_market_label(market)
        side_emoji = "🟢" if side == "BUY" else "🔴"
        notional = quantity * price

        if pnl is not None:
            # --- CLOSE message ---
            reason_text = self._humanize_close_reason(close_reason) if close_reason else "Signal"
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_emoji = "📈" if pnl >= 0 else "📉"

            lines = [f"{side_emoji} <b>CLOSED {symbol}</b> — {market_label}\n"]
            lines.append(f"🔒 Reason: {reason_text}")
            if entry_price is not None:
                lines.append(f"🎯 Entry:  {entry_price:.4f}")
            lines.append(f"📤 Exit:   {price:.4f}")
            lines.append(f"📦 Size:   {quantity:.6f} (${notional:.2f})")
            lines.append(f"{pnl_emoji} P&L:    {pnl_sign}{pnl:.2f} USDT")
            if balance is not None:
                lines.append(f"💼 Balance: ${balance:,.2f}")
            lines.append(f"🕐 {ts}")
        else:
            # --- OPEN/ENTRY message ---
            action = "BUY" if side == "BUY" else "SELL"
            lines = [f"{side_emoji} <b>{action} {symbol}</b> — {market_label}\n"]
            lines.append(f"🎯 Entry: {price:.4f}")
            if stop_loss is not None:
                lines.append(f"🛑 SL:    {stop_loss:.4f}")
            if take_profit is not None:
                lines.append(f"✅ TP:    {take_profit:.4f}")
            lines.append(f"📦 Size:  {quantity:.6f} (${notional:.2f})")
            if balance is not None:
                lines.append(f"💼 Balance: ${balance:,.2f}")
            lines.append(f"🕐 {ts}")

        message = "\n".join(lines)
        return await self.send_alert(message, AlertLevel.INFO)

    async def send_daily_summary(
        self,
        total_pnl: float,
        trades_count: int,
        win_rate: float,
        summary_date: date,
        *,
        display_name: str | None = None,
        agent_id: str | None = None,
        strategy_names: list[str] | tuple[str, ...] | None = None,
        trading_pairs: list[str] | tuple[str, ...] | None = None,
        timeframe: str | None = None,
        mode: str | None = None,
        by_symbol: dict[str, dict[str, float | int]] | None = None,
        wins: int | None = None,
        losses: int | None = None,
        largest_win: dict[str, Any] | None = None,
        largest_loss: dict[str, Any] | None = None,
        notes: list[str] | tuple[str, ...] | None = None,
    ) -> bool:
        """Send daily trading summary."""
        pnl_sign = "+" if total_pnl >= 0 else ""
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        lines = [f"📊 <b>Daily Summary</b> — {summary_date.isoformat()}", ""]

        if display_name:
            lines.append(f"🤖 Agent:    <b>{display_name}</b>")
        if agent_id and agent_id != display_name:
            lines.append(f"🆔 Agent ID: <code>{agent_id}</code>")
        if strategy_names:
            lines.append(f"🧠 Strategy: {', '.join(strategy_names)}")
        pair_bits: list[str] = []
        if trading_pairs:
            pair_bits.append("/".join(trading_pairs))
        if timeframe:
            pair_bits.append(timeframe)
        if mode:
            pair_bits.append(mode.upper())
        if pair_bits:
            lines.append(f"🪙 Scope:    {' • '.join(pair_bits)}")

        lines.extend(
            [
                f"🔢 Trades:   {trades_count}",
                f"🎯 Win Rate: {win_rate:.1f}%",
                f"{pnl_emoji} P&L:     {pnl_sign}{total_pnl:.2f} USDT",
            ]
        )

        if wins is not None or losses is not None:
            wins_v = wins if wins is not None else 0
            losses_v = losses if losses is not None else max(0, trades_count - wins_v)
            lines.append(f"✅ Wins/Losses: {wins_v}/{losses_v}")

        if by_symbol:
            lines.append("")
            lines.append("📦 <b>By Symbol</b>")
            for symbol, stats in sorted(
                by_symbol.items(),
                key=lambda item: float(item[1].get("pnl", 0.0)),
                reverse=True,
            ):
                symbol_pnl = float(stats.get("pnl", 0.0))
                symbol_trades = int(stats.get("trades", 0))
                symbol_wins = int(stats.get("wins", 0))
                symbol_sign = "+" if symbol_pnl >= 0 else ""
                lines.append(
                    f"• <b>{escape(symbol)}</b>: {symbol_trades} trades, {symbol_wins} wins, {symbol_sign}{symbol_pnl:.2f} USDT"
                )

        if largest_win and trades_count > 0:
            lines.append("")
            lines.append(
                f"🏆 Best Trade: <b>{escape(str(largest_win.get('symbol', '?')))}</b> {float(largest_win.get('pnl', 0.0)):+.2f} USDT"
            )
        if largest_loss and trades_count > 0:
            lines.append(
                f"🩸 Worst Trade: <b>{escape(str(largest_loss.get('symbol', '?')))}</b> {float(largest_loss.get('pnl', 0.0)):+.2f} USDT"
            )

        if notes:
            lines.append("")
            lines.append("📝 <b>Notes</b>")
            for note in notes:
                lines.append(f"• {escape(str(note))}")

        return await self.send_alert("\n".join(lines), AlertLevel.INFO)

    @staticmethod
    def _format_market_label(market: str | None) -> str:
        """Convert raw market tag to display label."""
        if not market:
            return "📄 PAPER"
        m = market.lower()
        leverage_match = re.search(r"\(([^)]+)\)", market)
        leverage_suffix = f" ({leverage_match.group(1).upper()})" if leverage_match else ""
        if "paper-futures" in m:
            return f"📄 PAPER FUTURES{leverage_suffix}"
        if "paper-spot" in m:
            return "📄 PAPER SPOT"
        if "paper" in m and "futures" in m:
            return f"📄 PAPER FUTURES{leverage_suffix}"
        if "paper" in m and "spot" in m:
            return "📄 PAPER SPOT"
        if "paper" in m:
            return "📄 PAPER"
        if "futures" in m:
            return f"⚡ FUTURES{leverage_suffix}"
        if "spot" in m:
            return "💰 SPOT"
        return market.upper()

    @staticmethod
    def _humanize_close_reason(reason: str) -> str:
        """Convert raw exit reason to human-readable text."""
        r = reason.upper()
        if r.startswith("STOP_LOSS"):
            return "Stop Loss"
        if r.startswith("TAKE_PROFIT"):
            return "Take Profit"
        if r.startswith("TRAILING_STOP"):
            return "Trailing Stop"
        if r.startswith("TIME_STOP"):
            return "Time Stop"
        return reason

    def _format_message(self, message: str, level: AlertLevel) -> str:
        """Format message. Level emojis are embedded in messages directly."""
        return message

    async def _send_message(
        self,
        text: str,
        parse_mode: str | None,
        chat_id: str,
    ) -> bool:
        """Send message via Telegram Bot API."""
        if not self._session:
            async with aiohttp.ClientSession() as session:
                return await self._do_send(session, text, parse_mode, chat_id)
        return await self._do_send(self._session, text, parse_mode, chat_id)

    async def _fetch_updates(
        self,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
    ) -> list[dict[str, Any]]:
        if not self._session:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as session:
                return await self._do_get_updates(
                    session,
                    offset,
                    timeout,
                    allowed_updates,
                )
        return await self._do_get_updates(
            self._session,
            offset,
            timeout,
            allowed_updates,
        )

    async def _do_send(
        self,
        session: aiohttp.ClientSession,
        text: str,
        parse_mode: str | None,
        chat_id: str,
    ) -> bool:
        """Execute the actual HTTP request to Telegram API."""
        if len(text) > _TELEGRAM_MAX_MESSAGE_LENGTH:
            truncation_notice = "\n\n[message truncated]"
            text = text[: _TELEGRAM_MAX_MESSAGE_LENGTH - len(truncation_notice)] + truncation_notice

        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        async with session.post(url, json=payload) as response:
            if response.status == 200:
                self._logger.info("Telegram alert sent successfully")
                return True

            error_text = await response.text()
            self._logger.error(f"Telegram API error: {response.status} - {error_text}")
            return False

    async def _do_get_updates(
        self,
        session: aiohttp.ClientSession,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
    ) -> list[dict[str, Any]]:
        url = f"https://api.telegram.org/bot{self._config.bot_token}/getUpdates"
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": allowed_updates,
        }
        if offset is not None:
            payload["offset"] = offset

        async with session.post(url, json=payload) as response:
            if response.status != 200:
                error_text = await response.text()
                if response.status == 409:
                    raise TelegramPollingConflict(
                        "Telegram getUpdates conflict: another consumer is polling this bot"
                    )
                self._logger.error(
                    "Telegram API getUpdates error: %s - %s",
                    response.status,
                    error_text,
                )
                if response.status in (401, 403):
                    raise RuntimeError(
                        f"Telegram auth failed ({response.status}). Check TELEGRAM_BOT_TOKEN."
                    )
                return []

            body = await response.json()
            if not body.get("ok"):
                self._logger.error("Telegram API getUpdates returned ok=false: %s", body)
                return []

            result = body.get("result", [])
            if not isinstance(result, list):
                return []
            return [item for item in result if isinstance(item, dict)]
