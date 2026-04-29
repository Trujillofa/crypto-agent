from __future__ import annotations

import asyncio
import difflib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.notifications.telegram import AlertLevel, TelegramNotifier, TelegramPollingConflict
from src.overseer.prompts import build_system_prompt
from src.overseer.xai import XAIClient
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.execution.futures_executor import FuturesTradingExecutor

_KNOWN_COMMANDS = ["/status", "/risk", "/positions", "/reset", "/ask", "/help"]


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
        agent_id: str = "default",
    ) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._mode = mode
        self._agent_id = agent_id
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
        self._futures_executor: FuturesTradingExecutor | None = None

    async def run(self) -> None:
        self._running = True
        self._logger.info("AI overseer loop started")
        consecutive_errors = 0
        max_backoff = 300  # 5 minutes cap

        while self._running:
            try:
                updates = await self._telegram.get_updates(
                    offset=self._offset,
                    timeout=25,
                )
                consecutive_errors = 0  # reset on success
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._offset = update_id + 1
                    await self._handle_update(update)
            except TelegramPollingConflict as exc:
                self._running = False
                self._logger.warning("AI overseer disabled: %s", exc)
                return
            except asyncio.CancelledError:
                self._logger.info("AI overseer loop cancelled")
                raise
            except Exception as exc:
                consecutive_errors += 1
                backoff = min(self._poll_interval_seconds * (2**consecutive_errors), max_backoff)
                self._logger.error("AI overseer loop error (retry in %.0fs): %s", backoff, exc)
                await asyncio.sleep(backoff)

    def stop(self) -> None:
        self._running = False

    def set_futures_executor(self, executor: FuturesTradingExecutor) -> None:
        self._futures_executor = executor

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

        matches = difflib.get_close_matches(command.split()[0], _KNOWN_COMMANDS, n=1, cutoff=0.6)
        if matches:
            await self._reply(
                chat_id,
                f"Unknown command. Did you mean <b>{matches[0]}</b>?",
                as_html=True,
            )
        else:
            await self._reply(chat_id, self._help_text(), as_html=True)

    async def _cmd_status(self) -> str:
        summary = await self._portfolio_manager.get_portfolio_summary()
        risk = self._risk_manager.get_risk_summary()
        active_breakers = [
            name for name, active in risk.get("circuit_breakers", {}).items() if bool(active)
        ]
        breakers = ", ".join(active_breakers) if active_breakers else "none"
        kill_emoji = "🔴" if risk.get("kill_switch_active") else "🟢"
        kill_switch = "ON" if risk.get("kill_switch_active") else "OFF"
        mode_label = "📄 PAPER" if "paper" in self._mode.lower() else "💰 LIVE"
        last_trade_text = "—"
        if summary.last_trade_time is not None:
            last_trade_text = summary.last_trade_time.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        pnl_sign = "+" if summary.total_realized_pnl >= 0 else ""
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        # Live futures data (cached from 30 s monitor loop — no extra API call)
        live = self._futures_executor.get_live_status() if self._futures_executor else None
        live_positions: list[dict[str, object]] = (
            live["positions"] if live else []  # type: ignore[index]
        )
        account_balance: float = float(live["account_balance"]) if live else 0.0

        # Unrealized P&L across all open positions
        total_unrealized = sum(float(p["unrealized_pnl"]) for p in live_positions)
        open_count = len(live_positions) if live_positions else summary.open_positions

        lines = [
            f"{kill_emoji} <b>{self._agent_id}</b> — {mode_label}",
            "",
            f"💼 Realized P&L: {pnl_sign}{summary.total_realized_pnl:.2f} USDT",
        ]

        if live is not None:
            unreal_sign = "+" if total_unrealized >= 0 else ""
            lines.append(f"📊 Unrealized: {unreal_sign}{total_unrealized:.2f} USDT")
            if account_balance > 0:
                lines.append(f"💰 Wallet: {account_balance:.2f} USDT")

        lines.append(f"📂 Positions: {open_count} open")

        if live_positions:
            lines.append("")
            for pos in live_positions:
                sym = str(pos["symbol"]).replace("USDT", "")
                qty = float(pos["qty"])
                entry = float(pos["entry_price"])
                mark = float(pos["mark_price"])
                upnl = float(pos["unrealized_pnl"])
                sl = float(pos["sl_price"])
                tp = float(pos["tp_price"])
                upnl_sign = "+" if upnl >= 0 else ""
                lines.append(
                    f"<b>{sym}</b>  {qty:.4g} @ {entry:.6g} → {mark:.6g}  ({upnl_sign}{upnl:.2f})"
                )
                if sl > 0 or tp > 0:
                    lines.append(f"  SL: {sl:.6g}  TP: {tp:.6g}")

        lines += [
            "",
            f"🛡 Kill Switch: {kill_switch}",
            f"⚡ Breakers: {breakers}",
            "",
            f"📈 Last Trade: {last_trade_text}",
            f"🔢 Lifetime:  {summary.total_trades} trades",
            f"🕐 {ts}",
        ]
        return "\n".join(lines)

    def _cmd_risk(self) -> str:
        risk = self._risk_manager.get_risk_summary()
        active_breakers = [
            name for name, active in risk.get("circuit_breakers", {}).items() if bool(active)
        ]
        breakers = ", ".join(active_breakers) if active_breakers else "none"
        kill_text = "ON" if risk.get("kill_switch_active") else "OFF"
        daily_pnl = float(risk.get("daily_pnl", 0.0))
        pnl_sign = "+" if daily_pnl >= 0 else ""

        return (
            "🛡 <b>Risk Snapshot</b>\n"
            "\n"
            f"⚡ Kill Switch: {kill_text}\n"
            f"🔌 Breakers: {breakers}\n"
            f"📈 Daily P&L: {pnl_sign}{daily_pnl:.2f} USDT\n"
            f"📉 Consecutive Losses: {int(risk.get('consecutive_losses', 0))}\n"
            f"🌐 API Errors: {int(risk.get('api_errors', 0))}\n"
            f"⏱ Avg Latency: {float(risk.get('avg_latency_ms', 0.0)):.1f} ms"
        )

    def _cmd_positions(self) -> str:
        positions = self._portfolio_manager.get_all_positions()
        if not positions:
            return "📂 <b>Open Positions:</b> none"

        lines = ["📂 <b>Open Positions</b>\n"]
        for position in positions[:10]:
            lines.append(
                f"🔸 {position.symbol} — {position.quantity:.6f} @ {position.entry_price:.4f}"
            )

        if len(positions) > 10:
            lines.append(f"\n… and {len(positions) - 10} more")

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
            name for name, active in risk.get("circuit_breakers", {}).items() if bool(active)
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
            "as_of": datetime.now(UTC).isoformat(),
        }

    def _append_history(self, chat_id: str, role: str, content: str) -> None:
        if self._max_history == 0:
            return
        if chat_id not in self._chat_history and len(self._chat_history) >= self._max_tracked_chats:
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
        was_blocked = risk_before.get("kill_switch_active") or any(
            risk_before.get("circuit_breakers", {}).values()
        )
        if not was_blocked:
            return "✅ No active blocks. Trading is already allowed."

        ks_before = "ON" if risk_before.get("kill_switch_active") else "OFF"
        self._risk_manager.clear_trading_blocks(
            reset_counters=True,
            reset_peak_balance=True,
        )
        risk_after = self._risk_manager.get_risk_summary()
        ks_after = "ON" if risk_after.get("kill_switch_active") else "OFF"
        self._logger.warning("Kill switch / breakers reset via Telegram overseer")

        return (
            "✅ <b>Risk Reset Complete</b>\n"
            "\n"
            f"⚡ Kill Switch: {ks_before} → {ks_after}\n"
            "🔌 Breakers: cleared\n"
            "🔢 Counters: reset\n"
            "💼 Peak Balance: reset"
        )

    def _help_text(self) -> str:
        return (
            "🤖 <b>Crypto Agent Commands</b>\n"
            "\n"
            "/status — Balance, positions, kill switch\n"
            "/risk — Risk metrics and breakers\n"
            "/positions — Open positions with entry prices\n"
            "/reset — Clear kill switch and breakers\n"
            "/ask &lt;question&gt; — AI-powered Q&A\n"
            "/help — This message"
        )
