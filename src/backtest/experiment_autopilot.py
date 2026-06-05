from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GateConfig:
    """Validation gates for experiment acceptance."""

    min_trades: int = 0
    min_wfo_trades: int = 20
    min_wfo_sharpe: float = 0.5
    max_drawdown_pct: float = 10.0
    max_bootstrap_p_loss_pct: float = 25.0
    min_oos_return_pct: float = 0.0
    max_profit_concentration_pct: float = 50.0


@dataclass(frozen=True)
class WfoWindow:
    """One walk-forward window definition."""

    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class WfoWindowResult:
    """Metrics for one walk-forward test window."""

    window_index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    total_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float


@dataclass(frozen=True)
class ExperimentSummary:
    """Final result with gate decision."""

    symbol: str
    timeframe: str
    start: str
    end: str
    total_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    wfo_windows: int
    wfo_total_trades: int
    wfo_mean_sharpe: float
    wfo_total_return_pct: float
    bootstrap_p_loss_pct: float
    profit_concentration_pct: float
    passes_gates: bool
    failure_reasons: list[str] = field(default_factory=list)
    blocked_buy_count: int = 0


def add_months(base: datetime, months: int) -> datetime:
    """Add months while clamping day to valid month range."""
    if months < 0:
        raise ValueError("months must be >= 0")

    month_index = (base.month - 1) + months
    year = base.year + (month_index // 12)
    month = (month_index % 12) + 1

    # Month lengths with leap-year handling for February.
    if month == 2:
        leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
        max_day = 29 if leap else 28
    elif month in {4, 6, 9, 11}:
        max_day = 30
    else:
        max_day = 31

    day = min(base.day, max_day)
    return base.replace(year=year, month=month, day=day)


def build_wfo_windows(
    start: str,
    end: str,
    train_months: int,
    test_months: int,
) -> list[WfoWindow]:
    """Build rolling WFO windows using train+test month spans."""
    if train_months <= 0 or test_months <= 0:
        raise ValueError("train_months and test_months must be > 0")

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    if start_dt >= end_dt:
        raise ValueError("start must be before end")

    windows: list[WfoWindow] = []
    current = start_dt

    while True:
        train_end = add_months(current, train_months)
        test_start = train_end
        test_end = add_months(test_start, test_months)

        if test_end > end_dt:
            break

        windows.append(
            WfoWindow(
                train_start=current.isoformat(),
                train_end=train_end.isoformat(),
                test_start=test_start.isoformat(),
                test_end=test_end.isoformat(),
            )
        )

        current = train_end

    return windows


def compound_returns_pct(returns_pct: list[float]) -> float:
    """Compound percentage returns into a single percentage return."""
    capital = 1.0
    for value in returns_pct:
        capital *= 1.0 + (value / 100.0)
    return (capital - 1.0) * 100.0


def bootstrap_loss_probability_pct(
    trade_returns_pct: list[float],
    iterations: int,
    seed: int = 42,
) -> float:
    """Estimate probability of total loss via bootstrap resampling."""
    if iterations <= 0:
        raise ValueError("iterations must be > 0")

    trade_count = len(trade_returns_pct)
    if trade_count == 0:
        return 100.0

    rng = random.Random(seed)
    losses = 0
    for _ in range(iterations):
        sample = rng.choices(trade_returns_pct, k=trade_count)
        total_return = compound_returns_pct(sample)
        if total_return < 0:
            losses += 1

    return (losses / iterations) * 100.0


def profit_concentration_pct(window_returns_pct: list[float]) -> float:
    """Share of positive return contributed by the best OOS window."""
    if not window_returns_pct:
        return 0.0

    positive_returns = [max(value, 0.0) for value in window_returns_pct]
    positive_total = sum(positive_returns)
    if positive_total <= 0:
        return 100.0

    return (max(positive_returns) / positive_total) * 100.0


def evaluate_gates(summary: ExperimentSummary, gates: GateConfig) -> list[str]:
    """Return gate failure reasons. Empty means pass."""
    failures: list[str] = []

    if gates.min_trades > 0 and summary.total_trades < gates.min_trades:
        failures.append(f"min_trades failed ({summary.total_trades} < {gates.min_trades})")

    if summary.wfo_windows == 0:
        failures.append("wfo_windows failed (no OOS windows built)")
    else:
        if summary.wfo_total_trades < gates.min_wfo_trades:
            failures.append(
                f"min_wfo_trades failed ({summary.wfo_total_trades} < {gates.min_wfo_trades})"
            )
        if summary.wfo_mean_sharpe < gates.min_wfo_sharpe:
            failures.append(
                "min_wfo_sharpe failed "
                f"({summary.wfo_mean_sharpe:.2f} < {gates.min_wfo_sharpe:.2f})"
            )

    if summary.max_drawdown_pct > gates.max_drawdown_pct:
        failures.append(
            "max_drawdown_pct failed "
            f"({summary.max_drawdown_pct:.2f}% > {gates.max_drawdown_pct:.2f}%)"
        )

    if summary.bootstrap_p_loss_pct > gates.max_bootstrap_p_loss_pct:
        failures.append(
            "max_bootstrap_p_loss_pct failed "
            f"({summary.bootstrap_p_loss_pct:.2f}% > {gates.max_bootstrap_p_loss_pct:.2f}%)"
        )

    if summary.wfo_total_return_pct < gates.min_oos_return_pct:
        failures.append(
            "min_oos_return_pct failed "
            f"({summary.wfo_total_return_pct:.2f}% < {gates.min_oos_return_pct:.2f}%)"
        )

    if summary.profit_concentration_pct > gates.max_profit_concentration_pct:
        failures.append(
            "max_profit_concentration_pct failed "
            f"({summary.profit_concentration_pct:.2f}% > {gates.max_profit_concentration_pct:.2f}%)"
        )

    return failures
