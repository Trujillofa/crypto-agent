from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Mapping

from src.features.reader import IndicatorReader
from src.strategy.aggregator import SignalAggregator
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    symbol: str
    timeframe: str
    start_date: str  # ISO 8601
    end_date: str  # ISO 8601
    initial_capital: float = 10000.0
    fee_rate: float = 0.001  # 0.1%
    stop_loss_pct: float = 0.0  # 0.0 = disabled
    take_profit_pct: float = 0.0  # 0.0 = disabled
    strategy_classes: list[type[BaseStrategy]] = field(default_factory=list)
    strategy_configs: list[Mapping[str, object] | None] = field(default_factory=list)
    aggregator_config: Mapping[str, object] = field(default_factory=dict)


@dataclass
class Trade:
    """Record of a simulated trade."""

    entry_time: str
    exit_time: str
    side: str  # BUY
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_reason: str = "SIGNAL"


@dataclass
class BacktestResult:
    """Results of a backtest."""

    total_return: float
    total_return_pct: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    trades: list[Trade]
    final_equity: float


class BacktestEngine:
    """Engine to simulate trading strategies on historical data."""

    def __init__(self, config: BacktestConfig, reader: IndicatorReader) -> None:
        self._config = config
        self._reader = reader
        self._logger = get_logger(self.__class__.__name__)
        self._aggregator = SignalAggregator(config.aggregator_config)

        self._cash = config.initial_capital
        self._position_qty = 0.0
        self._position_entry_price = 0.0
        self._position_entry_fee = 0.0
        self._position_entry_time = ""
        self._equity_curve: list[float] = []
        self._trades: list[Trade] = []

    async def run(self) -> BacktestResult:
        """Execute the backtest."""
        self._logger.info(f"Starting backtest for {self._config.symbol}...")

        data = await self._reader.fetch_range(
            self._config.symbol,
            self._config.timeframe,
            self._config.start_date,
            self._config.end_date,
        )

        if not data:
            self._logger.warning("No data found for backtest range")
            return self._create_empty_result()

        self._logger.info(f"Loaded {len(data)} data points")

        strategies = []
        for idx, cls in enumerate(self._config.strategy_classes):
            cfg = None
            if idx < len(self._config.strategy_configs):
                cfg = self._config.strategy_configs[idx]
            strategies.append(cls(cfg))

        for row in data:
            current_time = str(row["time"])
            current_price = row["close_price"]
            high_price = row.get("high_price", current_price)
            low_price = row.get("low_price", current_price)

            if self._position_qty > 0:
                if self._config.stop_loss_pct > 0:
                    sl_price = self._position_entry_price * (
                        1 - self._config.stop_loss_pct
                    )
                    if low_price <= sl_price:
                        self._close_position(current_time, sl_price, reason="STOP_LOSS")
                        continue

                if self._config.take_profit_pct > 0:
                    tp_price = self._position_entry_price * (
                        1 + self._config.take_profit_pct
                    )
                    if high_price >= tp_price:
                        self._close_position(
                            current_time, tp_price, reason="TAKE_PROFIT"
                        )
                        continue

            signals = []
            for strategy in strategies:
                try:
                    sig = await strategy.evaluate(self._config.symbol, row)
                    signals.append(sig)
                except Exception as e:
                    self._logger.error(f"Strategy error at {current_time}: {e}")

            final_signal = self._aggregator.aggregate(self._config.symbol, signals)

            self._process_signal(final_signal, current_time, current_price)

            equity = self._cash
            if self._position_qty > 0:
                equity += self._position_qty * current_price
            self._equity_curve.append(equity)

        if self._position_qty > 0:
            last_price = data[-1]["close_price"]
            last_time = str(data[-1]["time"])
            self._close_position(last_time, last_price)

        return self._calculate_metrics()

    def _process_signal(self, signal: Signal, timestamp: str, price: float) -> None:
        if signal.type == SignalType.BUY and self._position_qty == 0:
            qty = (self._cash * (1 - self._config.fee_rate)) / price
            cost = qty * price
            fee = cost * self._config.fee_rate

            self._cash -= cost + fee
            self._position_qty = qty
            self._position_entry_price = price
            self._position_entry_fee = fee
            self._position_entry_time = timestamp

        elif signal.type == SignalType.SELL and self._position_qty > 0:
            self._close_position(timestamp, price, reason="SIGNAL")

    def _close_position(
        self, timestamp: str, price: float, reason: str = "SIGNAL"
    ) -> None:
        revenue = self._position_qty * price
        fee = revenue * self._config.fee_rate
        net_revenue = revenue - fee

        cost_basis = self._position_qty * self._position_entry_price
        total_cost = cost_basis + self._position_entry_fee
        pnl = net_revenue - total_cost
        return_pct = (pnl / total_cost) * 100 if total_cost > 0 else 0.0

        trade = Trade(
            entry_time=self._position_entry_time,
            exit_time=timestamp,
            side="BUY",
            entry_price=self._position_entry_price,
            exit_price=price,
            quantity=self._position_qty,
            pnl=pnl,
            return_pct=return_pct,
            exit_reason=reason,
        )

        self._trades.append(trade)

        self._cash += net_revenue
        self._position_qty = 0.0
        self._position_entry_price = 0.0
        self._position_entry_fee = 0.0
        self._position_entry_time = ""

    def _calculate_metrics(self) -> BacktestResult:
        final_equity = (
            self._equity_curve[-1]
            if self._equity_curve
            else self._config.initial_capital
        )
        total_return = final_equity - self._config.initial_capital
        total_return_pct = (total_return / self._config.initial_capital) * 100

        wins = [t for t in self._trades if t.pnl > 0]
        win_rate = (len(wins) / len(self._trades)) * 100 if self._trades else 0.0

        peak = self._config.initial_capital
        max_dd = 0.0
        for equity in self._equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        return BacktestResult(
            total_return=total_return,
            total_return_pct=total_return_pct,
            max_drawdown=max_dd,
            win_rate=win_rate,
            total_trades=len(self._trades),
            trades=self._trades,
            final_equity=final_equity,
        )

    def _create_empty_result(self) -> BacktestResult:
        return BacktestResult(
            total_return=0.0,
            total_return_pct=0.0,
            max_drawdown=0.0,
            win_rate=0.0,
            total_trades=0,
            trades=[],
            final_equity=self._config.initial_capital,
        )
