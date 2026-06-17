from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.probe_macro_event_drift import (
    HorizonMetrics,
    MacroEvent,
    ProbeConfig,
    _bar_overlaps_release,
    _directional_consistency,
    audit_macro_data,
    decide_verdict,
    entry_bar_index,
    evaluate_symbol_events,
    forward_return_pct,
    load_frozen_events,
)
from scripts.probe_macro_event_drift import (
    HourlyBar as ProbeHourlyBar,
)
from scripts.probe_macro_event_drift import (
    SymbolProbeResult as ProbeSymbolResult,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbols": ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2026-06-01T00:00:00",
        "events_csv": Path("data/macro_events/us_macro_releases.csv"),
        "horizons_hours": (6, 24, 72),
        "baseline_vol_bars": 72,
        "event_exclusion_hours": 48,
        "one_way_fee_pct": 0.04,
        "min_usable_events": 50,
        "directional_consistency_pct": 60.0,
        "fee_noise_bar_pct": 0.3,
        "min_symbols_h1_pass": 2,
        "random_baseline_seed": 42,
    }
    values.update(overrides)
    return ProbeConfig(**values)


def _bar(hour: int, close: float, *, day: int = 1) -> ProbeHourlyBar:
    return ProbeHourlyBar(
        time=datetime(2024, 1, day, hour, 0, 0, tzinfo=UTC),
        close_price=close,
    )


def test_frozen_event_set_meets_data_gate() -> None:
    events = load_frozen_events(Path("data/macro_events/us_macro_releases.csv"))
    audit = audit_macro_data(events, min_usable_events=50)
    assert audit.total_events >= 50
    assert not audit.blocked
    assert set(audit.events_by_type) == {"FOMC", "CPI", "NFP"}


def test_bar_overlap_detects_release_inside_candle() -> None:
    release = datetime(2024, 1, 11, 13, 30, 0, tzinfo=UTC)
    bar_time = datetime(2024, 1, 11, 13, 0, 0, tzinfo=UTC)
    assert _bar_overlaps_release(bar_time, release) is True
    assert _bar_overlaps_release(datetime(2024, 1, 11, 14, 0, 0, tzinfo=UTC), release) is False


def test_entry_bar_index_skips_overlapping_and_look_ahead() -> None:
    release = datetime(2024, 1, 11, 13, 30, 0, tzinfo=UTC)
    bars = [_bar(h, 100.0 + h, day=11) for h in range(12, 20)]
    entry = entry_bar_index(bars, release)
    assert entry == 2
    assert bars[entry].time == datetime(2024, 1, 11, 14, 0, 0, tzinfo=UTC)


def test_forward_return_uses_horizon_bars_after_entry() -> None:
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]
    # entry close 101 → exit close 104 (+3 bars) = +2.97%
    assert forward_return_pct(closes, entry_idx=1, horizon_bars=3) == pytest.approx(
        2.9703, rel=1e-3
    )


def test_event_window_beats_random_baseline_on_synthetic_drift() -> None:
    """Inject post-release drift; event mean should exceed matched baseline."""
    bars: list[ProbeHourlyBar] = []
    price = 100.0
    for hour in range(200):
        price += 0.01
        bars.append(
            ProbeHourlyBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=hour),
                close_price=price,
            )
        )

    release = datetime(2024, 1, 3, 13, 30, 0, tzinfo=UTC)
    entry = entry_bar_index(bars, release)
    assert entry is not None
    for offset in range(1, 7):
        idx = entry + offset
        bars[idx] = ProbeHourlyBar(
            time=bars[idx].time,
            close_price=bars[idx].close_price + 0.5,
        )

    events = (
        MacroEvent("CPI", "2024-01-03", release, "bls.gov"),
        MacroEvent("NFP", "2024-01-10", datetime(2024, 1, 10, 13, 30, 0, tzinfo=UTC), "bls.gov"),
    )
    result = evaluate_symbol_events(
        bars,
        events,
        _config(horizons_hours=(6,), baseline_vol_bars=24, min_usable_events=1),
        symbol="BTCUSDT",
    )
    metrics = result.horizons[0]
    assert metrics.event_count >= 1
    assert metrics.excess_vs_baseline_pct > 0.0


def test_decide_verdict_has_pulse_when_two_symbols_pass_h1() -> None:
    def _metrics(h1: bool, h2: bool) -> HorizonMetrics:
        return HorizonMetrics(
            horizon_hours=6,
            event_count=10,
            mean_return_pct=1.0,
            median_return_pct=1.0,
            directional_consistency_pct=70.0,
            dominant_sign="positive",
            baseline_mean_return_pct=0.0,
            excess_vs_baseline_pct=1.0,
            mean_event_vol=1.0,
            mean_baseline_vol=0.5,
            vol_ratio=2.0,
            vol_elevated_fraction=0.8,
            h1_pass=h1,
            h2_pass=h2,
        )

    results = (
        ProbeSymbolResult("BTCUSDT", 10, (_metrics(True, False),)),
        ProbeSymbolResult("ETHUSDT", 10, (_metrics(True, False),)),
        ProbeSymbolResult("SOLUSDT", 10, (_metrics(False, False),)),
    )
    status, verdict, _ = decide_verdict(results, data_blocked=False, config=_config())
    assert status == "OK"
    assert verdict == "HAS_PULSE"


def test_decide_verdict_weak_edge_when_only_h2() -> None:
    def _metrics(h1: bool, h2: bool) -> HorizonMetrics:
        return HorizonMetrics(
            horizon_hours=6,
            event_count=10,
            mean_return_pct=0.0,
            median_return_pct=0.0,
            directional_consistency_pct=50.0,
            dominant_sign="positive",
            baseline_mean_return_pct=0.0,
            excess_vs_baseline_pct=0.0,
            mean_event_vol=2.0,
            mean_baseline_vol=1.0,
            vol_ratio=2.0,
            vol_elevated_fraction=0.8,
            h1_pass=h1,
            h2_pass=h2,
        )

    results = (
        ProbeSymbolResult("BTCUSDT", 10, (_metrics(False, True),)),
        ProbeSymbolResult("ETHUSDT", 10, (_metrics(False, True),)),
        ProbeSymbolResult("SOLUSDT", 10, (_metrics(False, False),)),
    )
    _, verdict, _ = decide_verdict(results, data_blocked=False, config=_config())
    assert verdict == "WEAK_EDGE"


def test_directional_consistency_majority_sign() -> None:
    consistency, sign = _directional_consistency([1.0, 0.5, -0.1, 0.2, 0.3])
    assert sign == "positive"
    assert consistency == 80.0
