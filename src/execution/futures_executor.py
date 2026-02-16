"""Futures trading execution service."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from src.execution.futures_client import (
    BinanceFuturesClient,
    FuturesOrderInfo,
)
from src.execution.metrics import ExecutionMetrics
from src.notifications.telegram import TelegramNotifier
from src.portfolio.manager import PortfolioManager
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


class FuturesTradingExecutor:
    """Futures trading execution service with leverage and risk management."""

    def __init__(
        self,
        config: FuturesTradingConfig,
        risk_manager: RiskManager,
        metrics: ExecutionMetrics,
        portfolio_manager: PortfolioManager | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._config = config
        self._risk_manager = risk_manager
        self._metrics = metrics
        self._portfolio_manager = portfolio_manager
        self._notifier = notifier or TelegramNotifier()
        self._logger = get_logger(self.__class__.__name__)
        self._client: BinanceFuturesClient | None = None
        self._running = False
        self._positions: dict[str, dict[str, Any]] = {}  # Track futures positions
        self._active_position_mode = "hedge"

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

        # Set default leverage for all configured symbols
        for symbol in self._config.symbols:
            try:
                await self._client.set_leverage(symbol, self._config.default_leverage)
                self._logger.info(
                    "Set leverage %dx for %s", self._config.default_leverage, symbol
                )
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
            except Exception as exc:
                self._logger.error(
                    "Failed to get position risk for %s: %s", symbol, exc
                )

    async def place_futures_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float,
        position_side: str = "LONG",
        reduce_only: bool = False,
    ) -> FuturesOrderInfo:
        """Place a futures order with risk checks.

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Order side ("BUY" or "SELL")
            quantity: Order quantity (in base asset units)
            position_side: "LONG" or "SHORT"
            reduce_only: If True, order will only reduce position

        Returns:
            FuturesOrderInfo: Information about placed order

        Raises:
            RuntimeError: If risk checks fail or order placement fails
        """
        if not self._config.enabled:
            raise RuntimeError("Futures trading executor is disabled")

        # Check if trading is allowed
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

        # Check margin usage
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

        request_position_side = (
            "BOTH" if self._active_position_mode == "one-way" else position_side
        )

        # Place order
        start_time = time.perf_counter()
        try:
            order = await self._client.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type="MARKET",
                reduce_only=reduce_only,
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
                self._metrics.record_order_filled(symbol, side)

                # Update position tracking
                if symbol not in self._positions:
                    self._positions[symbol] = {}

                if reduce_only:
                    # Position was reduced/closed
                    if self._portfolio_manager is not None:
                        await self._portfolio_manager.close_position(
                            symbol=symbol,
                            price=float(order.price) if order.price else 0.0,
                            order_id=str(order.order_id),
                            market="futures",
                        )
                    self._logger.info(
                        "Futures position closed: %s %s (qty: %.4f)",
                        side,
                        symbol,
                        quantity,
                    )
                else:
                    # New position or added to existing
                    if self._portfolio_manager is not None:
                        await self._portfolio_manager.open_position(
                            symbol=symbol,
                            quantity=quantity,
                            price=float(order.price) if order.price else 0.0,
                            order_id=str(order.order_id),
                            market="futures",
                        )
                    self._logger.info(
                        "Futures position opened: %s %s %s (qty: %.4f, leverage: %dx)",
                        side,
                        symbol,
                        request_position_side,
                        quantity,
                        self._config.default_leverage,
                    )

            return order

        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            self._logger.error("Failed to place futures order: %s", exc)
            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="FUTURES_MARKET",
                status="FAILED",
                latency_seconds=elapsed,
            )
            self._metrics.record_api_error(
                "place_futures_order", str(type(exc).__name__)
            )
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

            has_long_position = False
            if current_position is not None:
                if self._active_position_mode == "one-way":
                    has_long_position = current_position.position_amt > 0
                else:
                    has_long_position = current_position.position_side == "LONG"

            if signal.type == SignalType.BUY:
                # Open or add to LONG position
                if current_position is None:
                    # No position - open new LONG
                    order = await self.place_futures_order(
                        symbol=signal.symbol,
                        side="BUY",
                        quantity=self._calculate_quantity(signal.symbol),
                        position_side="LONG",
                        reduce_only=False,
                    )
                    if order.status == "FILLED":
                        filled_quantity = (
                            order.executed_quantity
                            if order.executed_quantity > 0
                            else order.quantity
                        )
                        filled_price = (
                            float(order.price) if order.price else signal.price
                        )
                        await self._notifier.send_trade_alert(
                            symbol=signal.symbol,
                            side="BUY",
                            quantity=filled_quantity,
                            price=filled_price,
                            pnl=None,
                            market="futures",
                        )
                    else:
                        self._logger.info(
                            "Futures BUY order not filled yet for %s (status: %s)",
                            signal.symbol,
                            order.status,
                        )
                elif has_long_position:
                    self._logger.info(
                        "BUY signal ignored: Futures LONG position already exists for %s",
                        signal.symbol,
                    )
                else:
                    # Have SHORT position - this shouldn't happen in LONG-only MVP
                    self._logger.warning(
                        "BUY signal but have SHORT position for %s. Closing SHORT first.",
                        signal.symbol,
                    )
                    # Close SHORT with reduceOnly
                    await self.place_futures_order(
                        symbol=signal.symbol,
                        side="BUY",  # BUY to close SHORT
                        quantity=abs(current_position.position_amt),
                        position_side="SHORT",
                        reduce_only=True,
                    )
                    # Then open LONG
                    await self.place_futures_order(
                        symbol=signal.symbol,
                        side="BUY",
                        quantity=self._calculate_quantity(signal.symbol),
                        position_side="LONG",
                        reduce_only=False,
                    )

            elif signal.type == SignalType.SELL:
                # Close LONG position (reduceOnly)
                if current_position and has_long_position:
                    order = await self.place_futures_order(
                        symbol=signal.symbol,
                        side="SELL",
                        quantity=abs(current_position.position_amt),
                        position_side="LONG",
                        reduce_only=True,  # Important: only reduce, don't flip
                    )
                    if order.status == "FILLED":
                        filled_quantity = (
                            order.executed_quantity
                            if order.executed_quantity > 0
                            else abs(current_position.position_amt)
                        )
                        filled_price = (
                            float(order.price) if order.price else signal.price
                        )
                        pnl = (
                            filled_price - current_position.entry_price
                        ) * filled_quantity
                        await self._notifier.send_trade_alert(
                            symbol=signal.symbol,
                            side="SELL",
                            quantity=filled_quantity,
                            price=filled_price,
                            pnl=pnl,
                            market="futures",
                        )
                    else:
                        self._logger.info(
                            "Futures SELL order not filled yet for %s (status: %s)",
                            signal.symbol,
                            order.status,
                        )
                else:
                    self._logger.info(
                        "SELL signal but no LONG position for %s. Ignoring.",
                        signal.symbol,
                    )
                    await self._notifier.send_alert(
                        f"<b>Signal skipped</b> [futures]\n"
                        f"{signal.symbol} SELL — no LONG position"
                    )

        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Futures signal rejected: %s — %s", signal, exc)
            await self._notifier.send_alert(
                f"<b>Signal rejected</b> [futures]\n"
                f"{signal.symbol} {signal.type.value} — {exc}"
            )

    def _calculate_quantity(self, symbol: str) -> float:
        """Calculate order quantity based on config.

        For futures, this returns the base asset quantity based on order_size_usdt
        and current price. For now, returns a fixed quantity.

        Args:
            symbol: Trading pair symbol

        Returns:
            float: Order quantity in base asset units
        """
        # TODO: Get current price and calculate quantity
        # For now, return a reasonable default based on symbol
        defaults = {
            "BTCUSDT": 0.01,  # ~$500-1000 at current prices
            "ETHUSDT": 0.1,  # ~$300-500 at current prices
        }
        return defaults.get(symbol, 0.01)

    def stop(self) -> None:
        """Stop the futures trading loop."""
        self._running = False
