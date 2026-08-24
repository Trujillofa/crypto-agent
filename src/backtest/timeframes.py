"""Clock and timeframe contract for backtest, WFO, and metrics.

Bar ``time`` is the **open** of that bar (Binance kline open time). A ``1h`` bar
at ``10:00`` covers ``[10:00, 11:00)``. Strategies see that bar only after it is
complete (OHLCV including close). ``execution_parity_v2`` evaluates on the
closed bar and fills at the **next** bar's open. ``legacy_v1`` fills at the
signal bar close (same-bar, mild-optimistic; kept for reproducibility).

Unknown labels raise. Do not silently treat a missing timeframe as 1 minute.
"""

from __future__ import annotations

from datetime import timedelta

TIMEFRAME_HOURS: dict[str, float] = {
    "1m": 1.0 / 60.0,
    "3m": 3.0 / 60.0,
    "5m": 5.0 / 60.0,
    "15m": 0.25,
    "30m": 0.5,
    "1h": 1.0,
    "2h": 2.0,
    "4h": 4.0,
    "6h": 6.0,
    "8h": 8.0,
    "12h": 12.0,
    "1d": 24.0,
    "3d": 72.0,
    "1w": 168.0,
}


def timeframe_hours(timeframe: str) -> float:
    """Return bar length in hours, or raise if the label is not in the contract."""
    try:
        return TIMEFRAME_HOURS[timeframe]
    except KeyError:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from None


def timeframe_minutes(timeframe: str) -> int:
    """Return bar length in minutes (integer minutes for Sharpe annualization)."""
    return int(round(timeframe_hours(timeframe) * 60.0))


def timeframe_delta(timeframe: str) -> timedelta:
    """Return the bar duration as a timedelta."""
    return timedelta(hours=timeframe_hours(timeframe))


def periods_per_year(timeframe: str) -> int:
    """Bars per 365-day year for annualizing per-bar returns."""
    minutes = timeframe_minutes(timeframe)
    if minutes <= 0:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return int(365 * 24 * 60 / minutes)
