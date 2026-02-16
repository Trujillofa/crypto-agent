"""Internal paper trading executor — simulates fills without Binance API."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.execution.metrics import ExecutionMetrics
from src.notifications.telegram import TelegramNotifier
from src.portfolio.manager import PortfolioManager
from src.risk.manager import RiskManager
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass
class PaperPosition:
    """A simulated open position."""

    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: float  # base asset units
    entry_price: float
    open_time: float

    @property
    def notional(self) -> float:
        return self.quantity * self.entry_price

    def pnl(self, current_price: float) -> float:
        if self.side == "LONG":
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity


@dataclass
class PaperTradingConfig:
    """Configuration for paper trading executor."""

    enabled: bool = True
    order_size_usdt: float = 200.0
    initial_balance: float = 10000.0
    symbols: list[str] = field(default_factory=list)
    futures_symbols: list[str] = field(default_factory=list)
    futures_leverage: int = 3
    fee_rate_spot: float = 0.001  # 0.1% per trade (Binance spot taker)
    fee_rate_futures: float = 0.0004  # 0.04% per trade (Binance futures taker)


class PaperExecutor:
    """Simulates spot + futures trading entirely in memory.

    No Binance API calls. Fills happen at signal price.
    Tracks balances, positions, and PnL. Sends Telegram alerts.
    """

    def __init__(
        self,
        config: PaperTradingConfig,
        risk_manager: RiskManager,
        metrics: ExecutionMetrics,
        notifier: TelegramNotifier | None = None,
        portfolio_manager: PortfolioManager | None = None,
    ) -> None:
        self._config = config
        self._risk_manager = risk_manager
        self._metrics = metrics
        self._notifier = notifier or TelegramNotifier()
        self._portfolio_manager = portfolio_manager
        self._logger = get_logger("PaperExecutor")

        # Simulated state
        self._balance: float = config.initial_balance
        self._positions: dict[str, PaperPosition] = {}  # symbol -> position
        self._trade_count: int = 0
        self._realized_pnl: float = 0.0
        self._total_fees: float = 0.0
        self._running = False

    async def __aenter__(self) -> PaperExecutor:
        if not self._config.enabled:
            self._logger.info("PaperExecutor disabled")
            return self

        await self._notifier.__aenter__()
        self._metrics.start_trading()
        self._logger.info(
            "PaperExecutor initialized: balance=%.2f USDT, order_size=%.2f USDT",
            self._balance,
            self._config.order_size_usdt,
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._notifier.__aexit__(exc_type, exc, tb)
        self._metrics.stop_trading()
        self._logger.info(
            "PaperExecutor stopped: balance=%.2f, realized_pnl=%.2f, fees=%.2f, trades=%d",
            self._balance,
            self._realized_pnl,
            self._total_fees,
            self._trade_count,
        )

    async def run(self) -> None:
        """No-op run loop — paper executor is event-driven via on_signal."""
        pass

    def stop(self) -> None:
        self._running = False

    async def on_signal(self, signal: Signal) -> None:
        """Handle a trading signal by simulating the fill."""
        if signal.type == SignalType.HOLD:
            return

        if not self._config.enabled:
            return

        is_futures = signal.trading_mode == "futures"
        market_tag = "futures" if is_futures else "spot"

        try:
            if signal.type == SignalType.BUY:
                await self._handle_buy(signal, market_tag, is_futures)
            elif signal.type == SignalType.SELL:
                await self._handle_sell(signal, market_tag, is_futures)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Paper signal failed: %s — %s", signal.symbol, exc)
            await self._notifier.send_alert(
                f"<b>Paper signal failed</b> [{market_tag}]\n"
                f"{signal.symbol} {signal.type.value} — {exc}"
            )

    async def _handle_buy(
        self, signal: Signal, market_tag: str, is_futures: bool
    ) -> None:
        pos_key = f"{signal.symbol}:{market_tag}"

        if pos_key in self._positions:
            self._logger.info(
                "BUY ignored: already have %s position for %s",
                market_tag,
                signal.symbol,
            )
            return

        # Check balance
        order_usdt = self._config.order_size_usdt
        if is_futures:
            # Futures uses margin = order_size / leverage
            margin_needed = order_usdt / self._config.futures_leverage
        else:
            margin_needed = order_usdt

        if margin_needed > self._balance:
            await self._notifier.send_alert(
                f"<b>Paper signal skipped</b> [{market_tag}]\n"
                f"{signal.symbol} BUY — insufficient balance "
                f"(need {margin_needed:.2f}, have {self._balance:.2f})"
            )
            return

        # Risk check
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            await self._notifier.send_alert(
                f"<b>Paper signal blocked</b> [{market_tag}]\n"
                f"{signal.symbol} BUY — {reason}"
            )
            return

        # Simulate fill with fee
        fee_rate = self._config.fee_rate_futures if is_futures else self._config.fee_rate_spot
        fee = order_usdt * fee_rate
        quantity = order_usdt / signal.price
        self._balance -= margin_needed + fee
        self._total_fees += fee
        self._positions[pos_key] = PaperPosition(
            symbol=signal.symbol,
            side="LONG",
            quantity=quantity,
            entry_price=signal.price,
            open_time=time.time(),
        )
        self._trade_count += 1

        # Record in portfolio DB for overseer visibility
        if self._portfolio_manager:
            try:
                await self._portfolio_manager.open_position(
                    symbol=pos_key, quantity=quantity, price=signal.price,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Portfolio DB write failed (buy): %s", exc)

        leverage_text = f" ({self._config.futures_leverage}x)" if is_futures else ""
        self._logger.info(
            "Paper BUY %s [%s%s]: qty=%.6f @ %.4f (%.2f USDT, fee=%.2f)",
            signal.symbol,
            market_tag,
            leverage_text,
            quantity,
            signal.price,
            order_usdt,
            fee,
        )

        self._metrics.record_order_placed(
            symbol=signal.symbol,
            order_type=f"PAPER_{market_tag.upper()}",
            status="FILLED",
            latency_seconds=0.0,
        )
        self._metrics.record_order_filled(signal.symbol, "BUY")

        await self._notifier.send_trade_alert(
            symbol=signal.symbol,
            side="BUY",
            quantity=order_usdt,
            price=signal.price,
            pnl=None,
            market=f"paper-{market_tag}{leverage_text}",
        )

    async def _handle_sell(
        self, signal: Signal, market_tag: str, is_futures: bool
    ) -> None:
        pos_key = f"{signal.symbol}:{market_tag}"
        position = self._positions.get(pos_key)

        if position is None:
            self._logger.info(
                "SELL ignored: no %s position for %s", market_tag, signal.symbol
            )
            await self._notifier.send_alert(
                f"<b>Paper signal skipped</b> [{market_tag}]\n"
                f"{signal.symbol} SELL — no position"
            )
            return

        # Calculate PnL with fees
        gross_pnl = position.pnl(signal.price)
        if is_futures:
            gross_pnl *= self._config.futures_leverage

        fee_rate = self._config.fee_rate_futures if is_futures else self._config.fee_rate_spot
        sell_notional = position.quantity * signal.price
        fee = sell_notional * fee_rate
        net_pnl = gross_pnl - fee
        self._total_fees += fee

        # Close position
        if is_futures:
            margin_returned = position.notional / self._config.futures_leverage
        else:
            margin_returned = position.notional

        self._balance += margin_returned + net_pnl
        self._realized_pnl += net_pnl
        del self._positions[pos_key]
        self._trade_count += 1

        # Record in risk manager
        self._risk_manager.record_trade(signal.symbol, net_pnl, self._balance)

        # Record in portfolio DB for overseer visibility
        if self._portfolio_manager:
            try:
                await self._portfolio_manager.close_position(
                    symbol=pos_key, price=signal.price,
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("Portfolio DB write failed (sell): %s", exc)

        leverage_text = f" ({self._config.futures_leverage}x)" if is_futures else ""
        self._logger.info(
            "Paper SELL %s [%s%s]: qty=%.6f @ %.4f, PnL=%.2f (fee=%.2f) USDT, balance=%.2f",
            signal.symbol,
            market_tag,
            leverage_text,
            position.quantity,
            signal.price,
            net_pnl,
            fee,
            self._balance,
        )

        self._metrics.record_order_placed(
            symbol=signal.symbol,
            order_type=f"PAPER_{market_tag.upper()}",
            status="FILLED",
            latency_seconds=0.0,
        )
        self._metrics.record_order_filled(signal.symbol, "SELL")

        await self._notifier.send_trade_alert(
            symbol=signal.symbol,
            side="SELL",
            quantity=position.quantity,
            price=signal.price,
            pnl=net_pnl,
            market=f"paper-{market_tag}{leverage_text}",
        )

    def get_positions(self) -> dict[str, Any]:
        """Return current positions for status reporting."""
        result = {}
        for key, pos in self._positions.items():
            result[key] = {
                "side": pos.side,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "notional": pos.notional,
            }
        return result

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def trade_count(self) -> int:
        return self._trade_count
