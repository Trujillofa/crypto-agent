from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.notifications.telegram import AlertLevel, TelegramNotifier
from src.overseer.prompts import build_system_prompt
from src.overseer.xai import XAIClient
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.utils.logger import get_logger


class OverseerAgent:
    def __init__(
        self,
        mode: str,
        poll_interval_seconds: float,
        max_history: int,
        allowed_chat_ids: list[str],
        telegram: TelegramNotifier,
        portfolio_manager: PortfolioManager,
        risk_manager: RiskManager,
        xai_client: XAIClient | None,
        max_tracked_chats: int = 50,
    ) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._mode = mode
        self._poll_interval_seconds = max(0.2, poll_interval_seconds)
        self._max_history = max(0, max_history)
        self._allowed_chat_ids = {chat_id for chat_id in allowed_chat_ids if chat_id}
        self._telegram = telegram
        self._portfolio_manager = portfolio_manager
        self._risk_manager = risk_manager
        self._xai_client = xai_client
        self._max_tracked_chats = max(1, max_tracked_chats)
        self._running = False
        self._offset: int | None = None
        self._chat_history: dict[str, list[dict[str, str]]] = {}

    async def run(self) -> None:
        self._running = True
        self._logger.info("AI overseer loop started")

        while self._running:
            should_backoff = False
            try:
                updates = await self._telegram.get_updates(
                    offset=self._offset,
                    timeout=25,
                )
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                self._logger.info("AI overseer loop cancelled")
                raise
            except Exception as exc:
                self._logger.error("AI overseer loop error: %s", exc)
                should_backoff = True

            if should_backoff:
                await asyncio.sleep(self._poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

    async def _handle_update(self, update: dict[str, object]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return

        user = message.get("from")
        if isinstance(user, dict) and user.get("is_bot") is True:
            return

        chat = message.get("chat")
        if not isinstance(chat, dict):
            return

        chat_id_raw = chat.get("id")
        if chat_id_raw is None:
            return
        chat_id = str(chat_id_raw)

        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            self._logger.warning("Ignoring unauthorized AI overseer chat: %s", chat_id)
            return

        text = message.get("text")
        if not isinstance(text, str):
            return

        command = text.strip()
        if not command:
            return

        if command.startswith("/status"):
            await self._reply(chat_id, await self._cmd_status(), as_html=True)
            return
        if command.startswith("/risk"):
            await self._reply(chat_id, self._cmd_risk(), as_html=True)
            return
        if command.startswith("/positions"):
            await self._reply(chat_id, self._cmd_positions(), as_html=True)
            return
        if command.startswith("/reset"):
            await self._reply(chat_id, self._cmd_reset(), as_html=True)
            return
        if command.startswith("/help"):
            await self._reply(chat_id, self._help_text(), as_html=True)
            return
        if command.startswith("/ask"):
            _, _, query = command.partition(" ")
            if not query.strip():
                await self._reply(
                    chat_id,
                    "<b>Usage:</b> /ask &lt;question&gt;",
                    as_html=True,
                )
                return
            answer = await self._cmd_ask(chat_id, query.strip())
            await self._reply(chat_id, answer, as_html=False)
            return

        await self._reply(chat_id, self._help_text(), as_html=True)

    async def _cmd_status(self) -> str:
        summary = await self._portfolio_manager.get_portfolio_summary()
        risk = self._risk_manager.get_risk_summary()
        active_breakers = [
            name
            for name, active in risk.get("circuit_breakers", {}).items()
            if bool(active)
        ]
        breakers = ", ".join(active_breakers) if active_breakers else "none"
        kill_switch = "ON" if risk.get("kill_switch_active") else "OFF"

        return (
            "<b>AI Overseer Status</b>\n"
            f"<b>Mode:</b> {self._mode}\n"
            f"<b>Kill Switch:</b> {kill_switch}\n"
            f"<b>Active Breakers:</b> {breakers}\n"
            f"<b>Open Positions:</b> {summary.open_positions}\n"
            f"<b>Total Trades:</b> {summary.total_trades}\n"
            f"<b>Realized PnL:</b> {summary.total_realized_pnl:.2f} USDT"
        )

    def _cmd_risk(self) -> str:
        risk = self._risk_manager.get_risk_summary()
        active_breakers = [
            name
            for name, active in risk.get("circuit_breakers", {}).items()
            if bool(active)
        ]
        breakers = ", ".join(active_breakers) if active_breakers else "none"

        return (
            "<b>Risk Snapshot</b>\n"
            f"<b>Kill Switch:</b> {'ON' if risk.get('kill_switch_active') else 'OFF'}\n"
            f"<b>Breakers:</b> {breakers}\n"
            f"<b>Daily PnL:</b> {float(risk.get('daily_pnl', 0.0)):.2f} USDT\n"
            f"<b>Consecutive Losses:</b> {int(risk.get('consecutive_losses', 0))}\n"
            f"<b>API Errors:</b> {int(risk.get('api_errors', 0))}\n"
            f"<b>Avg Latency:</b> {float(risk.get('avg_latency_ms', 0.0)):.1f} ms"
        )

    def _cmd_positions(self) -> str:
        positions = self._portfolio_manager.get_all_positions()
        if not positions:
            return "<b>Open Positions:</b> none"

        lines = ["<b>Open Positions</b>"]
        for position in positions[:10]:
            lines.append(
                f"- {position.symbol}: qty={position.quantity:.6f}, entry={position.entry_price:.4f}"
            )

        if len(positions) > 10:
            lines.append(f"... and {len(positions) - 10} more")

        return "\n".join(lines)

    async def _cmd_ask(self, chat_id: str, query: str) -> str:
        if self._xai_client is None:
            return "AI provider is not configured. Set XAI_API_KEY and enable ai settings first."

        context = await self._build_context()
        history = self._chat_history.get(chat_id, [])
        messages = [
            {"role": "system", "content": build_system_prompt(context)},
            *history,
            {"role": "user", "content": query},
        ]

        try:
            answer = await self._xai_client.chat(messages)
        except Exception as exc:
            self._logger.error("xAI request failed: %s", exc)
            return "AI request failed. Try again in a few seconds."

        self._append_history(chat_id, "user", query)
        self._append_history(chat_id, "assistant", answer)
        return answer

    async def _build_context(self) -> dict[str, str]:
        summary = await self._portfolio_manager.get_portfolio_summary()
        risk = self._risk_manager.get_risk_summary()
        positions = self._portfolio_manager.get_all_positions()

        active_breakers = [
            name
            for name, active in risk.get("circuit_breakers", {}).items()
            if bool(active)
        ]
        breaker_text = ", ".join(active_breakers) if active_breakers else "none"
        positions_text = ", ".join(
            f"{position.symbol}:{position.quantity:.6f}@{position.entry_price:.4f}"
            for position in positions[:5]
        )
        if not positions_text:
            positions_text = "none"

        return {
            "mode": self._mode,
            "risk": (
                f"kill_switch={risk.get('kill_switch_active')}; "
                f"breakers={breaker_text}; "
                f"daily_pnl={float(risk.get('daily_pnl', 0.0)):.2f}"
            ),
            "portfolio": (
                f"open_positions={summary.open_positions}; "
                f"total_trades={summary.total_trades}; "
                f"realized_pnl={summary.total_realized_pnl:.2f}"
            ),
            "positions": positions_text,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    def _append_history(self, chat_id: str, role: str, content: str) -> None:
        if self._max_history == 0:
            return
        if (
            chat_id not in self._chat_history
            and len(self._chat_history) >= self._max_tracked_chats
        ):
            oldest_chat_id = next(iter(self._chat_history))
            self._chat_history.pop(oldest_chat_id, None)
        history = self._chat_history.setdefault(chat_id, [])
        history.append({"role": role, "content": content})
        max_messages = self._max_history * 2
        if len(history) > max_messages:
            self._chat_history[chat_id] = history[-max_messages:]

    async def _reply(self, chat_id: str, message: str, as_html: bool) -> None:
        await self._telegram.send_alert(
            message,
            AlertLevel.INFO,
            parse_mode="HTML" if as_html else None,
            chat_id=chat_id,
            respect_rate_limit=False,
        )

    def _cmd_reset(self) -> str:
        risk_before = self._risk_manager.get_risk_summary()
        was_blocked = (
            risk_before.get("kill_switch_active")
            or any(risk_before.get("circuit_breakers", {}).values())
        )
        if not was_blocked:
            return "<b>Reset:</b> No active blocks. Trading is already allowed."

        self._risk_manager.clear_trading_blocks(
            reset_counters=True,
            reset_peak_balance=True,
        )
        risk_after = self._risk_manager.get_risk_summary()
        self._logger.warning("Kill switch / breakers reset via Telegram overseer")

        return (
            "<b>Risk Reset Complete</b>\n"
            f"<b>Kill Switch:</b> {'ON' if risk_before.get('kill_switch_active') else 'OFF'} → "
            f"{'ON' if risk_after.get('kill_switch_active') else 'OFF'}\n"
            f"<b>Breakers:</b> cleared\n"
            f"<b>Counters:</b> reset\n"
            f"<b>Peak Balance:</b> reset"
        )

    def _help_text(self) -> str:
        return (
            "<b>AI Overseer Commands</b>\n"
            "/status - Trading and PnL snapshot\n"
            "/risk - Risk and breaker snapshot\n"
            "/positions - Open positions\n"
            "/reset - Clear kill switch and breakers\n"
            "/ask &lt;question&gt; - Advisory Q&A"
        )
