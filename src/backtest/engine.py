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
    slippage_pct: float = 0.001  # 0.1% slippage per trade
    risk_per_trade: float = (
        0.02  # 2% risk of equity per trade (used if use_atr_sizing=True)
    )
    use_atr_sizing: bool = False
    atr_multiplier: float = 1.5  # Stop distance = 1.5 * ATR
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
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    avg_win_loss_ratio: float


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

            atr = row.get("atr_14", 0.0)
            self._process_signal(final_signal, current_time, current_price, atr)

            equity = self._cash
            if self._position_qty > 0:
                equity += self._position_qty * current_price
            self._equity_curve.append(equity)

        if self._position_qty > 0:
            last_price = data[-1]["close_price"]
            last_time = str(data[-1]["time"])
            self._close_position(last_time, last_price)

        return self._calculate_metrics()

    def _process_signal(
        self, signal: Signal, timestamp: str, price: float, atr: float = 0.0
    ) -> None:
        if signal.type == SignalType.BUY and self._position_qty == 0:
            # Apply slippage on entry logic first to determine actual fill price
            entry_price = price * (1 + self._config.slippage_pct)

            if self._config.use_atr_sizing and atr > 0:
                # Risk = Equity * Risk %
                # Stop Distance = ATR * Multiplier
                # Qty = Risk / Stop Distance
                current_equity = self._cash  # No position, so equity == cash
                risk_amount = current_equity * self._config.risk_per_trade
                stop_distance = atr * self._config.atr_multiplier

                # Avoid division by zero
                if stop_distance > 0:
                    target_qty = risk_amount / stop_distance
                else:
                    target_qty = 0.0

                # Check max purchasing power using ENTRY PRICE (not signal price)
                max_qty = (self._cash * (1 - self._config.fee_rate)) / entry_price
                qty = min(target_qty, max_qty)
            else:
                # Default: All-in
                qty = (self._cash * (1 - self._config.fee_rate)) / entry_price

            cost = qty * entry_price
            fee = cost * self._config.fee_rate

            self._cash -= cost + fee
            self._position_qty = qty
            self._position_entry_price = entry_price
            self._position_entry_fee = fee
            self._position_entry_time = timestamp

        elif signal.type == SignalType.SELL and self._position_qty > 0:
            self._close_position(timestamp, price, reason="SIGNAL")

    def _close_position(
        self, timestamp: str, price: float, reason: str = "SIGNAL"
    ) -> None:
        # Apply slippage on exit
        # Selling pushes price down, so we get less
        exit_price = price * (1 - self._config.slippage_pct)

        revenue = self._position_qty * exit_price
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
            exit_price=exit_price,
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
        import math

        final_equity = (
            self._equity_curve[-1]
            if self._equity_curve
            else self._config.initial_capital
        )
        total_return = final_equity - self._config.initial_capital
        total_return_pct = (total_return / self._config.initial_capital) * 100

        # Trade Stats
        wins = [t for t in self._trades if t.pnl > 0]
        losses = [t for t in self._trades if t.pnl <= 0]

        win_rate = (len(wins) / len(self._trades)) * 100 if self._trades else 0.0

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else float("inf")
            if gross_profit > 0
            else 0.0
        )

        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        avg_win_loss_ratio = (
            (avg_win / avg_loss)
            if avg_loss > 0
            else float("inf")
            if avg_win > 0
            else 0.0
        )

        # Drawdown
        peak = self._config.initial_capital
        max_dd = 0.0
        for equity in self._equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        # Advanced Metrics (Sharpe/Sortino)
        # Calculate periodic returns from equity curve
        sharpe_ratio = 0.0
        sortino_ratio = 0.0

        if len(self._equity_curve) > 1:
            returns = []
            for i in range(1, len(self._equity_curve)):
                prev = self._equity_curve[i - 1]
                curr = self._equity_curve[i]
                if prev > 0:
                    returns.append((curr - prev) / prev)
                else:
                    returns.append(0.0)

            if returns:
                # Calculate mean and std manually to avoid numpy dependency
                mean_return = sum(returns) / len(returns)
                variance = sum((x - mean_return) ** 2 for x in returns) / len(returns)
                std_return = math.sqrt(variance)

                # Annualize (assuming 1m data = 525600 periods/year)
                # Adjust periods based on config timeframe in future
                periods_per_year = 365 * 24 * 60

                if std_return > 0:
                    sharpe_ratio = (mean_return / std_return) * math.sqrt(
                        periods_per_year
                    )

                # Sortino (Downside deviation)
                negative_returns = [r for r in returns if r < 0]
                if negative_returns:
                    downside_variance = sum(x**2 for x in negative_returns) / len(
                        returns
                    )  # Downside deviation uses total N
                    downside_std = math.sqrt(downside_variance)
                    if downside_std > 0:
                        sortino_ratio = (mean_return / downside_std) * math.sqrt(
                            periods_per_year
                        )

        return BacktestResult(
            total_return=total_return,
            total_return_pct=total_return_pct,
            max_drawdown=max_dd,
            win_rate=win_rate,
            total_trades=len(self._trades),
            trades=self._trades,
            final_equity=final_equity,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            profit_factor=profit_factor,
            avg_win_loss_ratio=avg_win_loss_ratio,
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
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            profit_factor=0.0,
            avg_win_loss_ratio=0.0,
        )
