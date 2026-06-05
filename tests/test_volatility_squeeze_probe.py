from __future__ import annotations

from datetime import datetime, timedelta

from scripts.probe_volatility_squeeze_breakout import (
    ProbeConfig,
    evaluate_pulse,
    probe_squeeze_rows,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2024-02-01T00:00:00",
        "squeeze_lookback": 10,
        "squeeze_percentile": 0.30,
        "momentum_period": 3,
        "min_atr_pct": 0.001,
        "forward_bars_12h": 2,
        "forward_bars_24h": 4,
        "forward_bars_48h": 6,
        "min_events_for_pulse": 2,
        "max_profit_concentration_pct": 30.0,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _row(
    index: int,
    close: float,
    *,
    bb_upper_dist: float = 0.01,
    bb_lower_dist: float = 0.01,
    atr_pct: float = 0.01,
    sma_20: float | None = None,
) -> dict[str, object]:
    return {
        "time": datetime(2024, 1, 1) + timedelta(hours=index),
        "close_price": close,
        "bb_upper_dist": bb_upper_dist,
        "bb_lower_dist": bb_lower_dist,
        "atr_pct": atr_pct,
        "sma_20": close * 0.99 if sma_20 is None else sma_20,
    }


def test_probe_counts_squeeze_breakout_with_cooldown() -> None:
    rows = []
    for index in range(15):
        rows.append(_row(index, 100.0 + index * 0.1, bb_upper_dist=0.02, bb_lower_dist=0.02))
    rows.append(_row(20, 102.0, bb_upper_dist=0.001, bb_lower_dist=0.001, atr_pct=0.02))
    for index in range(21, 55):
        rows.append(_row(index, 102.0 + (index - 20) * 0.5, bb_upper_dist=0.02, bb_lower_dist=0.02))
    summary = probe_squeeze_rows(
        rows,
        _config(squeeze_lookback=10, squeeze_percentile=0.25, momentum_period=3),
    )
    assert len(summary.events) >= 1


def test_evaluate_pulse_sparse_when_few_events() -> None:
    summary = probe_squeeze_rows([], _config(min_events_for_pulse=5))
    assert evaluate_pulse(summary, _config(min_events_for_pulse=5)) == "NO_PULSE"
