"""Unit tests for higher-TF trend-following probe helpers (no DB/network).

Verifies the daily resample, SMA, trend-filter return/drawdown math, and verdict logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.probe_higher_tf_trend_following import (
    DailyBar,
    ProbeConfig,
    StrategyStats,
    SymbolResult,
    decide_verdict,
    evaluate_window,
    resample_daily,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbols": ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        "source_timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2026-06-01T00:00:00",
        "ma_windows": (50, 100, 200),
        "one_way_fee_pct": 0.04,
        "min_symbol_majority": 0.66,
        "min_total_return_pct": 0.0,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _bars(prices: list[float]) -> list[DailyBar]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [DailyBar(day=base + timedelta(days=i), close_price=p) for i, p in enumerate(prices)]


def _stat(window: int, *, passes: bool) -> StrategyStats:
    """Build a StrategyStats that passes or fails the gate deterministically."""
    if passes:
        return StrategyStats(window, 100, 5, 60.0, 50.0, 10.0, 1.5, 0.5, 20.0, 40.0)
    return StrategyStats(window, 100, 5, 60.0, -5.0, 10.0, 0.2, 0.5, 50.0, 40.0)


def test_resample_daily_takes_last_close_per_utc_day() -> None:
    rows = [
        {"time": datetime(2024, 1, 1, h, tzinfo=UTC), "close_price": float(h)} for h in range(24)
    ]
    rows += [
        {"time": datetime(2024, 1, 2, h, tzinfo=UTC), "close_price": 100.0 + h} for h in range(24)
    ]
    daily = resample_daily(rows)
    assert len(daily) == 2
    assert daily[0].close_price == 23.0
    assert daily[1].close_price == 123.0


def test_trend_filter_sidesteps_crash_and_cuts_drawdown() -> None:
    rise = [100 * (1.02**i) for i in range(60)]
    crash = [rise[-1] * (0.95**i) for i in range(1, 30)]
    recover = [crash[-1] * (1.02**i) for i in range(1, 60)]
    stat = evaluate_window(_bars(rise + crash + recover), 50, 0.04)
    assert stat is not None
    # Trend filter exits the crash → strictly lower drawdown and higher return than buy-hold.
    assert stat.strat_max_dd_pct < stat.bh_max_dd_pct
    assert stat.strat_total_return_pct > stat.bh_total_return_pct
    assert 0.0 < stat.time_in_market_pct < 100.0
    assert stat.passes is True


def test_evaluate_window_returns_none_when_too_short() -> None:
    assert evaluate_window(_bars([100.0] * 40), 50, 0.04) is None


def test_decide_verdict_has_pulse_on_symbol_majority() -> None:
    symbols = [
        SymbolResult("BTCUSDT", 800, (_stat(50, passes=True),)),
        SymbolResult("ETHUSDT", 800, (_stat(50, passes=True),)),
        SymbolResult("SOLUSDT", 800, (_stat(50, passes=False),)),
    ]
    verdict, windows = decide_verdict(symbols, _config())
    assert verdict == "HAS_PULSE"
    assert 50 in windows


def test_decide_verdict_weak_edge_without_majority() -> None:
    symbols = [
        SymbolResult("BTCUSDT", 800, (_stat(50, passes=True),)),
        SymbolResult("ETHUSDT", 800, (_stat(50, passes=False),)),
        SymbolResult("SOLUSDT", 800, (_stat(50, passes=False),)),
    ]
    verdict, windows = decide_verdict(symbols, _config())
    assert verdict == "WEAK_EDGE"
    assert windows == ()


def test_decide_verdict_no_pulse_when_nothing_passes() -> None:
    symbols = [
        SymbolResult("BTCUSDT", 800, (_stat(50, passes=False),)),
        SymbolResult("ETHUSDT", 800, (_stat(50, passes=False),)),
    ]
    verdict, windows = decide_verdict(symbols, _config())
    assert verdict == "NO_PULSE"
    assert windows == ()
