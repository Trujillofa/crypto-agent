"""Pure backtest metric calculations."""

from __future__ import annotations

import math

from src.backtest.models import BacktestConfig, BacktestResult, Trade

_TIMEFRAME_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "3d": 4320,
    "1w": 10080,
}


def calculate_backtest_metrics(
    *,
    config: BacktestConfig,
    equity_curve: list[float],
    trades: list[Trade],
    blocked_buy_count: int,
    basis_blocked_buy_count: int,
    dislocation_blocked_buy_count: int,
    queued_signal_count: int = 0,
    unfilled_signal_count: int = 0,
    funding_settlement_count: int = 0,
) -> BacktestResult:
    """Return portfolio metrics from already-accounted equity and trades."""
    final_equity = equity_curve[-1] if equity_curve else config.initial_capital
    total_return = final_equity - config.initial_capital
    total_return_pct = total_return / config.initial_capital * 100

    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0
    gross_profit = sum(trade.pnl for trade in wins)
    gross_loss = abs(sum(trade.pnl for trade in losses))
    profit_factor = gross_profit / gross_loss if gross_loss else math.inf if gross_profit else 0.0
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    avg_win_loss_ratio = avg_win / avg_loss if avg_loss else math.inf if avg_win else 0.0

    peak = config.initial_capital
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    returns = [
        (current - previous) / previous if previous > 0 else 0.0
        for previous, current in zip(equity_curve, equity_curve[1:], strict=False)
    ]
    sharpe_ratio = 0.0
    sortino_ratio = 0.0
    if returns:
        mean_return = sum(returns) / len(returns)
        variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
        std_return = math.sqrt(variance)
        periods_per_year = int(365 * 24 * 60 / _TIMEFRAME_MINUTES.get(config.timeframe, 1))
        if std_return > 0:
            sharpe_ratio = mean_return / std_return * math.sqrt(periods_per_year)
        negative_returns = [value for value in returns if value < 0]
        if negative_returns:
            downside_std = math.sqrt(sum(value**2 for value in negative_returns) / len(returns))
            if downside_std > 0:
                sortino_ratio = mean_return / downside_std * math.sqrt(periods_per_year)

    return BacktestResult(
        total_return=total_return,
        total_return_pct=total_return_pct,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        total_trades=len(trades),
        trades=trades,
        final_equity=final_equity,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        profit_factor=profit_factor,
        avg_win_loss_ratio=avg_win_loss_ratio,
        blocked_buy_count=blocked_buy_count,
        basis_blocked_buy_count=basis_blocked_buy_count,
        dislocation_blocked_buy_count=dislocation_blocked_buy_count,
        queued_signal_count=queued_signal_count,
        unfilled_signal_count=unfilled_signal_count,
        funding_settlement_count=funding_settlement_count,
    )
