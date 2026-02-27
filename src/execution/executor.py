from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from src.execution.binance_client import (
    BinancePrivateClient,
    OrderInfo,
)
from src.execution.metrics import ExecutionMetrics
from src.notifications.telegram import TelegramNotifier
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass(frozen=True)
class TradingConfig:
    """Trading execution configuration."""

    api_key: str
    api_secret: str
    test_mode: bool = True
    enabled: bool = False
    symbols: list[str] = field(default_factory=list)
    order_size_usdt: float = 100.0  # Default order size in USDT
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    use_atr_sizing: bool = False
    atr_multiplier: float = 1.0
    risk_per_trade_pct: float = 0.02


class TradingExecutor:
    """Main trading execution service with risk management integration."""

    def __init__(
        self,
        config: TradingConfig,
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
        self._client: BinancePrivateClient | None = None
        self._running = False

    async def __aenter__(self) -> TradingExecutor:
        if not self._config.enabled:
            self._logger.info(
                "TradingExecutor disabled (trading_execution.enabled=false)"
            )
            return self

        if not self._config.api_key or not self._config.api_secret:
            self._logger.error("Trading enabled but API keys are missing")
            raise RuntimeError("API keys missing for enabled trading execution")

        self._client = BinancePrivateClient(
            api_key=self._config.api_key,
            api_secret=self._config.api_secret,
            test_mode=self._config.test_mode,
        )
        await self._client.__aenter__()
        await self._client.load_exchange_info(self._config.symbols)
        await self._notifier.__aenter__()
        self._metrics.start_trading()
        self._logger.info("TradingExecutor initialized")
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)
        await self._notifier.__aexit__(exc_type, exc, tb)
        self._metrics.stop_trading()
        self._logger.info("TradingExecutor stopped")

    async def run(self) -> None:
        """Main trading loop."""
        if not self._config.enabled:
            self._logger.info(
                "Trading executor loop skipped (no strategy engine configured)"
            )
            return

        self._running = True
        self._logger.info("Starting trading execution loop...")

        try:
            while self._running:
                await self._monitor_and_update()
                await asyncio.sleep(30)  # Check every 30 seconds
        except asyncio.CancelledError:
            self._logger.info("Trading execution loop cancelled")

    async def _monitor_and_update(self) -> None:
        """Monitor account and update metrics."""
        # Check if trading is allowed
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            self._logger.warning(f"Trading blocked: {reason}")
            return

        # Get account info
        try:
            account_info = await self._client.get_account_info()
            self._metrics.update_account_balance(
                total_wallet=account_info.total_balance,
                available=account_info.available_balance,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to get account info: %s", exc)
            self._metrics.record_api_error("get_account_info", str(type(exc).__name__))

        # Get open orders
        open_orders_data = []
        for symbol in self._config.symbols:
            try:
                orders = await self._client.get_open_orders(symbol=symbol)
                open_orders_data.append((symbol, len(orders)))
            except Exception as exc:  # noqa: BLE001
                self._logger.error("Failed to get open orders for %s: %s", symbol, exc)
                self._metrics.record_api_error(
                    "get_open_orders", str(type(exc).__name__)
                )

        if open_orders_data:
            self._metrics.update_open_orders(open_orders_data)

    async def _wait_for_fill(
        self, symbol: str, order_id: str, timeout: float = 5.0
    ) -> OrderInfo:
        """Poll order status until filled or timeout.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to poll
            timeout: Max seconds to wait

        Returns:
            Updated OrderInfo
        """
        start_time = time.time()
        while (time.time() - start_time) < timeout:
            try:
                if self._client is None:
                    break
                order = await self._client.get_order_status(symbol, order_id)
                if order.status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                    return order
                await asyncio.sleep(0.5)
            except Exception as exc:
                self._logger.warning(f"Error polling order {order_id}: {exc}")
                await asyncio.sleep(1.0)

        # Return last known status
        if self._client:
            return await self._client.get_order_status(symbol, order_id)
        raise RuntimeError("Client disconnected during polling")

    async def place_market_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        quantity: float | None = None,
    ) -> OrderInfo:
        """Place a market order with risk checks.

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Order side ("BUY" or "SELL")
            quantity: Order quantity. If None, calculates from config.

        Returns:
            OrderInfo: Information about placed order

        Raises:
            RuntimeError: If risk checks fail or order placement fails
        """
        if not self._config.enabled:
            raise RuntimeError("Trading executor is disabled")

        # Check if trading is allowed
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            self._metrics.record_risk_block(symbol, reason)
            raise RuntimeError(f"Trading blocked: {reason}")

        # Get account info for portfolio value
        try:
            account_info = await self._client.get_account_info()
            portfolio_value = account_info.available_balance
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to get account info: %s", exc)
            self._metrics.record_api_error("get_account_info", str(type(exc).__name__))
            raise RuntimeError("Failed to get account info") from exc

        # Calculate quantity if not provided
        if quantity is None:
            quantity = self._calculate_quantity(symbol, portfolio_value)

        # Check position limits (skip for SELL — closing a position shouldn't be blocked)
        if side == "BUY":
            allowed, risk_reason = self._risk_manager.check_position_limit(
                symbol, quantity, portfolio_value
            )
            if not allowed:
                self._metrics.record_risk_block(symbol, risk_reason)
                raise RuntimeError(f"Position limit check failed: {risk_reason}")

        # Place order
        start_time = time.perf_counter()
        try:
            order = await self._client.place_market_order(
                symbol=symbol, side=side, quantity=quantity
            )
            elapsed = time.perf_counter() - start_time

            # E-2: Order Reconciliation - Poll for fill if not immediately filled
            if order.status not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                self._logger.info(
                    "Order %s not immediately filled (status: %s). Polling...",
                    order.order_id,
                    order.status,
                )
                try:
                    order = await self._wait_for_fill(symbol, str(order.order_id))
                except Exception as poll_exc:
                    self._logger.warning(
                        "Polling failed for order %s: %s", order.order_id, poll_exc
                    )
                    # We continue with the last known state of 'order'

            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="MARKET",
                status=order.status,
                latency_seconds=elapsed,
            )

            if order.status == "FILLED":
                self._metrics.record_order_filled(symbol, side)
                executed_price = order.executed_price or (
                    float(order.price) if order.price else 0.0
                )
                filled_quantity = (
                    order.executed_quantity if order.executed_quantity > 0 else quantity
                )
                # Record trade in portfolio manager for PnL tracking
                if self._portfolio_manager is not None:
                    if side == "BUY":
                        await self._portfolio_manager.open_position(
                            symbol=symbol,
                            quantity=filled_quantity,
                            price=executed_price,
                            order_id=str(order.order_id),
                            market="spot",
                        )
                        # Register position in risk manager
                        self._risk_manager.register_open_position(
                            symbol,
                            filled_quantity * executed_price,
                            executed_price,
                        )
                    elif side == "SELL":
                        _, pnl = await self._portfolio_manager.close_position(
                            symbol=symbol,
                            price=executed_price,
                            order_id=str(order.order_id),
                            market="spot",
                        )
                        # Record realized PnL in risk manager
                        self._risk_manager.record_trade(
                            symbol=symbol,
                            pnl=pnl,
                            portfolio_value=portfolio_value,
                        )
                        # Record realized PnL in metrics
                        self._metrics.record_realized_pnl(symbol, pnl)
                        # Register position close in risk manager
                        self._risk_manager.register_close_position(symbol)

            self._logger.info(
                "Market order placed: %s %s %s (qty: %.4f, status: %s)",
                side,
                symbol,
                order.order_id,
                filled_quantity if order.status == "FILLED" else quantity,
                order.status,
            )

            return order

        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - start_time
            self._logger.error("Failed to place market order: %s", exc)
            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="MARKET",
                status="FAILED",
                latency_seconds=elapsed,
            )
            self._metrics.record_api_error(
                "place_market_order", str(type(exc).__name__)
            )
            raise

    async def place_limit_order(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        quantity: float | None = None,
    ) -> OrderInfo:
        """Place a limit order with risk checks.

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            side: Order side ("BUY" or "SELL")
            price: Limit price
            quantity: Order quantity. If None, calculates from config.

        Returns:
            OrderInfo: Information about placed order

        Raises:
            RuntimeError: If risk checks fail or order placement fails
        """
        if not self._config.enabled:
            raise RuntimeError("Trading executor is disabled")

        # Check if trading is allowed
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            self._metrics.record_risk_block(symbol, reason)
            raise RuntimeError(f"Trading blocked: {reason}")

        # Get account info for portfolio value
        try:
            account_info = await self._client.get_account_info()
            portfolio_value = account_info.available_balance
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to get account info: %s", exc)
            self._metrics.record_api_error("get_account_info", str(type(exc).__name__))
            raise RuntimeError("Failed to get account info") from exc

        # Calculate quantity if not provided
        if quantity is None:
            quantity = self._calculate_quantity(symbol, portfolio_value)

        # Check position limits (skip for SELL — closing a position shouldn't be blocked)
        if side == "BUY":
            allowed, risk_reason = self._risk_manager.check_position_limit(
                symbol, quantity, portfolio_value
            )
            if not allowed:
                self._metrics.record_risk_block(symbol, risk_reason)
                raise RuntimeError(f"Position limit check failed: {risk_reason}")

        # Place order
        start_time = time.perf_counter()
        try:
            order = await self._client.place_limit_order(
                symbol=symbol,
                side=side,
                price=price,
                quantity=quantity,
            )
            elapsed = time.perf_counter() - start_time

            # Order Reconciliation - Poll for fill if not immediately filled
            if order.status not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                self._logger.info(
                    "Limit order %s status: %s. Polling...",
                    order.order_id,
                    order.status,
                )
                try:
                    order = await self._wait_for_fill(symbol, str(order.order_id))
                except Exception as poll_exc:
                    self._logger.warning(
                        "Polling failed for limit order %s: %s",
                        order.order_id,
                        poll_exc,
                    )

            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="LIMIT",
                status=order.status,
                latency_seconds=elapsed,
            )

            self._logger.info(
                "Limit order placed: %s %s %s @ %.4f (qty: %.4f, status: %s)",
                side,
                symbol,
                order.order_id,
                price,
                quantity,
                order.status,
            )

            return order

        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - start_time
            self._logger.error("Failed to place limit order: %s", exc)
            self._metrics.record_order_placed(
                symbol=symbol,
                order_type="LIMIT",
                status="FAILED",
                latency_seconds=elapsed,
            )
            self._metrics.record_api_error("place_limit_order", str(type(exc).__name__))
            raise

    async def cancel_order(self, symbol: str, order_id: str | int) -> dict[str, Any]:
        """Cancel an existing order.

        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel

        Returns:
            dict: Cancellation response from Binance
        """
        try:
            result = await self._client.cancel_order(symbol, order_id)
            self._metrics.record_order_cancelled(symbol, "user_request")

            self._logger.info("Order cancelled: %s %s", symbol, order_id)

            return result

        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to cancel order %s: %s", order_id, exc)
            self._metrics.record_api_error("cancel_order", str(type(exc).__name__))
            raise

    async def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all open orders for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            int: Number of orders cancelled
        """
        try:
            count = await self._client.cancel_all_orders(symbol)
            self._logger.info("Cancelled %d orders for %s", count, symbol)

            for _ in range(count):
                self._metrics.record_order_cancelled(symbol, "bulk_cancel")

            return count

        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to cancel all orders for %s: %s", symbol, exc)
            self._metrics.record_api_error("cancel_all_orders", str(type(exc).__name__))
            raise

    def _calculate_quantity(self, symbol: str, portfolio_value: float) -> float:
        """Calculate order quantity based on config and portfolio value.

        Args:
            symbol: Trading pair symbol
            portfolio_value: Current available balance

        Returns:
            float: Order quantity to place
        """
        order_size_usdt = self._config.order_size_usdt
        return order_size_usdt

    def stop(self) -> None:
        """Stop the trading loop."""
        self._running = False

    async def on_signal(self, signal: Signal) -> None:
        """Handle trading signal from StrategyEngine.

        Args:
            signal: Trading signal (BUY/SELL/HOLD)

        Spot-aware behavior:
        - BUY: Uses order_size_usdt via quoteOrderQty
        - SELL: Sells all held base asset (checks balance first)
        - HOLD: No action
        - Disabled executor: Returns early without client access
        """
        if signal.type == SignalType.HOLD:
            return

        if not self._config.enabled:
            self._logger.info("Signal ignored (executor disabled): %s", signal)
            return

        try:
            if signal.type == SignalType.BUY:
                # Check for duplicate orders/existing positions
                if self._portfolio_manager and self._portfolio_manager.has_position(
                    signal.symbol,
                    market="spot",
                ):
                    self._logger.info(
                        "BUY signal ignored: Position already exists for %s",
                        signal.symbol,
                    )
                    return

                order = await self.place_market_order(
                    signal.symbol, "BUY", self._config.order_size_usdt
                )
                if order.status == "FILLED":
                    filled_quantity = (
                        order.executed_quantity
                        if order.executed_quantity > 0
                        else self._config.order_size_usdt
                    )
                    filled_price = order.executed_price or (
                        float(order.price) if order.price else signal.price
                    )
                    await self._notifier.send_trade_alert(
                        symbol=signal.symbol,
                        side="BUY",
                        quantity=filled_quantity,
                        price=filled_price,
                        pnl=None,
                        market="spot",
                    )
                else:
                    self._logger.info(
                        "BUY order not filled yet for %s (status: %s)",
                        signal.symbol,
                        order.status,
                    )
            elif signal.type == SignalType.SELL:
                base_asset = signal.symbol.removesuffix("USDT")
                balance = await self._client.get_asset_balance(base_asset)
                if balance > 0:
                    entry_price: float | None = None
                    position_qty: float | None = None
                    if self._portfolio_manager is not None:
                        position = self._portfolio_manager.get_position(
                            signal.symbol,
                            market="spot",
                        )
                        if position is not None:
                            entry_price = position.entry_price
                            position_qty = position.quantity

                    normalized_quantity = await self._client.normalize_sell_quantity(
                        signal.symbol,
                        balance,
                    )
                    if normalized_quantity is None:
                        self._logger.debug(
                            "Skipping SELL for %s: balance %.12f below min LOT_SIZE",
                            signal.symbol,
                            balance,
                        )
                        return

                    order = await self.place_market_order(
                        signal.symbol, "SELL", balance
                    )
                    if order.status == "FILLED":
                        filled_quantity = (
                            order.executed_quantity
                            if order.executed_quantity > 0
                            else balance
                        )
                        filled_price = order.executed_price or (
                            float(order.price) if order.price else signal.price
                        )
                        pnl = None
                        if entry_price is not None and position_qty is not None:
                            pnl_quantity = (
                                filled_quantity if filled_quantity > 0 else position_qty
                            )
                            pnl = (filled_price - entry_price) * pnl_quantity
                        await self._notifier.send_trade_alert(
                            symbol=signal.symbol,
                            side="SELL",
                            quantity=filled_quantity,
                            price=filled_price,
                            pnl=pnl,
                            market="spot",
                        )
                    else:
                        self._logger.info(
                            "SELL order not filled yet for %s (status: %s)",
                            signal.symbol,
                            order.status,
                        )
                else:
                    self._logger.info("SELL signal but no %s balance", base_asset)
                    await self._notifier.send_alert(
                        f"<b>Signal skipped</b> [spot]\n"
                        f"{signal.symbol} SELL — no {base_asset} balance"
                    )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Signal rejected: %s — %s", signal, exc)
            await self._notifier.send_alert(
                f"<b>Signal rejected</b> [spot]\n"
                f"{signal.symbol} {signal.type.value} — {exc}"
            )
