from __future__ import annotations

from datetime import datetime, timedelta

from scripts.probe_session_liquidity_router import (
    DEFAULT_WINDOWS,
    ProbeConfig,
    _coerce_datetime,
    evaluate_pulse,
    probe_session_rows,
    session_for_hour,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2024-02-01T00:00:00",
        "forward_bars": 2,
        "min_bars_total": 3,
        "min_bars_per_window": 2,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _row(index: int, hour: int, close: float, low: float | None = None) -> dict[str, object]:
    return {
        "time": datetime(2024, 1, 1, hour, 0, 0) + timedelta(hours=index),
        "close_price": close,
        "low_price": close * 0.99 if low is None else low,
        "high_price": close * 1.01,
        "volume": 1000.0,
        "atr_pct": 0.01,
    }


def test_session_for_hour_disjoint_windows() -> None:
    assert session_for_hour(0, DEFAULT_WINDOWS) == "asia"
    assert session_for_hour(7, DEFAULT_WINDOWS) == "asia"
    assert session_for_hour(8, DEFAULT_WINDOWS) == "europe"
    assert session_for_hour(15, DEFAULT_WINDOWS) == "europe"
    assert session_for_hour(16, DEFAULT_WINDOWS) == "americas"
    assert session_for_hour(23, DEFAULT_WINDOWS) == "americas"


def test_coerce_datetime_accepts_iso_string() -> None:
    assert _coerce_datetime("2024-01-01T00:00:00") == datetime(2024, 1, 1)


def test_probe_detects_window_beating_baseline() -> None:
    rows = []
    for index in range(20):
        hour = 2 if index < 10 else 14
        close = 100.0 + index * (0.5 if hour == 2 else -0.2)
        rows.append(_row(index, hour, close, low=close - 0.1))
    summary = probe_session_rows(rows, _config(forward_bars=2, min_bars_per_window=3))
    assert summary.eligible_bars >= 3
    asia = next(s for s in summary.windows if s.window == "asia")
    assert asia.sample_count >= 3


def test_evaluate_pulse_has_pulse_when_best_window_set() -> None:
    from scripts.probe_session_liquidity_router import ProbeSummary, WindowStats

    baseline = WindowStats("baseline", 10, 0.0, 0.0, 1.0, 50.0)
    windows = (
        WindowStats("asia", 5, -1.0, -1.0, 2.0, 40.0),
        WindowStats("europe", 6, 0.5, 0.4, 0.5, 55.0),
    )
    summary = ProbeSummary("BTCUSDT", "1h", 10, baseline, windows, "europe")
    assert evaluate_pulse(summary, _config(min_bars_total=5, min_bars_per_window=5)) == "HAS_PULSE"
