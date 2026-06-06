"""Unit tests for range-break continuation probe helpers (no DB/network).

Mirrors test_liquidity_sweep_probe.py structure but for the *continuation* (close outside) definition.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.probe_range_break_continuation import (
    EventSide,
    HorizonStats,
    LiquidityBar,
    ProbeConfig,
    SideResult,
    SweepEvent,
    compute_baseline_long_mae,
    compute_baseline_short_mae,
    detect_downside_break_continuations,
    detect_upside_break_continuations,
    evaluate_side,
    forward_passes,
    mae_passes,
    probe_symbol,
    symbols_contradict,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbols": ("BTCUSDT",),
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2026-06-01T00:00:00",
        "lookback_bars": 5,
        "range_expansion_mult": 1.2,
        "volume_expansion_mult": 1.2,
        "forward_bars_6h": 2,
        "forward_bars_12h": 3,
        "forward_bars_24h": 4,
        "min_events_per_symbol": 1,
        "min_events_pooled": 3,
        "min_mean_forward_pct": 0.15,
        "round_trip_fee_pct": 0.08,
        "min_mae_improvement_pct": 10.0,
        "max_concentration_pct": 50.0,
        "max_month_share_pct": 40.0,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _flat_bars(count: int) -> list[LiquidityBar]:
    rows: list[LiquidityBar] = []
    for index in range(count):
        rows.append(
            LiquidityBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index),
                open_price=100.0,
                high_price=100.0,
                low_price=99.0,
                close_price=99.5,
                volume=1000.0,
            )
        )
    return rows


def _upside_break_continuation_series() -> list[LiquidityBar]:
    """Sweep high, *closes above* prior max, then continues up (for long continuation test)."""
    bars = _flat_bars(30)
    sweep_index = 10
    bars[sweep_index] = LiquidityBar(
        time=bars[sweep_index].time,
        open_price=99.8,
        high_price=101.5,
        low_price=99.0,
        close_price=101.2,  # closes *outside* (above) prior high
        volume=2500.0,
    )
    for index in range(sweep_index + 1, len(bars)):
        close = 101.2 + (index - sweep_index) * 0.3  # continuation up
        bars[index] = LiquidityBar(
            time=bars[index].time,
            open_price=close - 0.1,
            high_price=close + 0.2,
            low_price=close - 0.2,
            close_price=close,
            volume=1000.0,
        )
    return bars


def _downside_break_continuation_series() -> list[LiquidityBar]:
    """Sweep low, *closes below* prior min, then continues down (for short continuation test)."""
    bars = _flat_bars(30)
    sweep_index = 12
    bars[sweep_index] = LiquidityBar(
        time=bars[sweep_index].time,
        open_price=100.2,
        high_price=100.8,
        low_price=98.0,
        close_price=98.7,  # closes *outside* (below) prior low
        volume=2500.0,
    )
    for index in range(sweep_index + 1, len(bars)):
        close = 98.7 - (index - sweep_index) * 0.25  # continuation down
        bars[index] = LiquidityBar(
            time=bars[index].time,
            open_price=close + 0.1,
            high_price=close + 0.2,
            low_price=close - 0.2,
            close_price=close,
            volume=1000.0,
        )
    return bars


def test_detect_upside_break_continuations() -> None:
    bars = _upside_break_continuation_series()
    events = detect_upside_break_continuations(bars, config=_config())
    assert len(events) >= 1
    assert all(event.side is EventSide.LONG for event in events)
    # Continuation up after break => positive forward in test data
    assert all(event.forward_12h_pct > 0 for event in events)


def test_detect_downside_break_continuations() -> None:
    bars = _downside_break_continuation_series()
    events = detect_downside_break_continuations(bars, config=_config())
    assert len(events) >= 1
    assert all(event.side is EventSide.SHORT for event in events)
    assert all(event.forward_12h_pct > 0 for event in events)


def test_forward_passes_requires_positive_net_edge() -> None:
    good = HorizonStats(6, 5, 0.30, 0.22, 0.25, 1.0, 2.0, 50.0, 1.5, 10.0, 20.0)
    weak = HorizonStats(12, 5, 0.05, -0.03, 0.04, 1.0, 2.0, 0.0, 1.0, 10.0, 20.0)
    empty = HorizonStats(24, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert forward_passes(good, good, empty, 0.15) is True
    assert forward_passes(weak, weak, weak, 0.15) is False


def test_mae_passes_requires_improvement() -> None:
    improved = HorizonStats(12, 5, 0.0, 0.0, 0.0, 1.0, 2.0, 50.0, 1.0, 10.0, 20.0)
    flat = HorizonStats(12, 5, 0.0, 0.0, 0.0, 2.0, 2.0, 0.0, 1.0, 10.0, 20.0)
    assert mae_passes(improved, flat, flat, 10.0) is True
    assert mae_passes(flat, flat, flat, 10.0) is False


def test_symbols_contradict_detects_opposing_signs() -> None:
    def _side(symbol: str, fwd: float) -> SideResult:
        event = SweepEvent(
            time=datetime(2024, 1, 1, tzinfo=UTC),
            side=EventSide.SHORT,
            close_price=100.0,
            forward_6h_pct=fwd,
            forward_12h_pct=fwd,
            forward_24h_pct=fwd,
            mae_6h_pct=1.0,
            mae_12h_pct=1.0,
            mae_24h_pct=1.0,
            mfe_6h_pct=1.0,
            mfe_12h_pct=1.0,
            mfe_24h_pct=1.0,
        )
        return SideResult(
            symbol,
            EventSide.SHORT,
            (event,) * 25,
            2.0,
            2.0,
            2.0,
        )

    assert symbols_contradict(
        [_side("BTCUSDT", 0.5), _side("ETHUSDT", -0.5)],
        min_events=20,
        threshold=0.15,
    )


def test_probe_symbol_builds_both_sides() -> None:
    bars = _upside_break_continuation_series()
    summary = probe_symbol(bars, "BTCUSDT", _config())
    sides = {side.side for side in summary.sides}
    assert EventSide.LONG in sides
    assert EventSide.SHORT in sides
    assert compute_baseline_long_mae(bars, 3) > 0
    assert compute_baseline_short_mae(bars, 3) > 0


def test_evaluate_side_requires_forward_and_mae() -> None:
    events = tuple(
        SweepEvent(
            time=datetime(2024, month, 1, tzinfo=UTC),
            side=EventSide.LONG,
            close_price=100.0,
            forward_6h_pct=0.5,
            forward_12h_pct=0.5,
            forward_24h_pct=0.5,
            mae_6h_pct=0.5,
            mae_12h_pct=0.5,
            mae_24h_pct=0.5,
            mfe_6h_pct=1.0,
            mfe_12h_pct=1.0,
            mfe_24h_pct=1.0,
        )
        for month in range(1, 13)
        for _ in range(3)
    )
    strong = SideResult("BTCUSDT", EventSide.LONG, events, 2.0, 2.0, 2.0)
    weak_mae = SideResult("BTCUSDT", EventSide.LONG, events, 0.5, 0.5, 0.5)
    assert evaluate_side(strong, config=_config(min_events_per_symbol=20)) is True
    assert evaluate_side(weak_mae, config=_config(min_events_per_symbol=20)) is False
