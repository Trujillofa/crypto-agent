"""Futures trading execution service."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.backtest.sizing import calculate_futures_order_quantity
from src.core.event_log import EventLog
from src.db.pool import get_pool
from src.execution.entry_admission import admit
from src.execution.futures_client import (
    BinanceFuturesClient,
    FuturesOrderInfo,
    FuturesUserTrade,
)
from src.execution.metrics import ExecutionMetrics
from src.execution.staged_orders import StagedOrderManager
from src.notifications.telegram import TelegramNotifier
from src.portfolio.manager import PortfolioManager
from src.risk.entry_control import EntryControl
from src.risk.guards import GuardContext, GuardPipeline
from src.risk.manager import RiskManager
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass(frozen=True)
class FuturesTradingConfig:
    """Futures trading execution configuration."""

    api_key: str
    api_secret: str
    test_mode: bool = True
    enabled: bool = False
    symbols: list[str] = field(default_factory=list)
    default_leverage: int = 5
    max_leverage: int = 10
    margin_mode: str = "isolated"
    position_mode: str = "one-way"
    order_size_usdt: float = 100.0
    liquidation_buffer_pct: float = 5.0
    timeframe: str = "1h"
    # SL/TP — ATR-based (primary) with fixed-pct fallback
    sl_atr_multiplier: float = 2.0  # SL = entry - mult * ATR(14)
    tp_atr_multiplier: float = 4.5  # TP = entry + mult * ATR(14)
    trailing_activate_atr: float = 1.5
    trailing_offset_atr: float = 1.0
    time_stop_minutes: float = 0.0  # 0 = disabled
    stop_loss_pct: float = 0.03  # fallback if ATR unavailable
    take_profit_pct: float = 0.06  # fallback if ATR unavailable
    max_concurrent_longs: int = 0  # 0 = disabled; cap on simultaneous LONG positions
    sl_cooldown_minutes: int = 0  # 0 = disabled; re-entry cooldown after a stop-loss


@dataclass(frozen=True)
class CloseExecutionDetails:
    """Exchange close details resolved from Binance after an SL/TP trigger."""

    reason: str | None
    ticket_id: str | None
    fill_price: float | None = None
    realized_pnl: float | None = None
    confirmed_fill: bool = False


class PostFillAccountingError(RuntimeError):
    """The exchange fill is final but local bookkeeping could not follow it.

    Raised *after* the order executed. Callers must not retry it: the exposure
    change already happened, so a retry would double it. ``retryable`` is
    explicit so generic retry helpers can honour that without inspecting the
    message.
    """

    retryable = False


class PositionStateDivergence(PostFillAccountingError):
    """The fill disagrees with tracked state (e.g. it closed more than we hold).

    Fail-closed: tracked state and the risk slot are left untouched so the next
    entry is blocked and reconciliation has to repair the books.
    """

    retryable = False


# Quantity comparison tolerance. Exchange step sizes are truncated by the
# client, so an exact float comparison would reject legitimate full closes.
_QUANTITY_TOLERANCE = 1e-9


class FuturesTradingExecutor:
    """Futures trading execution service with leverage and risk management."""

    def __init__(
        self,
        config: FuturesTradingConfig,
        risk_manager: RiskManager,
        metrics: ExecutionMetrics,
        portfolio_manager: PortfolioManager | None = None,
        notifier: TelegramNotifier | None = None,
        event_log: EventLog | None = None,
        guard_pipeline: GuardPipeline | None = None,
        staged_manager: StagedOrderManager | None = None,
        entry_control: EntryControl | None = None,
    ) -> None:
        self._config = config
        self._risk_manager = risk_manager
        self._metrics = metrics
        self._portfolio_manager = portfolio_manager
        self._notifier = notifier or TelegramNotifier()
        self._logger = get_logger(self.__class__.__name__)
        self._event_log = event_log
        self._guard_pipeline = guard_pipeline
        self._staged_manager = staged_manager
        # Durable brake. Present in every environment: with no control row it
        # fails closed, which is the point.
        self._entry_control = entry_control or EntryControl()
        self._client: BinanceFuturesClient | None = None
        self._running = False
        self._positions: dict[str, dict[str, Any]] = {}  # Track futures positions
        self._sl_tp_orders: dict[str, dict[str, str]] = {}  # symbol → {sl_order_id, tp_order_id}
        self._sl_tp_prices: dict[str, dict[str, float]] = {}  # software SL/TP fallback
        self._active_position_mode: str = config.position_mode
        self._sell_reject_alert_times: dict[str, float] = {}  # symbol → last alert timestamp
        # Set to False after first -4120 to skip exchange conditional order attempts.
        # Software SL/TP monitor (30 s) acts as sole protection in that case.
        self._exchange_conditional_supported: bool = True
        self._sl_cooldown_timestamps: dict[str, float] = {}  # symbol → Unix time of last SL close
        self._account_balance: float = 0.0
        self._recent_close_pnl: dict[str, float] = {}  # symbol → net realized PnL of last close
        # symbol → reason. Set when a fill is final but a piece of local
        # accounting (fill price, PnL, balance) could not be resolved. These
        # are announced, never guessed, and cleared once the gap is settled.
        self._accounting_gaps: dict[str, str] = {}
        # symbol → realized PnL that could not be fed to the circuit breakers
        # because no live balance was available at close time. Folded into the
        # next close that *does* have one.
        self._deferred_risk_pnl: dict[str, float] = {}

    async def __aenter__(self) -> FuturesTradingExecutor:
        if not self._config.enabled:
            self._logger.info("FuturesTradingExecutor disabled (futures.enabled=false)")
            return self

        if not self._config.api_key or not self._config.api_secret:
            self._logger.error("Futures trading enabled but API keys are missing")
            raise RuntimeError("API keys missing for enabled futures trading")

        self._client = BinanceFuturesClient(
            api_key=self._config.api_key,
            api_secret=self._config.api_secret,
            test_mode=self._config.test_mode,
        )
        await self._client.__aenter__()
        await self._client.load_exchange_info(self._config.symbols)
        await self._notifier.__aenter__()
        await self._announce_entry_control()
        self._metrics.start_trading()

        try:
            account_position_mode = await self._client.get_position_mode()
            self._active_position_mode = account_position_mode
            if account_position_mode != self._config.position_mode:
                self._logger.warning(
                    "Configured futures position_mode=%s but account mode is %s. Using account mode.",
                    self._config.position_mode,
                    account_position_mode,
                )
        except Exception as exc:
            self._logger.warning(
                "Failed to read futures account position mode, using config value '%s': %s",
                self._config.position_mode,
                exc,
            )
            self._active_position_mode = self._config.position_mode

        await self._recover_open_positions()

        # Set default leverage for all configured symbols
        for symbol in self._config.symbols:
            try:
                await self._client.set_leverage(symbol, self._config.default_leverage)
                self._logger.info("Set leverage %dx for %s", self._config.default_leverage, symbol)
            except Exception as exc:
                self._logger.warning("Failed to set leverage for %s: %s", symbol, exc)

        self._logger.info("FuturesTradingExecutor initialized")
        return self

    async def _announce_entry_control(self) -> None:
        """Read the brake at startup so its state is visible before the first signal.

        Without this the metrics stay NaN until the first order attempt, which can
        be days on a quiet strategy - and that means a disarmed agent looks
        identical to one that simply has not traded.
        """
        state = await self._entry_control.state(force=True)
        self._metrics.record_entry_control(
            armed=state.armed,
            reduce_only=state.reduce_only,
            read_age_s=self._entry_control.last_success_age_s,
            fault=state.source == "fail_closed",
        )
        if state.entries_allowed:
            self._logger.info("Entry control: %s", state.describe())
        else:
            self._logger.warning("Entry control: %s", state.describe())

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)
        await self._notifier.__aexit__(exc_type, exc, tb)
        self._metrics.stop_trading()
        self._logger.info("FuturesTradingExecutor stopped")

    def _run_guards(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        signal_confidence: float = 0.0,
        price: float | None = None,
        order_notional: float | None = None,
        market: str = "futures",
        is_exit: bool | None = None,
    ) -> None:
        if self._guard_pipeline is None:
            return
        context = GuardContext(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            portfolio_value=portfolio_value,
            signal_confidence=signal_confidence,
            order_notional=order_notional,
            # Existing exposure for this pair, so the position-limit guard adds
            # the incoming order to what is ALREADY held instead of measuring
            # the new order from zero.
            current_position=self._risk_manager.get_position_notional(symbol, market),
            is_exit=is_exit,
        )
        result = self._guard_pipeline.check(context)
        if result.blocked:
            self._logger.warning("Guard blocked %s %s: %s", side, symbol, result.reason)
            raise RuntimeError(f"Guard blocked: {result.reason}")
        self._logger.debug("Guard passed for %s %s: %s", side, symbol, result.reason)

    def _timeframe_seconds(self) -> int:
        tf = (self._config.timeframe or "").strip().lower()
        if tf.endswith("m"):
            return int(tf[:-1]) * 60
        if tf.endswith("h"):
            return int(tf[:-1]) * 3600
        if tf.endswith("d"):
            return int(tf[:-1]) * 86400
        return 0

    def _estimate_bars_held(self, entry_time: datetime | None) -> int | None:
        if entry_time is None:
            return None
        tf_seconds = self._timeframe_seconds()
        if tf_seconds <= 0:
            return None
        now = datetime.now(UTC)
        elapsed = (now - entry_time).total_seconds()
        if elapsed < 0:
            return 0
        return int(elapsed // tf_seconds)

    @staticmethod
    def _summarize_user_trades(trades: list[FuturesUserTrade]) -> tuple[float | None, float | None]:
        total_qty = sum(trade.quantity for trade in trades)
        if total_qty <= 0:
            return None, None
        fill_price = sum(trade.price * trade.quantity for trade in trades) / total_qty
        realized_pnl = sum(trade.realized_pnl for trade in trades)
        return fill_price, realized_pnl

    async def _resolve_close_fill(
        self,
        symbol: str,
        order: FuturesOrderInfo,
        closing_side: str,
    ) -> tuple[float | None, float | None]:
        """Return (fill_price, realized_pnl) from exchange data only.

        ``order.price`` is the venue's average fill price, so it is a valid
        source; the trade history is preferred because it also carries the
        realized PnL. Nothing here falls back to the mark/ticker price: a
        guessed exit price is indistinguishable from a real one once booked.
        """
        price = float(order.price) if order.price and order.price > 0 else None
        realized_pnl: float | None = None
        if order.order_id:
            exchange_price, exchange_pnl = await self._fetch_close_execution_details(
                symbol, str(order.order_id), closing_side
            )
            price = exchange_price if exchange_price is not None else price
            realized_pnl = exchange_pnl
        return price, realized_pnl

    async def _fetch_close_execution_details(
        self,
        symbol: str,
        ticket_id: str | None,
        closing_side: str = "SELL",
    ) -> tuple[float | None, float | None]:
        if self._client is None:
            return None, None

        if ticket_id:
            try:
                order_trades = await self._client.get_user_trades(symbol, order_id=ticket_id)
                if order_trades:
                    return self._summarize_user_trades(order_trades)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "Failed to fetch Binance close fills for %s order %s: %s",
                    symbol,
                    ticket_id,
                    exc,
                )

        try:
            recent_trades = await self._client.get_user_trades(symbol, limit=20)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Failed to fetch recent Binance fills for %s: %s", symbol, exc)
            return None, None

        close_trades = [
            trade
            for trade in recent_trades
            if trade.side == closing_side and abs(trade.realized_pnl) > 0
        ]
        if not close_trades:
            return None, None

        latest_order_id = max(close_trades, key=lambda trade: trade.time).order_id
        latest_order_trades = [trade for trade in close_trades if trade.order_id == latest_order_id]
        return self._summarize_user_trades(latest_order_trades)

    async def _resolve_close_execution_details(
        self,
        symbol: str,
        tracked_orders: dict[str, str],
    ) -> CloseExecutionDetails:
        sl_order_id = tracked_orders.get("sl_order_id")
        tp_order_id = tracked_orders.get("tp_order_id")

        async def _is_filled(order_id: str | None) -> bool:
            if not order_id or self._client is None:
                return False
            is_algo = (
                order_id == tracked_orders.get("sl_order_id") and tracked_orders.get("sl_is_algo")
            ) or (
                order_id == tracked_orders.get("tp_order_id") and tracked_orders.get("tp_is_algo")
            )
            try:
                if is_algo:
                    status = await self._client.get_algo_order_status(order_id, symbol)
                    return status.status == "TRIGGERED"
                status = await self._client.get_order_status(symbol, order_id)
                return status.status == "FILLED"
            except Exception as exc:  # noqa: BLE001
                self._logger.debug(
                    "Could not read order status for %s/%s while resolving close reason: %s",
                    symbol,
                    order_id,
                    exc,
                )
                return False

        close_reason: str | None = None
        close_ticket: str | None = None
        confirmed_fill = False
        if await _is_filled(sl_order_id):
            close_reason = "stop_loss"
            close_ticket = sl_order_id
            confirmed_fill = True
        elif await _is_filled(tp_order_id):
            close_reason = "take_profit"
            close_ticket = tp_order_id
            confirmed_fill = True

        fill_price: float | None = None
        realized_pnl: float | None = None
        if confirmed_fill and close_ticket:
            fill_price, realized_pnl = await self._fetch_close_execution_details(
                symbol, close_ticket
            )
            if fill_price is None or realized_pnl is None:
                self._logger.warning(
                    "Confirmed %s fill for %s but Binance fill/PnL lookup was unavailable",
                    close_reason or "exchange",
                    symbol,
                )
        return CloseExecutionDetails(
            reason=close_reason,
            ticket_id=close_ticket,
            fill_price=fill_price,
            realized_pnl=realized_pnl,
            confirmed_fill=confirmed_fill,
        )

    @staticmethod
    def _resolve_risk_close_pnl(
        booked_pnl: float | None,
        exchange_realized_pnl: float | None,
        close_price: float | None,
        entry_px: float,
        qty: float,
    ) -> float | None:
        """Best-effort realized PnL for circuit-breaker accounting on confirmed closes."""
        if booked_pnl is not None:
            return booked_pnl
        if exchange_realized_pnl is not None:
            return exchange_realized_pnl
        if close_price is not None and entry_px > 0 and qty > 0 and close_price > 0:
            return (close_price - entry_px) * qty
        return None

    @staticmethod
    def _risk_fallback_close_price(
        close_details: CloseExecutionDetails,
        sl_px: float,
        tp_px: float,
    ) -> float | None:
        """Conservative close price for risk accounting when trade fill lookup fails."""
        if close_details.fill_price is not None and close_details.fill_price > 0:
            return close_details.fill_price
        if close_details.reason == "stop_loss" and sl_px > 0:
            return sl_px
        if close_details.reason == "take_profit" and tp_px > 0:
            return tp_px
        return None

    async def run(self) -> None:
        """Main futures trading loop."""
        if not self._config.enabled:
            self._logger.info("Futures trading executor loop skipped (not enabled)")
            return

        self._running = True
        self._logger.info("Starting futures trading execution loop...")

        try:
            while self._running:
                await self._monitor_and_update()
                await asyncio.sleep(30)  # Check every 30 seconds
        except asyncio.CancelledError:
            self._logger.info("Futures trading execution loop cancelled")

    async def _monitor_and_update(self) -> None:
        """Monitor positions and update metrics."""
        # This loop only ever CLOSES positions (software SL/TP, trailing stop,
        # time stop) and reads account state -- it never opens one. The kill
        # switch and circuit breakers exist to stop NEW risk from being
        # taken; gating the close path on them means a tripped breaker stops
        # the software stop-loss from working, which turns a protective exit
        # into a trap on an open losing position. Use is_close_allowed(),
        # which still respects a reconciliation block (we must not close
        # something we are not sure we are holding).
        close_allowed, reason = self._risk_manager.is_close_allowed()
        if not close_allowed:
            self._logger.warning(f"Futures position monitoring blocked: {reason}")
            return

        # Get account info
        try:
            account_info = await self._client.get_account_info()
            self._account_balance = account_info.total_margin_balance
            self._metrics.update_account_balance(
                total_wallet=account_info.total_margin_balance,
                available=account_info.available_balance,
            )
        except Exception as exc:
            self._logger.error("Failed to get futures account info: %s", exc)
            self._metrics.record_api_error("get_account_info", str(type(exc).__name__))

        # Update position risks and check liquidation buffer
        for symbol in self._config.symbols:
            try:
                positions = await self._client.get_position_risk(symbol)
                for pos in positions:
                    # Check liquidation buffer
                    if pos.liquidation_price > 0:
                        risk_side = pos.position_side
                        if risk_side not in {"LONG", "SHORT"}:
                            if pos.position_amt > 0:
                                risk_side = "LONG"
                            elif pos.position_amt < 0:
                                risk_side = "SHORT"
                            else:
                                continue

                        allowed, reason = self._risk_manager.check_liquidation_buffer(
                            mark_price=pos.mark_price,
                            liquidation_price=pos.liquidation_price,
                            position_side=risk_side,
                            buffer_pct=self._config.liquidation_buffer_pct,
                        )
                        if not allowed:
                            self._logger.critical(
                                "LIQUIDATION RISK for %s %s: %s",
                                symbol,
                                pos.position_side,
                                reason,
                            )
                            await self._notifier.send_alert(
                                f"LIQUIDATION RISK: {symbol} {pos.position_side}\n{reason}"
                            )

                    # Update internal position tracking
                    self._positions[symbol] = {
                        "side": pos.position_side,
                        "amount": pos.position_amt,
                        "entry_price": pos.entry_price,
                        "mark_price": pos.mark_price,
                        "liquidation_price": pos.liquidation_price,
                        "leverage": pos.leverage,
                        "unrealized_pnl": pos.unrealized_pnl,
                    }

                # Position closed by exchange-side SL/TP — clean up tracking
                if not positions and symbol in self._sl_tp_orders:
                    self._logger.info(
                        "Position %s closed by exchange SL/TP — clearing order tracking",
                        symbol,
                    )
                    pos_state = self._positions.get(symbol, {})
                    entry_px = float(pos_state.get("entry_price", 0.0))
                    qty = float(pos_state.get("amount", 0.0))
                    tracked_orders = self._sl_tp_orders.get(symbol, {})
                    sw_prices = self._sl_tp_prices.get(symbol, {})
                    sl_px = sw_prices.get("sl_price", 0.0)
                    close_details = await self._resolve_close_execution_details(
                        symbol=symbol,
                        tracked_orders=tracked_orders,
                    )
                    if close_details.confirmed_fill and close_details.ticket_id:
                        alert_fill_price = close_details.fill_price
                        if alert_fill_price is None:
                            self._logger.warning(
                                "Confirmed exchange close for %s but fill price unavailable — "
                                "skipping close alert",
                                symbol,
                            )

                        if (
                            close_details.reason == "stop_loss"
                            and self._config.sl_cooldown_minutes > 0
                        ):
                            cooldown_close_px = (
                                alert_fill_price if alert_fill_price is not None else sl_px
                            )
                            if cooldown_close_px > 0:
                                self._sl_cooldown_timestamps[symbol] = time.time()
                                self._logger.info(
                                    "SL cooldown started for %s (close=%.4f sl=%.4f)",
                                    symbol,
                                    cooldown_close_px,
                                    sl_px,
                                )

                        bars_held: int | None = None
                        if self._portfolio_manager is not None:
                            tracked_position = self._portfolio_manager.get_position(
                                symbol, market="futures"
                            )
                            if tracked_position is not None:
                                bars_held = self._estimate_bars_held(tracked_position.entry_time)

                        booked_pnl: float | None = None
                        if alert_fill_price is not None and self._portfolio_manager is not None:
                            try:
                                _, booked_pnl = await self._portfolio_manager.close_position(
                                    symbol=symbol,
                                    price=alert_fill_price,
                                    order_id=close_details.ticket_id,
                                    market="futures",
                                    closing_side="SELL",
                                    realized_pnl_override=close_details.realized_pnl,
                                )
                            except ValueError:
                                booked_pnl = close_details.realized_pnl

                        if (
                            booked_pnl is not None
                            and alert_fill_price is not None
                            and entry_px > 0
                            and qty > 0
                            and alert_fill_price > 0
                        ):
                            await self._notifier.send_trade_alert(
                                symbol=symbol,
                                side="SELL",
                                quantity=qty,
                                price=alert_fill_price,
                                pnl=booked_pnl,
                                market="futures",
                                entry_price=entry_px,
                                close_reason=close_details.reason,
                                bars_held=bars_held,
                                ticket_id=close_details.ticket_id,
                            )
                        elif alert_fill_price is not None:
                            self._logger.warning(
                                "Confirmed exchange close for %s but booked PnL unavailable — "
                                "skipping close alert",
                                symbol,
                            )

                        risk_close_price = self._risk_fallback_close_price(
                            close_details, sl_px, sw_prices.get("tp_price", 0.0)
                        )
                        risk_pnl = self._resolve_risk_close_pnl(
                            booked_pnl=booked_pnl,
                            exchange_realized_pnl=close_details.realized_pnl,
                            close_price=risk_close_price,
                            entry_px=entry_px,
                            qty=qty,
                        )
                        if risk_pnl is not None:
                            await self._record_risk_close(symbol, risk_pnl, self._account_balance)
                        else:
                            self._logger.warning(
                                "Confirmed exchange close for %s but no usable PnL for risk "
                                "recording — daily-loss circuit breaker not updated",
                                symbol,
                            )
                    else:
                        self._logger.info(
                            "Position %s flat on exchange with no confirmed SL/TP fill — "
                            "clearing tracking only",
                            symbol,
                        )

                    self._sl_tp_orders.pop(symbol, None)
                    self._sl_tp_prices.pop(symbol, None)
                    self._positions.pop(symbol, None)

                # Software exits: enforce SL/TP, ATR trailing, and time stops.
                sw = self._sl_tp_prices.get(symbol)
                if sw and positions:
                    mark = positions[0].mark_price
                    sl = sw.get("sl_price", 0.0)
                    tp = sw.get("tp_price", 0.0)
                    entry_price = sw.get("entry_price", 0.0)
                    atr_14 = sw.get("atr_14", 0.0)
                    high_water_mark = max(sw.get("high_water_mark", entry_price), mark)
                    sw["high_water_mark"] = high_water_mark
                    if atr_14 > 0 and high_water_mark >= (
                        entry_price + self._config.trailing_activate_atr * atr_14
                    ):
                        trailing_sl = high_water_mark - self._config.trailing_offset_atr * atr_14
                        if trailing_sl > sl:
                            sl = trailing_sl
                            sw["sl_price"] = trailing_sl

                    opened_at = sw.get("opened_at", 0.0)
                    time_stop = (
                        self._config.time_stop_minutes > 0
                        and opened_at > 0
                        and (time.time() - opened_at) / 60 >= self._config.time_stop_minutes
                    )
                    sw_close_reason: str | None = None
                    if sl > 0 and mark <= sl:
                        sw_close_reason = "stop_loss"
                    elif tp > 0 and mark >= tp:
                        sw_close_reason = "take_profit"
                    elif time_stop:
                        sw_close_reason = "time_stop"

                    if sw_close_reason is not None:
                        reason_str = (
                            f"SL {sl:.4f}"
                            if sw_close_reason == "stop_loss"
                            else (
                                f"TP {tp:.4f}"
                                if sw_close_reason == "take_profit"
                                else f"time stop {self._config.time_stop_minutes:.0f}m"
                            )
                        )
                        self._logger.warning(
                            "Software %s triggered for %s: mark=%.4f — closing position",
                            reason_str,
                            symbol,
                            mark,
                        )
                        sw_pos_state = self._positions.get(symbol, {})
                        sw_entry_px = float(sw_pos_state.get("entry_price", 0.0))
                        bars_held: int | None = None
                        if self._portfolio_manager is not None:
                            tracked_position = self._portfolio_manager.get_position(
                                symbol, market="futures"
                            )
                            if tracked_position is not None:
                                bars_held = self._estimate_bars_held(tracked_position.entry_time)
                        # Snapshot the software bracket before cancelling it so a
                        # partial fill can restore protection for the remainder.
                        sw_snapshot = dict(sw)
                        self._sl_tp_prices.pop(symbol, None)
                        try:
                            held_qty = abs(float(positions[0].position_amt))
                            # Derive the closing side from the position's sign:
                            # a hardcoded SELL/LONG would open exposure instead of
                            # closing a short leg.
                            close_side, close_leg = self._close_leg_for(positions[0].position_amt)
                            mode = self._position_mode()
                            await self._cancel_sl_tp_orders(symbol)
                            close_order = await self._client.place_order(
                                symbol=symbol,
                                side=close_side,
                                quantity=held_qty,
                                order_type="MARKET",
                                reduce_only=mode == "one-way",
                                position_side="BOTH" if mode == "one-way" else close_leg,
                            )
                            self._logger.info(
                                "Software %s close order placed for %s", reason_str, symbol
                            )
                            executed = self._executed_quantity(close_order)
                            if executed is None:
                                # The order may well have filled; we just cannot
                                # say how much. Keep tracking and protection
                                # rather than releasing a slot on a guess.
                                self._sl_tp_prices[symbol] = sw_snapshot
                                self._mark_accounting_gap(
                                    symbol,
                                    f"software {sw_close_reason} close reported no executed quantity",
                                )
                                raise PostFillAccountingError(
                                    f"{symbol} {reason_str} close reported no executed quantity; "
                                    "position tracking left registered"
                                )
                            if executed - held_qty > _QUANTITY_TOLERANCE:
                                self._sl_tp_prices[symbol] = sw_snapshot
                                self._mark_accounting_gap(
                                    symbol,
                                    f"software close overshoot: executed {executed:.8f} "
                                    f"> held {held_qty:.8f}",
                                )
                                raise PositionStateDivergence(
                                    f"{symbol} {reason_str} close executed {executed:.8f} "
                                    f"but only {held_qty:.8f} is held"
                                )

                            # Only exchange data may price this fill. The previous
                            # fallback to the mark price invented a PnL that was
                            # then booked into the ledger and the breakers.
                            close_fill_price, close_realized_pnl = await self._resolve_close_fill(
                                symbol, close_order, close_side
                            )
                            remaining_qty = max(0.0, held_qty - executed)

                            if remaining_qty > _QUANTITY_TOLERANCE:
                                # Partial close: the rest is still open and now
                                # unprotected at the exchange. Reconcile sizes
                                # exactly like a signal-driven exit.
                                if (
                                    self._portfolio_manager is not None
                                    and close_fill_price is not None
                                ):
                                    try:
                                        (
                                            _,
                                            close_realized_pnl,
                                            _ledger_remaining,
                                        ) = await self._portfolio_manager.reduce_position(
                                            symbol=symbol,
                                            quantity=executed,
                                            price=close_fill_price,
                                            order_id=str(close_order.order_id),
                                            market="futures",
                                            closing_side=close_side,
                                        )
                                    except ValueError as reduce_exc:
                                        self._logger.error(
                                            "Partial software close of %s not booked: %s",
                                            symbol,
                                            reduce_exc,
                                        )
                                        self._mark_accounting_gap(
                                            symbol, "partial software close not in ledger"
                                        )
                                elif self._portfolio_manager is not None:
                                    try:
                                        await self._portfolio_manager.update_quantity(
                                            symbol, remaining_qty, market="futures"
                                        )
                                    except ValueError as reduce_exc:
                                        self._logger.error(
                                            "Partial software close of %s not reflected: %s",
                                            symbol,
                                            reduce_exc,
                                        )
                                    self._mark_accounting_gap(
                                        symbol,
                                        "software close fill price unknown: ledger size "
                                        "corrected, PnL deferred",
                                    )

                                state = self._positions.setdefault(symbol, {})
                                state["amount"] = remaining_qty
                                # Restore protection for what is still held.
                                self._sl_tp_prices[symbol] = sw_snapshot
                                if sw_entry_px > 0:
                                    notional = remaining_qty * sw_entry_px
                                    if not self._risk_manager.adjust_open_position(
                                        symbol, notional, market="futures"
                                    ):
                                        self._risk_manager.register_open_position(
                                            symbol, notional, sw_entry_px, market="futures"
                                        )
                                self._logger.warning(
                                    "Software %s close for %s partially filled: closed %.4f, "
                                    "remaining %.4f still open",
                                    reason_str,
                                    symbol,
                                    executed,
                                    remaining_qty,
                                )
                            else:
                                # Fully flat at the exchange.
                                if (
                                    self._portfolio_manager is not None
                                    and close_fill_price is not None
                                ):
                                    try:
                                        (
                                            _,
                                            close_realized_pnl,
                                        ) = await self._portfolio_manager.close_position(
                                            symbol=symbol,
                                            price=close_fill_price,
                                            market="futures",
                                            closing_side=close_side,
                                            realized_pnl_override=close_realized_pnl,
                                        )
                                    except ValueError:
                                        pass
                                elif self._portfolio_manager is not None:
                                    try:
                                        await self._portfolio_manager.update_quantity(
                                            symbol, 0.0, market="futures"
                                        )
                                    except ValueError:
                                        pass
                                    self._mark_accounting_gap(
                                        symbol,
                                        "software close fill price unknown: ledger size "
                                        "cleared, PnL deferred",
                                    )
                                self._positions.pop(symbol, None)
                                if (
                                    sw_close_reason == "stop_loss"
                                    and self._config.sl_cooldown_minutes > 0
                                ):
                                    self._sl_cooldown_timestamps[symbol] = time.time()
                                pnl = close_realized_pnl
                                if pnl is not None and close_order.order_id:
                                    await self._notifier.send_trade_alert(
                                        symbol=symbol,
                                        side=close_side,
                                        quantity=executed,
                                        price=(
                                            close_fill_price
                                            if close_fill_price is not None
                                            else sw_entry_px
                                        ),
                                        pnl=pnl,
                                        market="futures",
                                        entry_price=sw_entry_px if sw_entry_px > 0 else None,
                                        close_reason=sw_close_reason,
                                        bars_held=bars_held,
                                        ticket_id=close_order.order_id,
                                    )
                                if pnl is not None:
                                    await self._record_risk_close(
                                        symbol, pnl, self._account_balance
                                    )
                                else:
                                    # Release the slot (flat) but never invent a
                                    # PnL for the breakers.
                                    await self._record_risk_close(
                                        symbol, None, self._account_balance
                                    )
                                    self._logger.warning(
                                        "Software %s close for %s missing booked PnL — "
                                        "close alert skipped, risk PnL deferred",
                                        reason_str,
                                        symbol,
                                    )
                        except Exception as close_exc:
                            self._logger.error(
                                "Failed to close %s after software %s: %s",
                                symbol,
                                reason_str,
                                close_exc,
                            )

            except Exception as exc:
                self._logger.error("Failed to get position risk for %s: %s", symbol, exc)

    def _mark_accounting_gap(self, symbol: str, reason: str) -> None:
        """Announce a deferred post-fill accounting decision instead of guessing.

        Records the reason on the executor (so status surfaces it), publishes a
        dedicated metric and logs. Never swallowed: an unaccounted fill that
        nobody can see is the failure mode this exists to prevent.
        """
        self._accounting_gaps[symbol] = reason
        self._metrics.record_accounting_gap(symbol, reason)
        self._logger.error("Accounting gap for %s: %s", symbol, reason)

    def _clear_accounting_gap(self, symbol: str) -> None:
        self._accounting_gaps.pop(symbol, None)

    async def _live_account_balance(self, cached: float | None = None) -> float:
        """Return a positive account balance, reading the exchange when needed.

        ``record_trade`` cannot apply percentage-based limits against 0, so a
        close that runs before the monitor loop has ever refreshed the account
        would silently skip the loss/circuit-breaker checks. Read the live
        balance first; return 0.0 only when the exchange cannot be read, and let
        the caller defer with a marker.
        """
        candidate = self._account_balance if cached is None else cached
        if candidate is not None and candidate > 0:
            return float(candidate)
        if self._client is None:
            return 0.0
        try:
            account_info = await self._client.get_account_info()
            balance = float(getattr(account_info, "total_margin_balance", 0.0) or 0.0)
        except Exception as exc:  # noqa: BLE001 - deferred, not fatal
            self._logger.warning("Could not read account balance for risk accounting: %s", exc)
            return 0.0
        if balance > 0:
            self._account_balance = balance
            self._metrics.update_account_balance(
                total_wallet=balance,
                available=float(getattr(account_info, "available_balance", balance) or balance),
            )
        return balance

    async def _record_risk_close(
        self,
        symbol: str,
        pnl: float | None,
        portfolio_value: float,
    ) -> None:
        """Release futures risk state and feed realized P&L into circuit breakers.

        The position is always released (the exposure is gone at the exchange),
        but P&L is only fed to the breakers against a real balance. With no live
        balance the amount is deferred with an explicit marker and folded into
        the next close that has one - bookkeeping never invents a base to
        divide by.
        """
        if pnl is not None:
            balance = await self._live_account_balance(portfolio_value)
            deferred = self._deferred_risk_pnl.pop(symbol, 0.0)
            total_pnl = pnl + deferred
            if balance > 0:
                self._risk_manager.record_trade(symbol, total_pnl, balance)
                self._metrics.record_realized_pnl(symbol, total_pnl)
                self._clear_accounting_gap(symbol)
            else:
                self._deferred_risk_pnl[symbol] = total_pnl
                self._metrics.record_realized_pnl(symbol, pnl)
                self._mark_accounting_gap(
                    symbol,
                    "risk-close deferred: no live balance for circuit-breaker accounting",
                )
        self._risk_manager.register_close_position(symbol, market="futures")

    def _can_prepare_request_clock(self) -> bool:
        """True only for a client that exposes the preparation seam.

        Checked on the class, not the instance, so lightweight test doubles do
        not grow an arbitrary attribute that fails when awaited.
        """
        return getattr(type(self._client), "prepare_signed_request", None) is not None

    async def _prepare_request_clock(self) -> bool:
        """Run the exchange clock sync before the final admission read."""
        if self._client is None or not self._can_prepare_request_clock():
            return False
        await self._client.prepare_signed_request()
        return True

    async def _prepare_request_clock_or_reject(self, staged_order_id: str | None) -> bool:
        """Prepare the clock; a failure must not strand a staged order."""
        try:
            return await self._prepare_request_clock()
        except BaseException:
            if staged_order_id is not None and self._staged_manager is not None:
                await self._staged_manager.mark_rejected(
                    staged_order_id,
                    "exchange clock sync failed before submission",
                )
            raise

    def _prepared_clock_expired(self) -> bool:
        """True when the prepared clock can no longer stamp a fresh POST.

        A missing clock counts as expired: another submission may have consumed
        the shared marker while this one was awaiting its admission read, which
        would send the POST back to a lazy sync after the decision.
        """
        if self._client is None or not self._can_prepare_request_clock():
            return False
        ttl = float(getattr(type(self._client), "PREPARED_REQUEST_TTL_S", 0.0))
        age = self._client.prepared_request_age_s()
        return age is None or age > ttl

    async def _admit_entry(self, symbol: str, staged_order_id: str | None) -> None:
        """Take the final, freshest entry decision; reject the stage on refusal."""
        try:
            await admit(self._entry_control, self._metrics, symbol)
        except BaseException:
            if staged_order_id is not None and self._staged_manager is not None:
                await self._staged_manager.mark_rejected(
                    staged_order_id, "entry admission failed before submission"
                )
            raise

    @staticmethod
    def _executed_quantity(order: FuturesOrderInfo) -> float | None:
        """Exchange-reported executed quantity, or ``None`` when it is unknown.

        No fallback to the requested size: a FILLED order that does not report
        what it filled is a data gap, and assuming the request is the fill is
        how a tracker releases risk for volume that was never closed.
        """
        executed = getattr(order, "executed_quantity", None)
        if isinstance(executed, bool) or not isinstance(executed, (int, float)):
            return None
        return float(executed) if executed > 0 else None

    def _position_mode(self) -> str:
        """Return the validated position mode, refusing to guess.

        Anything other than the two modes Binance defines would build an order
        the exchange rejects (or, worse, one it interprets differently), so an
        unknown mode is a hard error on the money path.
        """
        mode = (self._active_position_mode or "").strip().lower()
        if mode not in {"one-way", "hedge"}:
            raise RuntimeError(
                f"Unsupported futures position mode {self._active_position_mode!r}; "
                "refusing to place an order"
            )
        return mode

    @staticmethod
    def _close_leg_for(position_amt: float) -> tuple[str, str]:
        """Return (side, position_side) that reduces a position of this sign."""
        if position_amt < 0:
            return "BUY", "SHORT"
        return "SELL", "LONG"

    async def _verify_hedge_reduce_holdings(
        self,
        symbol: str,
        side: str,
        position_side: str,
        quantity: float,
    ) -> float:
        """Prove a hedge-mode close against the leg the exchange actually holds.

        Binance rejects ``reduceOnly`` in hedge mode: the reduction is enforced
        by naming the position side, so an order is only a close if that leg
        exists, points the right way, and is at least as large as the order.
        Shape checks (side/leg consistency) are necessary but not sufficient -
        without this read, a mislabelled exit opens exposure instead of closing
        it, and an oversized one silently "closes" more than we hold.

        Returns the held quantity. Raises (fail-closed) when the leg cannot be
        verified.
        """
        if self._client is None:
            raise RuntimeError("hedge reduce rejected: futures client not initialized")
        try:
            positions = await self._client.get_position_risk(symbol)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"hedge reduce rejected: could not verify held {position_side} leg for {symbol}: {exc}"
            ) from exc

        held = None
        for pos in positions or []:
            if pos.position_side == position_side and float(pos.position_amt) != 0.0:
                held = pos
                break
        if held is None:
            raise RuntimeError(
                f"hedge reduce rejected: no open {position_side} position for {symbol}"
            )

        amount = float(held.position_amt)
        if (position_side == "LONG" and amount <= 0) or (position_side == "SHORT" and amount >= 0):
            raise RuntimeError(
                f"hedge reduce rejected: {symbol} {position_side} leg has amount {amount}"
            )

        held_qty = abs(amount)
        if quantity - held_qty > _QUANTITY_TOLERANCE:
            raise RuntimeError(
                f"hedge reduce rejected: {quantity} exceeds held {position_side} "
                f"quantity {held_qty} for {symbol}"
            )
        # The shape must also match the leg being named, otherwise the exchange
        # would treat this as an increase on the opposite leg.
        if (side, position_side) not in {("SELL", "LONG"), ("BUY", "SHORT")}:
            raise ValueError("invalid hedge reduce-only side and position_side")
        return held_qty

    async def _apply_partial_reduce(
        self,
        symbol: str,
        side: str,
        order: FuturesOrderInfo,
        closed_qty: float,
        remaining_qty: float,
        exchange_held: float | None = None,
    ) -> float | None:
        """Keep the unclosed remainder registered after a partial fill.

        The exchange fill is final, so local tracking, the portfolio ledger and
        the risk manager are all reduced to the remaining volume in place of
        the full-close path. Returns the realized PnL booked for this fill.

        Two invariants:

        * There is no PnL without a price. An unknown fill price is resolved
          from the exchange, and if that fails the ledger's *size* is corrected
          while PnL is left unbooked with an explicit marker. A fabricated
          price (0.0 gives a 100% loss) would trip the loss breaker on a trade
          that never happened.
        * Tracked size never exceeds the exchange-derived remainder. The
          portfolio ledger's own remainder is clamped against
          ``held - executed`` so a stale ledger cannot inflate open exposure.
        """
        tracked = self._positions.get(symbol, {})
        entry_price = float(tracked.get("entry_price") or 0.0)
        tracked_qty = abs(float(tracked.get("amount") or 0.0))

        fill_price = float(order.price) if order.price and order.price > 0 else None
        close_pnl: float | None = None
        if fill_price is None:
            # Ask the exchange what actually filled before deciding anything.
            exchange_price, exchange_pnl = await self._fetch_close_execution_details(
                symbol, str(order.order_id) if order.order_id else None, side
            )
            fill_price = exchange_price
            close_pnl = exchange_pnl
        if fill_price is not None and entry_price > 0 and close_pnl is None:
            direction = 1.0 if side.upper() == "SELL" else -1.0
            close_pnl = (fill_price - entry_price) * closed_qty * direction

        # Exchange-derived ceiling. Never trust a remainder larger than what
        # the venue says is left after this fill.
        ceil_qty = (
            max(0.0, exchange_held - closed_qty)
            if exchange_held is not None and exchange_held > 0
            else max(0.0, tracked_qty - closed_qty)
        )
        remaining_qty = min(max(0.0, remaining_qty), ceil_qty)

        if self._portfolio_manager is not None:
            if fill_price is not None:
                try:
                    (
                        _,
                        portfolio_pnl,
                        portfolio_remaining,
                    ) = await self._portfolio_manager.reduce_position(
                        symbol=symbol,
                        quantity=closed_qty,
                        price=fill_price,
                        order_id=str(order.order_id) if order.order_id else None,
                        market="futures",
                        closing_side=side,
                    )
                except ValueError as exc:
                    self._logger.error(
                        "Partial close of %s not booked in portfolio ledger: %s", symbol, exc
                    )
                    self._mark_accounting_gap(symbol, "partial reduce not in portfolio ledger")
                else:
                    close_pnl = portfolio_pnl
                    # Re-clamp. With a known exchange holding the venue decides
                    # the remainder; without one the ledger is the only other
                    # witness, so never track more than it thinks is left.
                    ledger_remaining = max(0.0, float(portfolio_remaining))
                    if exchange_held is None:
                        remaining_qty = min(remaining_qty, ledger_remaining)
                    elif abs(ledger_remaining - remaining_qty) > _QUANTITY_TOLERANCE:
                        self._mark_accounting_gap(
                            symbol,
                            f"ledger remainder {ledger_remaining:.8f} disagrees with "
                            f"exchange {remaining_qty:.8f} after partial reduce",
                        )
            else:
                # No price anywhere. Correct the ledger size only; booking a
                # price would invent a PnL.
                try:
                    await self._portfolio_manager.update_quantity(
                        symbol, remaining_qty, market="futures"
                    )
                except ValueError as exc:
                    self._logger.error(
                        "Partial close of %s not reflected in portfolio ledger: %s", symbol, exc
                    )
                self._mark_accounting_gap(
                    symbol, "partial reduce fill price unknown: ledger size corrected, PnL deferred"
                )

        if close_pnl is not None:
            balance = await self._live_account_balance()
            self._risk_manager.record_trade(symbol, close_pnl, balance)
            self._metrics.record_realized_pnl(symbol, close_pnl)
        if entry_price > 0:
            notional = remaining_qty * entry_price
            if not self._risk_manager.adjust_open_position(symbol, notional, market="futures"):
                self._risk_manager.register_open_position(
                    symbol,
                    notional,
                    entry_price,
                    market="futures",
                )
        tracked["amount"] = remaining_qty
        self._positions[symbol] = tracked
        if close_pnl is not None:
            self._recent_close_pnl[symbol] = close_pnl
        self._logger.warning(
            "Futures position partially closed: %s %s qty %.4f, remaining %.4f",
            side,
            symbol,
            closed_qty,
            remaining_qty,
        )
        return close_pnl

    async def _verify_hedge_leg_reduced(
        self,
        symbol: str,
        position_side: str,
        held_before: float,
        executed: float,
    ) -> None:
        """Confirm the hedge leg actually shrank by the filled quantity.

        Hedge-mode reduce-only is enforced by the leg, not by a flag, so the only
        proof that the order acted as a close is that the leg got smaller. If it
        did not, booking a close would drop exposure that is still open.
        """
        if self._client is None:
            return
        try:
            positions = await self._client.get_position_risk(symbol)
        except Exception as exc:  # noqa: BLE001
            self._mark_accounting_gap(
                symbol, f"post-fill hedge leg verification unavailable: {exc}"
            )
            return
        after = 0.0
        for pos in positions or []:
            if pos.position_side == position_side:
                after = abs(float(pos.position_amt))
                break
        ceiling = max(0.0, held_before - executed) + _QUANTITY_TOLERANCE
        if after > ceiling:
            self._mark_accounting_gap(
                symbol,
                f"hedge leg not reduced: {position_side} still {after:.8f} after "
                f"closing {executed:.8f} of {held_before:.8f}",
            )
            raise PositionStateDivergence(
                f"{symbol} {position_side} leg still {after:.8f} after closing "
                f"{executed:.8f} of {held_before:.8f}; the exchange did not reduce it"
            )

    async def _settle_reduce_fill(
        self,
        symbol: str,
        side: str,
        order: FuturesOrderInfo,
        exchange_held: float | None,
    ) -> None:
        """Book a filled reduce-only order without ever over-releasing risk.

        Fail-closed rules:

        * An oversized fill (more executed than tracked/held) is an error. The
          tracker and risk slot stay registered so the next entry is blocked
          and reconciliation can repair the books - the previous behaviour
          clamped the excess and popped the tracker, silently freeing a risk
          slot for exposure that something else still holds.
        * An unparseable filled quantity is an error for the same reason.
        * A full close releases the risk slot only when the exchange confirms
          the position is flat (executed covers everything held).
        """
        executed = self._executed_quantity(order)
        if executed is None:
            self._mark_accounting_gap(symbol, "filled reduce reported no executed quantity")
            raise PostFillAccountingError(
                f"Filled reduce for {symbol} reported no executed quantity; "
                "tracked position and risk slot were left registered"
            )

        tracked_state = self._positions.get(symbol)
        tracked_qty = abs(float(tracked_state.get("amount", 0.0) or 0.0)) if tracked_state else 0.0
        reference_qty = exchange_held if exchange_held is not None else tracked_qty
        if reference_qty > 0 and executed - reference_qty > _QUANTITY_TOLERANCE:
            self._mark_accounting_gap(
                symbol,
                f"reduce fill overshoot: executed {executed:.8f} > held {reference_qty:.8f}",
            )
            raise PositionStateDivergence(
                f"Reduce fill for {symbol} executed {executed:.8f} but only "
                f"{reference_qty:.8f} is held; refusing to unregister exposure"
            )

        remaining_qty = max(0.0, reference_qty - executed) if reference_qty > 0 else 0.0
        if exchange_held is not None:
            # Hedge-mode close: verify at the venue that the leg actually shrank
            # before booking anything as closed.
            await self._verify_hedge_leg_reduced(
                symbol, "LONG" if side.upper() == "SELL" else "SHORT", exchange_held, executed
            )
        if remaining_qty > _QUANTITY_TOLERANCE:
            await self._apply_partial_reduce(
                symbol,
                side,
                order,
                executed,
                remaining_qty,
                exchange_held=exchange_held,
            )
            return

        # Fully flat: book the close and release the risk slot. Reaching here
        # means executed covers everything held (overshoot is rejected above).
        close_pnl: float | None = None
        fill_price = float(order.price) if order.price and order.price > 0 else None
        if self._portfolio_manager is not None and fill_price is not None:
            try:
                _, close_pnl = await self._portfolio_manager.close_position(
                    symbol=symbol,
                    price=fill_price,
                    order_id=str(order.order_id),
                    market="futures",
                    closing_side=side,
                )
            except ValueError as exc:
                # Ledger had nothing open (already reconciled): the exchange is
                # flat either way, so release risk and record the gap.
                self._logger.error("Closed %s not booked in portfolio ledger: %s", symbol, exc)
                self._mark_accounting_gap(symbol, "close not in portfolio ledger")
            else:
                if close_pnl is not None:
                    self._recent_close_pnl[symbol] = close_pnl
        elif self._portfolio_manager is not None:
            # Flat, but no fill price: correct the ledger size, never invent a
            # PnL for the closed leg.
            try:
                await self._portfolio_manager.update_quantity(symbol, 0.0, market="futures")
            except ValueError as exc:
                self._logger.error("Closed %s not reflected in portfolio ledger: %s", symbol, exc)
            self._mark_accounting_gap(
                symbol, "close fill price unknown: ledger size cleared, PnL deferred"
            )
        await self._record_risk_close(symbol, close_pnl, self._account_balance)
        # Clear in-memory tracker so max_concurrent_longs frees the slot.
        # The monitor-loop cleanup only fires while _sl_tp_orders still has the
        # symbol, but signal-driven closes pop it beforehand.
        self._positions.pop(symbol, None)
        self._logger.info("Futures position closed: %s %s (qty: %.4f)", side, symbol, executed)

    async def place_futures_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float,
        position_side: str = "LONG",
        reduce_only: bool = False,
        reference_price: float | None = None,
    ) -> FuturesOrderInfo:
        """Place a futures order with risk checks.

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Order side ("BUY" or "SELL")
            quantity: Order quantity (in base asset units)
            position_side: "LONG" or "SHORT"
            reduce_only: If True, order will only reduce position
            reference_price: Current market price used to enforce opening notional limits

        Returns:
            FuturesOrderInfo: Information about placed order

        Raises:
            RuntimeError: If risk checks fail or order placement fails
        """
        if not self._config.enabled:
            raise RuntimeError("Futures trading executor is disabled")

        client = self._client
        if client is None:
            raise RuntimeError("Futures client not initialized")

        mode = self._position_mode()

        # Binance rejects reduceOnly in hedge mode ("cannot be sent in Hedge
        # Mode"), so the reduction is enforced by naming the leg: SELL+LONG can
        # only shrink the long. That makes the *leg* the safety mechanism, which
        # is only sound if the leg is really held. Prove it against the exchange
        # before anything is staged or sent; shape checks alone let a mislabelled
        # or oversized exit open exposure instead of closing it.
        exchange_held: float | None = None
        if reduce_only:
            if mode == "hedge":
                if (side, position_side) not in {("SELL", "LONG"), ("BUY", "SHORT")}:
                    raise ValueError("invalid hedge reduce-only side and position_side")
                exchange_held = await self._verify_hedge_reduce_holdings(
                    symbol, side, position_side, quantity
                )
        elif mode == "hedge" and position_side not in {"LONG", "SHORT"}:
            raise ValueError("hedge-mode orders require position_side LONG or SHORT")

        # A reduce-only BUY closes a short; neither close side is an entry.
        # Exits consult only the close gate: a reconciliation block stays fatal
        # (we may not be closing what we think we are), while kill switches,
        # circuit breakers, leverage and margin limits all describe opening
        # risk and must never trap an exit.
        if reduce_only:
            is_allowed, reason = self._risk_manager.is_close_allowed()
        else:
            is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            self._metrics.record_risk_block(symbol, reason)
            raise RuntimeError(f"Futures trading blocked: {reason}")

        account_info = None
        if not reduce_only:
            # Opening risk still needed below: margin headroom is measured
            # against the live account, and both limit checks are entry-only.
            try:
                account_info = await client.get_account_info()
            except Exception as exc:
                self._logger.error("Failed to get account info: %s", exc)
                raise RuntimeError("Failed to get account info") from exc

            leverage_allowed, leverage_reason = self._risk_manager.check_max_leverage(
                self._config.default_leverage
            )
            if not leverage_allowed:
                self._metrics.record_risk_block(symbol, leverage_reason)
                raise RuntimeError(f"Leverage check failed: {leverage_reason}")

            if reference_price is None or reference_price <= 0:
                raise ValueError("reference_price must be positive for opening futures orders")

            notional_value = quantity * reference_price
            position_allowed, position_reason = self._risk_manager.check_position_limit(
                f"{symbol}:futures",
                notional_value,
                account_info.total_margin_balance,
            )
            if not position_allowed:
                self._metrics.record_risk_block(symbol, position_reason)
                raise RuntimeError(f"Position limit check failed: {position_reason}")

            current_positions = self._positions.get(symbol, {})
            used_margin = (
                abs(current_positions.get("amount", 0))
                * current_positions.get("entry_price", 0)
                / current_positions.get("leverage", 1)
            )

            margin_allowed, margin_reason = self._risk_manager.check_margin_usage(
                used_margin=used_margin,
                available_balance=account_info.available_balance,
            )
            if not margin_allowed:
                self._metrics.record_risk_block(symbol, margin_reason)
                raise RuntimeError(f"Margin check failed: {margin_reason}")

        request_position_side = "BOTH" if mode == "one-way" else position_side
        # In hedge mode the leg (positionSide) carries the reduce-only intent and
        # an explicit reduceOnly is rejected; in one-way mode the flag is the
        # only exchange-enforced guard against flipping the position.
        request_reduce_only = reduce_only and mode == "one-way"
        staged_order_id: str | None = None

        if self._staged_manager is not None:
            staged_order = await self._staged_manager.stage(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type="MARKET",
                metadata={
                    "position_side": request_position_side,
                    "reduce_only": reduce_only,
                    "market": "futures",
                },
            )
            staged_order_id = staged_order.order_id

        # Every awaited preparatory step (account read, staging, the exchange
        # clock sync) runs before the final decision. The clock is prepared
        # here, not inside the POST, so a disarm that lands while the sync is
        # in flight is still observed by the fresh admission read below.
        clock_prepared = await self._prepare_request_clock_or_reject(staged_order_id)
        # Force a fresh decision after all awaited preflight and staging work.
        # Exits do not consult the entry brake.
        if not reduce_only:
            await self._admit_entry(symbol, staged_order_id)
            if clock_prepared and self._prepared_clock_expired():
                # The admission read outlived the prepared clock. Re-sync and
                # re-decide rather than POST a stale timestamp: the risk read
                # must remain the last awaited work before submission.
                await self._prepare_request_clock_or_reject(staged_order_id)
                await self._admit_entry(symbol, staged_order_id)
                if self._prepared_clock_expired():
                    # Two slow reads in a row. An entry can wait for the next
                    # signal; sending it without a fresh timestamp (or behind a
                    # second clock sync) is the race this ordering exists to
                    # prevent. Exits never take this branch.
                    self._metrics.record_risk_block(symbol, "entry_control: stale admission")
                    if staged_order_id is not None and self._staged_manager is not None:
                        await self._staged_manager.mark_rejected(
                            staged_order_id,
                            "entry admission exceeded timestamp freshness window",
                        )
                    raise RuntimeError(
                        "Entry blocked: admission exceeded the timestamp freshness window"
                    )

        # Place order. Only this submission step can be reported as FAILED:
        # once the exchange accepts the order, the exposure may already have
        # changed, so every later problem is an accounting gap, not a failure.
        start_time = time.perf_counter()
        try:
            order = await client.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type="MARKET",
                reduce_only=request_reduce_only,
                position_side=request_position_side,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            self._logger.error("Failed to place futures order: %s", exc)
            if staged_order_id is not None:
                await self._staged_manager.mark_rejected(staged_order_id, str(exc))
            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="FUTURES_MARKET",
                status="FAILED",
                latency_seconds=elapsed,
            )
            self._metrics.record_api_error("place_futures_order", str(type(exc).__name__))
            raise

        # The order is live. A polling hiccup says nothing about the placement.
        if order.status != "FILLED":
            initial_status = order.status
            try:
                for _ in range(4):
                    await asyncio.sleep(0.25)
                    order = await client.get_order_status(symbol, order.order_id)
                    if order.status == "FILLED":
                        self._logger.info(
                            "Futures order %s transitioned %s -> FILLED for %s",
                            order.order_id,
                            initial_status,
                            symbol,
                        )
                        break
            except Exception as exc:  # noqa: BLE001
                self._mark_accounting_gap(
                    symbol, f"fill status poll failed after submission: {type(exc).__name__}"
                )

        elapsed = time.perf_counter() - start_time
        self._metrics.record_order_placed(
            symbol=symbol,
            order_type="FUTURES_MARKET",
            status=order.status,
            latency_seconds=elapsed,
        )

        if order.status != "FILLED":
            return order

        try:
            if staged_order_id is not None:
                await self._staged_manager.mark_completed(staged_order_id, order)
        except Exception as exc:  # noqa: BLE001
            self._mark_accounting_gap(symbol, f"staged order completion not recorded: {exc}")
        self._metrics.record_order_filled(symbol, side)

        try:
            if reduce_only:
                await self._settle_reduce_fill(symbol, side, order, exchange_held)
            else:
                await self._register_open_fill(
                    symbol, side, order, quantity, position_side, reference_price
                )
        except PostFillAccountingError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._mark_accounting_gap(symbol, f"post-fill booking failed: {exc}")
            raise PostFillAccountingError(
                f"{symbol} {side} order {order.order_id} filled but local booking failed: {exc}"
            ) from exc

        return order

    async def _register_open_fill(
        self,
        symbol: str,
        side: str,
        order: FuturesOrderInfo,
        requested_qty: float,
        position_side: str,
        reference_price: float | None,
    ) -> None:
        """Record a filled entry in tracking, the ledger and the risk manager."""
        if symbol not in self._positions:
            self._positions[symbol] = {}
        # New position or added to existing
        executed = self._executed_quantity(order)
        if executed is None:
            # Entries must not be dropped (the exchange holds them), but the
            # assumed size is a bookkeeping gap and has to be visible.
            self._mark_accounting_gap(
                symbol, "entry fill reported no executed quantity: requested size assumed"
            )
        recorded_qty = executed if executed is not None else requested_qty
        if self._portfolio_manager is not None:
            await self._portfolio_manager.open_position(
                symbol=symbol,
                quantity=recorded_qty,
                price=float(order.price) if order.price else 0.0,
                order_id=str(order.order_id),
                market="futures",
                position_side="SHORT" if side == "SELL" else "LONG",
            )
        self._logger.info(
            "Futures position opened: %s %s %s (qty: %.4f, leverage: %dx)",
            side,
            symbol,
            position_side,
            recorded_qty,
            self._config.default_leverage,
        )
        # Immediately reflect filled qty so max_concurrent_longs guard
        # works within the same evaluation cycle (before next monitoring tick).
        self._positions[symbol]["amount"] = recorded_qty
        self._positions[symbol]["side"] = "LONG"
        executed_price = float(order.price) if order.price else reference_price
        if executed_price is None or executed_price <= 0:
            # Cannot value the entry: book nothing rather than a zero notional.
            self._mark_accounting_gap(
                symbol, "entry fill price unknown: risk notional not registered"
            )
            return
        self._risk_manager.register_open_position(
            symbol,
            recorded_qty * executed_price,
            executed_price,
            market="futures",
        )

    async def on_signal(self, signal: Signal) -> None:
        """Handle futures trading signal.

        LONG-only MVP implementation:
        - BUY signal: Open/add to LONG position
        - SELL signal: Close LONG position with reduceOnly

        Args:
            signal: Trading signal with trading_mode="futures"
        """
        if signal.type == SignalType.HOLD:
            return

        if not self._config.enabled:
            self._logger.info("Futures signal ignored (executor disabled): %s", signal)
            return

        if signal.trading_mode != "futures":
            self._logger.warning(
                "Signal trading_mode is '%s' but expected 'futures'. Ignoring.",
                signal.trading_mode,
            )
            return

        if signal.symbol not in self._config.symbols:
            self._logger.info(
                "Futures signal for unmanaged symbol %s. Configured symbols: %s",
                signal.symbol,
                self._config.symbols,
            )
            return

        self._logger.info(
            "Futures signal received: %s %s @ %.2f (mode=%s)",
            signal.type.value,
            signal.symbol,
            signal.price,
            signal.trading_mode,
        )

        try:
            # Get current position info
            positions = await self._client.get_position_risk(signal.symbol)
            current_position = None
            for pos in positions:
                if pos.position_amt != 0:
                    current_position = pos
                    break

            if signal.type == SignalType.BUY:
                if current_position is not None:
                    # Already have a position with active SL/TP — skip
                    self._logger.info(
                        "BUY ignored: already have position for %s with active SL/TP",
                        signal.symbol,
                    )
                else:
                    # No position — check guards before opening new LONG
                    if self._config.max_concurrent_longs > 0:
                        current_longs = sum(
                            1 for p in self._positions.values() if p.get("amount", 0) > 0
                        )
                        if current_longs >= self._config.max_concurrent_longs:
                            self._logger.info(
                                "BUY skipped: max_concurrent_longs=%d already at limit for %s",
                                self._config.max_concurrent_longs,
                                signal.symbol,
                            )
                            return
                    if self._config.sl_cooldown_minutes > 0:
                        last_sl = self._sl_cooldown_timestamps.get(signal.symbol)
                        if last_sl is not None:
                            elapsed_min = (time.time() - last_sl) / 60
                            if elapsed_min < self._config.sl_cooldown_minutes:
                                remaining = int(self._config.sl_cooldown_minutes - elapsed_min)
                                self._logger.info(
                                    "BUY skipped: SL cooldown active for %s (%d min remaining)",
                                    signal.symbol,
                                    remaining,
                                )
                                return
                    order_size = self._calculate_quantity(signal.symbol, signal.price)
                    if self._client is not None:
                        min_qty = self._client.get_min_qty(signal.symbol)
                        if min_qty is not None and order_size < min_qty:
                            self._logger.warning(
                                "BUY skipped: qty %.6f < min_qty %.6f for %s (order_size_usdt=%.2f)",
                                order_size,
                                min_qty,
                                signal.symbol,
                                self._config.order_size_usdt,
                            )
                            await self._notifier.send_alert(
                                f"⚠️ BUY skipped: {signal.symbol} order too small "
                                f"(qty {order_size:.6f} < min {min_qty:.6f}; "
                                f"need ~${min_qty * signal.price:.0f}, "
                                f"have ${self._config.order_size_usdt:.0f})"
                            )
                            return
                    if self._guard_pipeline is not None:
                        account_info = await self._client.get_account_info()
                        portfolio_value = account_info.available_balance
                        try:
                            self._run_guards(
                                signal.symbol,
                                "BUY",
                                order_size,
                                portfolio_value,
                                signal.confidence,
                                price=signal.price,
                                order_notional=order_size * signal.price,
                                is_exit=False,
                            )
                        except RuntimeError:
                            self._logger.info(
                                "BUY signal for %s blocked by guard pipeline", signal.symbol
                            )
                            return
                    order = await self.place_futures_order(
                        symbol=signal.symbol,
                        side="BUY",
                        quantity=order_size,
                        position_side="LONG",
                        reduce_only=False,
                        reference_price=signal.price,
                    )
                    if order.status == "FILLED":
                        entry_price = (
                            float(order.price) if order.price and order.price > 0 else signal.price
                        )
                        filled_qty = (
                            order.executed_quantity if order.executed_quantity > 0 else order_size
                        )
                        atr_14 = float(signal.indicators.get("atr_14") or 0.0)
                        sl_price, tp_price = await self._place_sl_tp_orders(
                            signal.symbol, entry_price, filled_qty, atr_14
                        )
                        await self._notifier.send_trade_alert(
                            symbol=signal.symbol,
                            side="BUY",
                            quantity=filled_qty,
                            price=entry_price,
                            pnl=None,
                            market="futures",
                            stop_loss=sl_price if sl_price > 0 else None,
                            take_profit=tp_price if tp_price > 0 else None,
                        )

                        # Start the cooldown window only after the fill.
                        if self._guard_pipeline is not None:
                            self._guard_pipeline.record_fill(signal.symbol)

            elif signal.type == SignalType.SELL:
                # FAIL-SAFE: Close only agent-owned position amount, not full account position
                # This prevents one agent from closing other agents' positions in shared accounts

                # Step 1: Get agent-owned position from PortfolioManager
                agent_position_qty = 0.0
                agent_position_side = None
                if self._portfolio_manager is not None:
                    agent_pos = self._portfolio_manager.get_position(
                        signal.symbol, market="futures"
                    )
                    if agent_pos is not None:
                        agent_position_qty = agent_pos.quantity
                        agent_position_side = agent_pos.position_side or "LONG"

                # Step 2: Validate we have a recorded position to close
                if agent_position_qty <= 0:
                    self._logger.warning(
                        "SELL refused: No agent-owned position recorded for %s. "
                        "Exchange may have untracked positions from other agents.",
                        signal.symbol,
                    )
                    # Alert at most once per 4 hours per symbol to avoid spam when
                    # the strategy fires exit signals while the agent is flat.
                    _now = time.time()
                    _last = self._sell_reject_alert_times.get(signal.symbol, 0.0)
                    if _now - _last >= 14400:
                        self._sell_reject_alert_times[signal.symbol] = _now
                        await self._notifier.send_alert(
                            f"<b>Signal rejected</b> [futures]\n"
                            f"{signal.symbol} SELL — No agent-owned position to close"
                        )
                    return

                # Step 3: Get exchange position and validate alignment
                exchange_position_qty = 0.0
                exchange_position_side = None
                exchange_entry_price = 0.0
                if current_position is not None:
                    exchange_position_qty = abs(current_position.position_amt)
                    # Normalize position side: handle BOTH (one-way mode) by inferring from position_amt
                    raw_side = current_position.position_side
                    if raw_side not in {"LONG", "SHORT"}:
                        if current_position.position_amt > 0:
                            raw_side = "LONG"
                        elif current_position.position_amt < 0:
                            raw_side = "SHORT"
                    exchange_position_side = raw_side
                    exchange_entry_price = current_position.entry_price

                # Step 4: Safety checks before closing
                if exchange_position_side != agent_position_side:
                    self._logger.error(
                        "SELL refused: Position side mismatch for %s. Agent: %s, Exchange: %s",
                        signal.symbol,
                        agent_position_side,
                        exchange_position_side,
                    )
                    await self._notifier.send_alert(
                        f"<b>Signal rejected</b> [futures]\n"
                        f"{signal.symbol} SELL — Position side mismatch "
                        f"(agent: {agent_position_side}, exchange: {exchange_position_side})"
                    )
                    return

                if exchange_position_qty < agent_position_qty:
                    self._logger.error(
                        "SELL refused: Exchange position smaller than agent-owned for %s. "
                        "Agent owns: %.4f, Exchange has: %.4f. "
                        "Possible position reduction by another agent.",
                        signal.symbol,
                        agent_position_qty,
                        exchange_position_qty,
                    )
                    await self._notifier.send_alert(
                        f"<b>Signal rejected</b> [futures]\n"
                        f"{signal.symbol} SELL — Exchange position (%.4f) smaller than agent-owned (%.4f)"
                        % (exchange_position_qty, agent_position_qty)
                    )
                    return

                # Step 5: Check for untracked exchange positions (other agents)
                if exchange_position_qty > agent_position_qty:
                    untracked_qty = exchange_position_qty - agent_position_qty
                    self._logger.error(
                        "SELL refused: Untracked exchange position detected for %s. "
                        "Exchange total: %.4f, Agent-owned: %.4f, Untracked: %.4f. "
                        "Shared account without proper isolation.",
                        signal.symbol,
                        exchange_position_qty,
                        agent_position_qty,
                        untracked_qty,
                    )
                    await self._notifier.send_alert(
                        f"<b>Signal rejected</b> [futures]\n"
                        f"{signal.symbol} SELL — Shared account detected "
                        f"({untracked_qty:.4f} untracked qty). "
                        f"Use separate accounts for multi-agent futures."
                    )
                    return

                # Step 6: Safe to close — cancel SL/TP first, then close with agent-owned quantity
                self._logger.info(
                    "SELL approved: Closing agent-owned position for %s (qty: %.4f)",
                    signal.symbol,
                    agent_position_qty,
                )

                if self._guard_pipeline is not None:
                    account_info = await self._client.get_account_info()
                    portfolio_value = account_info.available_balance
                    try:
                        self._run_guards(
                            signal.symbol,
                            "SELL",
                            agent_position_qty,
                            portfolio_value,
                            signal.confidence,
                            price=signal.price,
                            order_notional=agent_position_qty * signal.price,
                            # A futures SELL here closes an open long.
                            is_exit=True,
                        )
                    except RuntimeError:
                        self._logger.info(
                            "SELL signal for %s blocked by guard pipeline", signal.symbol
                        )
                        return

                # Snapshot software protection before cancelling: a partial fill
                # leaves exposure behind and it must not be left unprotected.
                previous_sl_tp = dict(self._sl_tp_prices.get(signal.symbol) or {})
                await self._cancel_sl_tp_orders(signal.symbol)
                order = await self.place_futures_order(
                    symbol=signal.symbol,
                    side="SELL",
                    quantity=agent_position_qty,  # Use agent-owned qty, not full exchange position
                    position_side="LONG",
                    reduce_only=True,
                )

                if order.status == "FILLED":
                    # The order only counts what the exchange says it filled.
                    filled_quantity = self._executed_quantity(order)
                    if filled_quantity is None:
                        self._mark_accounting_gap(
                            signal.symbol, "signal exit reported no executed quantity"
                        )
                        await self._notifier.send_alert(
                            f"<b>Exit fill unconfirmed</b> [futures]\n"
                            f"{signal.symbol} SELL filled without an executed quantity; "
                            f"no PnL booked, position tracking left to reconciliation"
                        )
                        return
                    remaining_state = self._positions.get(signal.symbol)
                    remaining_qty = (
                        float(remaining_state.get("amount", 0.0) or 0.0) if remaining_state else 0.0
                    )
                    if remaining_qty > 0 and previous_sl_tp:
                        # Exchange conditionals were cancelled for the exit; the
                        # software monitor is the always-on fallback and still
                        # covers the remainder at the same stop/target levels.
                        self._sl_tp_prices[signal.symbol] = previous_sl_tp
                        self._logger.warning(
                            "Partial exit for %s left %.4f open — software SL/TP restored",
                            signal.symbol,
                            remaining_qty,
                        )
                    stashed_pnl = self._recent_close_pnl.pop(signal.symbol, None)
                    filled_price: float | None = float(order.price) if order.price else None
                    if stashed_pnl is None and filled_price is None:
                        filled_price, _ = await self._resolve_close_fill(
                            signal.symbol, order, "SELL"
                        )
                    if stashed_pnl is not None:
                        pnl: float | None = stashed_pnl
                    elif filled_price is not None and exchange_entry_price > 0:
                        pnl = (filled_price - exchange_entry_price) * filled_quantity
                    else:
                        # No price anywhere: report the exit without a PnL
                        # rather than deriving one from the signal price.
                        pnl = None
                        if filled_price is None:
                            self._mark_accounting_gap(
                                signal.symbol, "signal exit fill price unknown: alert PnL omitted"
                            )
                    alert_price = (
                        filled_price
                        if filled_price is not None
                        else (signal.price if signal.price > 0 else exchange_entry_price)
                    )
                    bars_held = (
                        self._estimate_bars_held(agent_pos.entry_time) if agent_pos else None
                    )

                    pnl_text = "unknown" if pnl is None else f"{pnl:+.2f}"
                    if remaining_qty > 0:
                        self._logger.info(
                            "Futures position partially closed: %s SELL (qty: %.4f, "
                            "remaining: %.4f, pnl: %s)",
                            signal.symbol,
                            filled_quantity,
                            remaining_qty,
                            pnl_text,
                        )
                    else:
                        self._logger.info(
                            "Futures position closed: %s SELL (qty: %.4f, pnl: %s)",
                            signal.symbol,
                            filled_quantity,
                            pnl_text,
                        )

                    if pnl is None:
                        # The exit is real; only its PnL is unknown. Say that
                        # instead of rendering a close alert with no PnL (which
                        # the notifier would format as an *entry*).
                        await self._notifier.send_alert(
                            f"<b>Exit filled, PnL unknown</b> [futures]\n"
                            f"{signal.symbol} SELL {filled_quantity:.6f} @ {alert_price:.4f} — "
                            f"no exchange price/PnL available; booked as an accounting gap"
                        )
                    else:
                        await self._notifier.send_trade_alert(
                            symbol=signal.symbol,
                            side="SELL",
                            quantity=filled_quantity,
                            price=alert_price,
                            pnl=pnl,
                            market="futures",
                            entry_price=exchange_entry_price if exchange_entry_price > 0 else None,
                            bars_held=bars_held,
                            ticket_id=order.order_id,
                        )

                    # Start the cooldown window only after the fill.
                    if self._guard_pipeline is not None:
                        self._guard_pipeline.record_fill(signal.symbol)
                else:
                    self._logger.warning(
                        "Futures SELL order not filled for %s (status: %s)",
                        signal.symbol,
                        order.status,
                    )
            else:
                self._logger.info(
                    "SELL signal but no LONG position for %s. Ignoring.",
                    signal.symbol,
                )

        except PostFillAccountingError as exc:
            # The order executed; only local bookkeeping failed. Reporting this
            # as "rejected" invites a retry, and a retry would double the exit.
            self._logger.error(
                "Futures signal %s filled but accounting is pending: %s", signal, exc
            )
            await self._notifier.send_alert(
                f"<b>Order filled, accounting pending</b> [futures]\n"
                f"{signal.symbol} {signal.type.value} — {exc}\n"
                f"Do not retry; reconciliation will settle the books."
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Futures signal rejected: %s — %s", signal, exc)
            await self._notifier.send_alert(
                f"<b>Signal rejected</b> [futures]\n{signal.symbol} {signal.type.value} — {exc}"
            )

    def _calculate_quantity(self, symbol: str, price: float) -> float:
        """Calculate order quantity ensuring notional >= MIN_NOTIONAL after LOT_SIZE truncation.

        Binance applies math.floor truncation to LOT_SIZE step before checking the $20
        minimum notional, so raw_qty = order_size_usdt / price can slip under $20 after
        truncation. We add one step if needed.
        """
        _MIN_NOTIONAL = 20.0
        if price <= 0:
            self._logger.warning("Invalid price %.4f for %s", price, symbol)
            return 0.0
        return calculate_futures_order_quantity(
            order_size_usdt=self._config.order_size_usdt,
            price=price,
            quantity_step_size=self._client.get_step_size(symbol),
            min_notional_usdt=_MIN_NOTIONAL,
        )

    async def _place_exchange_conditional(
        self,
        symbol: str,
        order_type: str,
        trigger_price: float,
        quantity: float,
        position_side: str,
    ) -> str:
        """Place a STOP_MARKET or TAKE_PROFIT_MARKET order via Algo Order API.

        Uses POST /fapi/v1/algoOrder (algoType=CONDitional) which replaces
        the deprecated conditional orders on /fapi/v1/order (error -4120).

        Returns the algo order ID, or raises on failure.
        """
        order = await self._client.place_algo_order(
            symbol=symbol,
            side="SELL",
            order_type=order_type,
            trigger_price=trigger_price,
            position_side=position_side,
            close_position=True,
        )
        return order.algo_id

    async def _place_sl_tp_orders(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        atr_14: float,
        opened_at: float | None = None,
    ) -> tuple[float, float]:
        """Place exchange-side SL and TP orders, with software-monitor fallback.

        Tries STOP_MARKET/TAKE_PROFIT_MARKET first; if the account rejects them
        (-4120), retries with limit-based STOP/TAKE_PROFIT. Software monitoring
        (every 30 s) acts as the final safety net if exchange orders fail entirely.
        """
        if atr_14 > 0:
            sl_price = entry_price - self._config.sl_atr_multiplier * atr_14
            tp_price = entry_price + self._config.tp_atr_multiplier * atr_14
        else:
            sl_price = (
                entry_price * (1 - self._config.stop_loss_pct)
                if self._config.stop_loss_pct > 0
                else 0.0
            )
            tp_price = (
                entry_price * (1 + self._config.take_profit_pct)
                if self._config.take_profit_pct > 0
                else 0.0
            )

        sl_order_id = ""
        tp_order_id = ""

        # Software monitor always active as final fallback.
        self._sl_tp_prices[symbol] = {
            "sl_price": sl_price,
            "tp_price": tp_price,
            "entry_price": entry_price,
            "atr_14": atr_14,
            "high_water_mark": entry_price,
            "opened_at": opened_at if opened_at is not None else time.time(),
        }

        order_position_side = "BOTH" if self._active_position_mode == "one-way" else "LONG"

        if self._exchange_conditional_supported and sl_price > 0:
            try:
                sl_order_id = await self._place_exchange_conditional(
                    symbol, "STOP_MARKET", sl_price, quantity, order_position_side
                )
                self._logger.info(
                    "SL order placed for %s: %.4f (ATR: %.4f)", symbol, sl_price, atr_14
                )
            except Exception as exc:
                self._logger.warning(
                    "Exchange SL order failed for %s (software fallback active): %s", symbol, exc
                )

        if self._exchange_conditional_supported and tp_price > 0:
            try:
                tp_order_id = await self._place_exchange_conditional(
                    symbol, "TAKE_PROFIT_MARKET", tp_price, quantity, order_position_side
                )
                self._logger.info(
                    "TP order placed for %s: %.4f (ATR: %.4f)", symbol, tp_price, atr_14
                )
            except Exception as exc:
                self._logger.warning(
                    "Exchange TP order failed for %s (software fallback active): %s", symbol, exc
                )

        self._sl_tp_orders[symbol] = {
            "sl_order_id": sl_order_id,
            "tp_order_id": tp_order_id,
            "sl_is_algo": bool(sl_order_id),
            "tp_is_algo": bool(tp_order_id),
        }
        self._logger.info(
            "SL/TP tracking active for %s: SL=%.4f TP=%.4f (exchange_sl=%s exchange_tp=%s)",
            symbol,
            sl_price,
            tp_price,
            "yes" if sl_order_id else "software-only",
            "yes" if tp_order_id else "software-only",
        )
        return sl_price, tp_price

    async def _cancel_sl_tp_orders(self, symbol: str) -> None:
        """Cancel tracked SL/TP orders and clear software SL/TP prices before a manual close."""
        self._sl_tp_prices.pop(symbol, None)
        tracked = self._sl_tp_orders.pop(symbol, None)
        if not tracked:
            return
        for label, order_id, is_algo in [
            ("SL", tracked.get("sl_order_id"), tracked.get("sl_is_algo", False)),
            ("TP", tracked.get("tp_order_id"), tracked.get("tp_is_algo", False)),
        ]:
            if not order_id:
                continue
            try:
                if is_algo:
                    await self._client.cancel_algo_order(order_id, symbol)
                else:
                    await self._client.cancel_order(symbol, order_id)
                self._logger.info("Cancelled %s algo order %s for %s", label, order_id, symbol)
            except Exception as exc:
                # Order may already be filled or cancelled by the exchange — fine
                self._logger.debug(
                    "Cancel %s order %s for %s (may already be closed): %s",
                    label,
                    order_id,
                    symbol,
                    exc,
                )

    async def _fetch_latest_atr(self, symbol: str, timeframe: str) -> float:
        """Return the most recent ATR_14 for symbol+timeframe from the DB, or 0.0 on failure."""
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT atr_14 FROM indicators WHERE symbol=$1 AND timeframe=$2"
                    " ORDER BY time DESC LIMIT 1",
                    symbol,
                    timeframe,
                )
                return float(row["atr_14"]) if row and row["atr_14"] is not None else 0.0
        except Exception as exc:
            self._logger.warning("Could not read ATR for %s: %s", symbol, exc)
            return 0.0

    async def _recover_open_positions(self) -> None:
        """On startup, find open positions with no tracked SL/TP and place protective orders."""
        for symbol in self._config.symbols:
            try:
                positions = await self._client.get_position_risk(symbol)
                for pos in positions:
                    if pos.position_amt <= 0:
                        continue
                    if symbol in self._sl_tp_orders:
                        continue
                    atr_14 = await self._fetch_latest_atr(symbol, self._config.timeframe)
                    self._logger.warning(
                        "Recovering unprotected position %s @ %.4f (qty: %.4f, ATR=%.4f) — placing SL/TP",
                        symbol,
                        pos.entry_price,
                        abs(pos.position_amt),
                        atr_14,
                    )
                    opened_at: float | None = None
                    if self._portfolio_manager is not None:
                        tracked_position = self._portfolio_manager.get_position(
                            symbol, market="futures"
                        )
                        if tracked_position is not None and tracked_position.entry_time is not None:
                            opened_at = tracked_position.entry_time.timestamp()
                    await self._place_sl_tp_orders(
                        symbol,
                        pos.entry_price,
                        abs(pos.position_amt),
                        atr_14,
                        opened_at=opened_at,
                    )
                    self._risk_manager.register_open_position(
                        symbol,
                        abs(pos.position_amt) * pos.entry_price,
                        pos.entry_price,
                        market="futures",
                    )
            except Exception as exc:
                self._logger.error("Failed to recover SL/TP for open %s position: %s", symbol, exc)

    def get_live_status(self) -> dict[str, object]:
        """Return cached live futures data for the overseer status command.

        Uses in-memory state refreshed every 30 s by the monitor loop — no
        extra API call on each /status request.
        """
        positions = []
        for symbol, pos in self._positions.items():
            amount = float(pos.get("amount", 0))
            if amount <= 0:
                continue
            sl_tp = self._sl_tp_prices.get(symbol, {})
            positions.append(
                {
                    "symbol": symbol,
                    "qty": amount,
                    "entry_price": float(pos.get("entry_price", 0)),
                    "mark_price": float(pos.get("mark_price", 0)),
                    "unrealized_pnl": float(pos.get("unrealized_pnl", 0)),
                    "leverage": int(pos.get("leverage", self._config.default_leverage)),
                    "sl_price": float(sl_tp.get("sl_price", 0.0)),
                    "tp_price": float(sl_tp.get("tp_price", 0.0)),
                }
            )
        return {
            "account_balance": self._account_balance,
            "positions": positions,
        }

    def stop(self) -> None:
        """Stop the futures trading loop."""
        self._running = False
