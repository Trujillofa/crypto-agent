"""Unit tests for short crowding probe helpers (no DB/network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scripts.probe_short_crowding import (
    CrowdingBar,
    ProbeConfig,
    ScenarioKind,
    compute_baseline_short_mae,
    detect_combined_crowded_events,
    detect_positive_funding_tail_events,
    detect_positive_premium_tail_events,
    forward_passes_short,
    probe_symbol,
    symbols_contradict,
    tail_threshold_high,
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
        "round_trip_fee_pct": 0.08,
        "min_mae_improvement_pct": 10.0,
        "max_concentration_pct": 50.0,
        "max_month_share_pct": 40.0,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _bars(count: int, *, basis_step: float = 1.0, price_step: float = -0.5) -> list[CrowdingBar]:
    rows: list[CrowdingBar] = []
    for index in range(count):
        rows.append(
            CrowdingBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index),
                basis_bps=index * basis_step,
                premium_index=index * 0.0001,
                funding_rate=index * 0.00001,
                close_price=100.0 + index * price_step,
                high_price=100.5 + index * price_step,
                low_price=99.5 + index * price_step,
            )
        )
    return rows


def test_short_forward_profits_when_price_falls() -> None:
    bars = _bars(50, basis_step=2.0, price_step=-0.4)
    events = detect_positive_premium_tail_events(
        bars,
        metric="basis_bps",
        tail_pct=10,
        config=_config(forward_bars_12h=2, forward_bars_24h=4),
    )
    assert len(events) >= 1
    assert all(event.forward_12h_pct > 0 for event in events)


def test_detect_positive_funding_tail_events() -> None:
    bars = _bars(50, basis_step=0.1, price_step=0.0)
    events = detect_positive_funding_tail_events(bars, tail_pct=10, config=_config())
    assert len(events) >= 1


def test_detect_combined_crowded_events() -> None:
    bars = _bars(60)
    events = detect_combined_crowded_events(
        bars,
        premium_metric="basis_bps",
        tail_pct=10,
        config=_config(),
    )
    assert len(events) >= 2


def test_forward_passes_short_requires_positive_net_edge() -> None:
    from scripts.probe_short_crowding import HorizonStats

    good = HorizonStats(12, 5, 0.30, 0.22, 0.25, 1.0, 2.0, 50.0, 10.0, 20.0)
    weak = HorizonStats(24, 5, 0.05, -0.03, 0.04, 1.0, 2.0, 0.0, 10.0, 20.0)
    assert forward_passes_short(good, good, 0.15) is True
    assert forward_passes_short(weak, weak, 0.15) is False


def test_symbols_contradict_detects_opposing_signs() -> None:
    from scripts.probe_short_crowding import ScenarioResult, ShortEvent

    def _scenario(symbol: str, fwd: float) -> ScenarioResult:
        event = ShortEvent(
            time=datetime(2024, 1, 1, tzinfo=UTC),
            kind=ScenarioKind.POSITIVE_PREMIUM_TAIL,
            metric="basis_bps",
            tail_pct=5,
            basis_bps=10.0,
            funding_rate=0.001,
            forward_12h_pct=fwd,
            forward_24h_pct=fwd,
            mae_12h_pct=1.0,
            mae_24h_pct=1.0,
        )
        return ScenarioResult(
            symbol, ScenarioKind.POSITIVE_PREMIUM_TAIL, "basis_bps", 5, (event,) * 25, 2.0, 2.0
        )

    assert symbols_contradict(
        [_scenario("BTCUSDT", 0.5), _scenario("ETHUSDT", -0.5)],
        min_events=20,
        threshold=0.15,
    )


def test_probe_symbol_builds_all_scenario_kinds() -> None:
    summary = probe_symbol(_bars(80), "BTCUSDT", _config())
    kinds = {scenario.kind for scenario in summary.scenarios}
    assert ScenarioKind.POSITIVE_PREMIUM_TAIL in kinds
    assert ScenarioKind.POSITIVE_FUNDING_TAIL in kinds
    assert ScenarioKind.COMBINED_CROWDED in kinds
    assert ScenarioKind.NORMALIZATION_FROM_POSITIVE in kinds
    rising_highs = [
        CrowdingBar(
            time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=index),
            basis_bps=float(index),
            premium_index=0.0,
            funding_rate=0.0001,
            close_price=100.0,
            high_price=100.0 + (index % 3),
            low_price=99.0,
        )
        for index in range(20)
    ]
    assert compute_baseline_short_mae(rising_highs, 2) > 0
    assert tail_threshold_high(list(range(100)), 5) >= 94
