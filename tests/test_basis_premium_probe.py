"""Unit tests for basis/premium probe helpers (no DB/network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.probe_basis_premium import (
    HorizonStats,
    PremiumBar,
    ProbeConfig,
    ScenarioKind,
    compute_baseline_mae,
    detect_extreme_events,
    detect_normalization_events,
    forward_passes,
    mae_passes,
    probe_symbol,
    tail_threshold_high,
    tail_threshold_low,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbols": ("BTCUSDT",),
        "timeframe": "1h",
        "exchange": "binance_usdm",
        "start": "2024-01-01T00:00:00",
        "end": "2026-06-01T00:00:00",
        "tail_pcts": (5, 10),
        "forward_bars_12h": 2,
        "forward_bars_24h": 4,
        "min_events_per_symbol": 2,
        "min_events_pooled": 5,
        "min_mean_forward_pct": 0.15,
        "min_mae_improvement_pct": 10.0,
        "max_concentration_pct": 50.0,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _bars(count: int, basis_step: float = 1.0, price_step: float = 1.0) -> list[PremiumBar]:
    rows: list[PremiumBar] = []
    for index in range(count):
        rows.append(
            PremiumBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index),
                basis_bps=index * basis_step,
                premium_index=index * 0.0001,
                close_price=100.0 + index * price_step,
                low_price=99.0 + index * price_step,
            )
        )
    return rows


def test_tail_thresholds_select_extremes() -> None:
    values = list(range(100))
    assert tail_threshold_high(values, 5) >= 94
    assert tail_threshold_low(values, 5) <= 5


def test_detect_extreme_positive_events() -> None:
    bars = _bars(80, basis_step=2.0)
    events = detect_extreme_events(
        bars,
        metric="basis_bps",
        tail_pct=10,
        kind=ScenarioKind.EXTREME_POSITIVE,
        config=_config(forward_bars_12h=2, forward_bars_24h=4),
    )
    assert len(events) >= 4
    assert all(event.side == "positive" for event in events)


def test_detect_normalization_from_positive_tail() -> None:
    rows: list[PremiumBar] = []
    basis_values = [50.0] * 5 + [5.0] * 5 + [50.0] * 5 + [5.0] * 5
    for index, basis in enumerate(basis_values):
        rows.append(
            PremiumBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index),
                basis_bps=basis,
                premium_index=0.001,
                close_price=100.0 + index,
                low_price=99.0 + index,
            )
        )
    events = detect_normalization_events(
        rows,
        metric="basis_bps",
        tail_pct=10,
        config=_config(forward_bars_12h=2, forward_bars_24h=3),
    )
    assert len(events) >= 1


def test_forward_passes_requires_consistent_sign() -> None:
    stats_pos_12 = HorizonStats(12, 10, 0.20, 0.20, 1.0, 1.0, 0.0, 10.0)
    stats_neg_24 = HorizonStats(24, 10, -0.20, -0.20, 1.0, 1.0, 0.0, 10.0)
    assert forward_passes(stats_pos_12, stats_neg_24, 0.15) is False
    assert forward_passes(
        stats_pos_12,
        HorizonStats(24, 10, 0.18, 0.18, 1.0, 1.0, 0.0, 10.0),
        0.15,
    )


def test_evaluate_scenario_accepts_mae_improvement() -> None:
    bars = _bars(80, basis_step=3.0, price_step=0.5)
    summary = probe_symbol(
        bars,
        "BTCUSDT",
        _config(min_events_per_symbol=2, forward_bars_12h=2, forward_bars_24h=4),
    )
    scenario = next(
        s
        for s in summary.scenarios
        if s.kind == ScenarioKind.EXTREME_POSITIVE and s.metric == "basis_bps" and s.tail_pct == 10
    )
    assert len(scenario.events) >= 2
    assert compute_baseline_mae(bars, 2) > 0
    stats_12 = scenario.stats_for("12h", scenario.baseline_mae_12h)
    assert mae_passes(stats_12, stats_12, 10.0) or forward_passes(stats_12, stats_12, 0.15)
