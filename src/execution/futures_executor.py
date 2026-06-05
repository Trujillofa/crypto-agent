"""Futures trading execution service."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.core.event_log import EventLog
from src.db.pool import get_pool
from src.execution.futures_client import (
    BinanceFuturesClient,
    FuturesOrderInfo,
    FuturesUserTrade,
)
from src.execution.metrics import ExecutionMetrics
from src.execution.staged_orders import StagedOrderManager
from src.notifications.telegram import TelegramNotifier
from src.portfolio.manager import PortfolioManager
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
    ) -> None:
        if self._guard_pipeline is None:
            return
        context = GuardContext(
            symbol=symbol,
            side=side,
            quantity=quantity,
            portfolio_value=portfolio_value,
            signal_confidence=signal_confidence,
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
        last_mark: float,
        sl_price: float,
        tp_price: float,
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
        if await _is_filled(sl_order_id):
            close_reason = "stop_loss"
            close_ticket = sl_order_id
        elif await _is_filled(tp_order_id):
            close_reason = "take_profit"
            close_ticket = tp_order_id
        elif sl_price > 0 and last_mark > 0:
            dist_sl = abs(last_mark - sl_price)
            dist_tp = abs(last_mark - tp_price) if tp_price > 0 else float("inf")
            if dist_sl <= dist_tp:
                close_reason = "stop_loss"
                close_ticket = sl_order_id
            else:
                close_reason = "take_profit"
                close_ticket = tp_order_id
        else:
            close_ticket = sl_order_id or tp_order_id

        fill_price, realized_pnl = await self._fetch_close_execution_details(symbol, close_ticket)
        if fill_price is None or realized_pnl is None:
            self._logger.warning(
                "Using estimated %s close for %s because Binance fill/PnL lookup was unavailable",
                close_reason or "exchange",
                symbol,
            )
        return CloseExecutionDetails(
            reason=close_reason,
            ticket_id=close_ticket,
            fill_price=fill_price,
            realized_pnl=realized_pnl,
        )

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
        # Check if trading is allowed
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            self._logger.warning(f"Futures trading blocked: {reason}")
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
                    last_mark = float(pos_state.get("mark_price", 0.0))
                    qty = float(pos_state.get("amount", 0.0))
                    tracked_orders = self._sl_tp_orders.get(symbol, {})
                    sw_prices = self._sl_tp_prices.get(symbol, {})
                    sl_px = sw_prices.get("sl_price", 0.0)
                    tp_px = sw_prices.get("tp_price", 0.0)
                    close_details = await self._resolve_close_execution_details(
                        symbol=symbol,
                        tracked_orders=tracked_orders,
                        last_mark=last_mark,
                        sl_price=sl_px,
                        tp_price=tp_px,
                    )
                    close_price = (
                        close_details.fill_price if close_details.fill_price else last_mark
                    )
                    pnl: float | None = None
                    if close_details.reason == "stop_loss" and self._config.sl_cooldown_minutes > 0:
                        self._sl_cooldown_timestamps[symbol] = time.time()
                        self._logger.info(
                            "SL cooldown started for %s (close=%.4f sl=%.4f)",
                            symbol,
                            close_price,
                            sl_px,
                        )

                    bars_held: int | None = None
                    if self._portfolio_manager is not None:
                        tracked_position = self._portfolio_manager.get_position(
                            symbol, market="futures"
                        )
                        if tracked_position is not None:
                            bars_held = self._estimate_bars_held(tracked_position.entry_time)

                    realized_pnl: float | None = None
                    if self._portfolio_manager is not None and close_price > 0:
                        try:
                            _, realized_pnl = await self._portfolio_manager.close_position(
                                symbol=symbol,
                                price=close_price,
                                order_id=close_details.ticket_id,
                                market="futures",
                                closing_side="SELL",
                                realized_pnl_override=close_details.realized_pnl,
                            )
                        except ValueError:
                            pass  # position not in DB (recovered or pre-tracking)

                    if entry_px > 0 and qty > 0 and close_price > 0:
                        pnl = (
                            realized_pnl
                            if realized_pnl is not None
                            else (close_price - entry_px) * qty
                        )
                        await self._notifier.send_trade_alert(
                            symbol=symbol,
                            side="SELL",
                            quantity=qty,
                            price=close_price,
                            pnl=pnl,
                            market="futures",
                            entry_price=entry_px,
                            close_reason=close_details.reason,
                            bars_held=bars_held,
                            ticket_id=close_details.ticket_id,
                        )

                    self._sl_tp_orders.pop(symbol, None)
                    self._sl_tp_prices.pop(symbol, None)
                    self._positions.pop(symbol, None)
                    self._record_risk_close(symbol, pnl, self._account_balance)

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
                        self._sl_tp_prices.pop(symbol, None)
                        try:
                            qty = abs(positions[0].position_amt)
                            await self._cancel_sl_tp_orders(symbol)
                            request_position_side = (
                                "BOTH" if self._active_position_mode == "one-way" else "LONG"
                            )
                            request_reduce_only = self._active_position_mode == "one-way"
                            close_order = await self._client.place_order(
                                symbol=symbol,
                                side="SELL",
                                quantity=qty,
                                order_type="MARKET",
                                reduce_only=request_reduce_only,
                                position_side=request_position_side,
                            )
                            self._logger.info(
                                "Software %s close order placed for %s", reason_str, symbol
                            )
                            # Resolve actual fill price/PnL from the MARKET close order
                            (
                                close_fill_price,
                                close_realized_pnl,
                            ) = await self._fetch_close_execution_details(
                                symbol, close_order.order_id, "SELL"
                            )
                            if close_fill_price is None:
                                close_fill_price = mark

                            # Record close in portfolio DB with real fill
                            if self._portfolio_manager is not None and close_fill_price > 0:
                                try:
                                    (
                                        _,
                                        close_realized_pnl,
                                    ) = await self._portfolio_manager.close_position(
                                        symbol=symbol,
                                        price=close_fill_price,
                                        market="futures",
                                        closing_side="SELL",
                                        realized_pnl_override=close_realized_pnl,
                                    )
                                except ValueError:
                                    pass
                            self._positions.pop(symbol, None)
                            if (
                                sw_close_reason == "stop_loss"
                                and self._config.sl_cooldown_minutes > 0
                            ):
                                self._sl_cooldown_timestamps[symbol] = time.time()
                            if close_realized_pnl is not None:
                                pnl = close_realized_pnl
                            else:
                                pnl = (
                                    (close_fill_price - sw_entry_px) * qty
                                    if sw_entry_px > 0
                                    else None
                                )
                            await self._notifier.send_trade_alert(
                                symbol=symbol,
                                side="SELL",
                                quantity=qty,
                                price=close_fill_price,
                                pnl=pnl,
                                market="futures",
                                entry_price=sw_entry_px if sw_entry_px > 0 else None,
                                close_reason=sw_close_reason,
                                bars_held=bars_held,
                                ticket_id=close_order.order_id,
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

    def _record_risk_close(
        self,
        symbol: str,
        pnl: float | None,
        portfolio_value: float,
    ) -> None:
        """Release futures risk state and feed realized P&L into circuit breakers."""
        if pnl is not None and portfolio_value > 0:
            self._risk_manager.record_trade(symbol, pnl, portfolio_value)
            self._metrics.record_realized_pnl(symbol, pnl)
        self._risk_manager.register_close_position(symbol)

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

        if reduce_only and side != "SELL":
            raise ValueError(f"reduce_only=True is only valid for SELL orders, got side={side!r}")

        # Close orders only block on reconciliation; kill switch / circuit breakers protect against
        # new risk and must not trap the account in an open losing position.
        if reduce_only:
            is_allowed, reason = self._risk_manager.is_close_allowed()
        else:
            is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            self._metrics.record_risk_block(symbol, reason)
            raise RuntimeError(f"Futures trading blocked: {reason}")

        # Get account info for margin check
        try:
            account_info = await self._client.get_account_info()
        except Exception as exc:
            self._logger.error("Failed to get account info: %s", exc)
            raise RuntimeError("Failed to get account info") from exc

        # Check leverage limit
        leverage_allowed, leverage_reason = self._risk_manager.check_max_leverage(
            self._config.default_leverage
        )
        if not leverage_allowed:
            self._metrics.record_risk_block(symbol, leverage_reason)
            raise RuntimeError(f"Leverage check failed: {leverage_reason}")

        # Check margin usage — skip for reduce-only orders (closing frees margin, never consumes it)
        if not reduce_only:
            if reference_price is None or reference_price <= 0:
                raise ValueError("reference_price must be positive for opening futures orders")

            notional_value = quantity * reference_price
            position_allowed, position_reason = self._risk_manager.check_position_limit(
                symbol,
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

        request_position_side = "BOTH" if self._active_position_mode == "one-way" else position_side
        # In hedge mode, closing is conveyed via positionSide=LONG/SHORT + opposite side;
        # sending reduceOnly alongside positionSide != BOTH triggers Binance error -1106.
        request_reduce_only = reduce_only and self._active_position_mode == "one-way"
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

        # Place order
        start_time = time.perf_counter()
        try:
            order = await self._client.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type="MARKET",
                reduce_only=request_reduce_only,
                position_side=request_position_side,
            )
            if order.status != "FILLED":
                initial_status = order.status
                for _ in range(4):
                    await asyncio.sleep(0.25)
                    order = await self._client.get_order_status(symbol, order.order_id)
                    if order.status == "FILLED":
                        self._logger.info(
                            "Futures order %s transitioned %s -> FILLED for %s",
                            order.order_id,
                            initial_status,
                            symbol,
                        )
                        break
            elapsed = time.perf_counter() - start_time

            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="FUTURES_MARKET",
                status=order.status,
                latency_seconds=elapsed,
            )

            if order.status == "FILLED":
                if staged_order_id is not None:
                    await self._staged_manager.mark_completed(staged_order_id, order)
                self._metrics.record_order_filled(symbol, side)

                if reduce_only:
                    # Position was reduced/closed
                    close_pnl: float | None = None
                    if self._portfolio_manager is not None:
                        _, close_pnl = await self._portfolio_manager.close_position(
                            symbol=symbol,
                            price=float(order.price) if order.price else 0.0,
                            order_id=str(order.order_id),
                            market="futures",
                            closing_side=side,
                        )
                        self._recent_close_pnl[symbol] = close_pnl
                    self._record_risk_close(
                        symbol,
                        close_pnl,
                        account_info.total_margin_balance,
                    )
                    # Clear in-memory tracker so max_concurrent_longs frees the slot.
                    # The monitor-loop cleanup at line ~313 only fires when _sl_tp_orders
                    # still has the symbol, but signal-driven closes pop it beforehand.
                    self._positions.pop(symbol, None)
                    self._logger.info(
                        "Futures position closed: %s %s (qty: %.4f)",
                        side,
                        symbol,
                        quantity,
                    )
                else:
                    # Update position tracking
                    if symbol not in self._positions:
                        self._positions[symbol] = {}
                    # New position or added to existing
                    recorded_qty = (
                        order.executed_quantity if order.executed_quantity > 0 else quantity
                    )
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
                        request_position_side,
                        recorded_qty,
                        self._config.default_leverage,
                    )
                    # Immediately reflect filled qty so max_concurrent_longs guard
                    # works within the same evaluation cycle (before next monitoring tick).
                    self._positions[symbol]["amount"] = recorded_qty
                    self._positions[symbol]["side"] = "LONG"
                    executed_price = float(order.price) if order.price else reference_price
                    self._risk_manager.register_open_position(
                        symbol,
                        recorded_qty * executed_price,
                        executed_price,
                    )

            return order

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
                        )
                    except RuntimeError:
                        self._logger.info(
                            "SELL signal for %s blocked by guard pipeline", signal.symbol
                        )
                        return

                await self._cancel_sl_tp_orders(signal.symbol)
                order = await self.place_futures_order(
                    symbol=signal.symbol,
                    side="SELL",
                    quantity=agent_position_qty,  # Use agent-owned qty, not full exchange position
                    position_side="LONG",
                    reduce_only=True,
                )

                if order.status == "FILLED":
                    filled_quantity = (
                        order.executed_quantity
                        if order.executed_quantity > 0
                        else agent_position_qty
                    )
                    filled_price = float(order.price) if order.price else signal.price
                    stashed_pnl = self._recent_close_pnl.pop(signal.symbol, None)
                    pnl = (
                        stashed_pnl
                        if stashed_pnl is not None
                        else (filled_price - exchange_entry_price) * filled_quantity
                    )
                    bars_held = (
                        self._estimate_bars_held(agent_pos.entry_time) if agent_pos else None
                    )

                    self._logger.info(
                        "Futures position closed: %s SELL (qty: %.4f, pnl: %.2f)",
                        signal.symbol,
                        filled_quantity,
                        pnl,
                    )

                    await self._notifier.send_trade_alert(
                        symbol=signal.symbol,
                        side="SELL",
                        quantity=filled_quantity,
                        price=filled_price,
                        pnl=pnl,
                        market="futures",
                        entry_price=exchange_entry_price if exchange_entry_price > 0 else None,
                        bars_held=bars_held,
                        ticket_id=order.order_id,
                    )
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
        raw_qty = self._config.order_size_usdt / price
        step = self._client.get_step_size(symbol)
        if step > 0:
            truncated = math.floor(raw_qty / step) * step
            if truncated * price < _MIN_NOTIONAL:
                return truncated + step
            return truncated
        return raw_qty

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
