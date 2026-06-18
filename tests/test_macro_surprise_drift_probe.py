from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.probe_macro_event_drift import (
    HourlyBar,
    MacroEvent,
    ProbeConfig,
    entry_bar_index,
    load_frozen_events,
)
from scripts.probe_macro_surprise_drift import (
    EXPECTED_RETURN_SIGN_WHEN_HOT,
    MacroSurprise,
    _rank_correlation,
    audit_surprise_data,
    decide_surprise_verdict,
    evaluate_series_symbol,
    join_events_to_surprises,
    load_surprises,
)


def _config(**overrides: object) -> ProbeConfig:
    values = {
        "symbols": ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        "timeframe": "1h",
        "start": "2024-01-01T00:00:00",
        "end": "2026-06-01T00:00:00",
        "events_csv": Path("data/macro_events/us_macro_releases.csv"),
        "horizons_hours": (6, 24),
        "baseline_vol_bars": 24,
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


def test_surprises_csv_joins_on_release_ts() -> None:
    events = load_frozen_events(Path("data/macro_events/us_macro_releases.csv"))
    surprises = load_surprises(Path("data/macro_events/us_macro_surprises.csv"))
    joined = join_events_to_surprises(events, surprises)
    assert len(joined) == len(surprises)
    for event, surprise in joined:
        assert event.event_type == surprise.event_type
        assert event.release_ts == surprise.release_ts


def test_standardized_z_matches_surprise_over_sample_stdev() -> None:
    surprises = load_surprises(Path("data/macro_events/us_macro_surprises.csv"))
    for event_type in ("CPI", "NFP"):
        series = [item for item in surprises if item.event_type == event_type]
        assert len(series) >= 17
        raw = [item.surprise for item in series]
        stdev = (sum((value - sum(raw) / len(raw)) ** 2 for value in raw) / len(raw)) ** 0.5
        assert stdev > 0
        for item in series:
            assert item.z == pytest.approx(item.surprise / stdev, rel=1e-3)


def test_data_audit_passes_with_committed_coverage() -> None:
    events = load_frozen_events(Path("data/macro_events/us_macro_releases.csv"))
    cpi_nfp = [event for event in events if event.event_type in ("CPI", "NFP")]
    surprises = load_surprises(Path("data/macro_events/us_macro_surprises.csv"))
    audit = audit_surprise_data(cpi_nfp, surprises)
    assert not audit.blocked
    assert audit.rows_with_consensus >= 55
    assert audit.missing_consensus == 1
    assert "Wayback" in audit.point_in_time_caveat or "Investing.com" in audit.point_in_time_caveat


def test_ex_ante_sign_is_frozen_negative_for_hot_cpi_and_nfp() -> None:
    assert EXPECTED_RETURN_SIGN_WHEN_HOT["CPI"] == -1
    assert EXPECTED_RETURN_SIGN_WHEN_HOT["NFP"] == -1


def test_point_in_time_entry_reused_from_calendar_probe() -> None:
    release = datetime(2024, 1, 11, 13, 30, 0, tzinfo=UTC)
    bars = [
        HourlyBar(datetime(2024, 1, 11, 13, 0, 0, tzinfo=UTC), 100.0),
        HourlyBar(datetime(2024, 1, 11, 14, 0, 0, tzinfo=UTC), 101.0),
    ]
    assert entry_bar_index(bars, release) == 1


def test_hot_cold_bucketing_oriented_spread_passes_on_synthetic_edge() -> None:
    bars: list[HourlyBar] = []
    price = 100.0
    for hour in range(1200):
        price += 0.01
        bars.append(
            HourlyBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=hour),
                close_price=price,
            )
        )

    def _event(day: int, etype: str, z: float) -> tuple[MacroEvent, MacroSurprise]:
        release = datetime(2024, 2, day, 13, 30, 0, tzinfo=UTC)
        event = MacroEvent(etype, f"2024-02-{day:02d}", release, "bls.gov")
        surprise = MacroSurprise(
            event_type=etype,
            release_date_et=f"2024-02-{day:02d}",
            release_ts=release,
            metric="headline_cpi_mom_pct",
            actual=0.4 if z > 0 else 0.1,
            consensus=0.2,
            surprise=0.2 if z > 0 else -0.1,
            z=z,
            consensus_source="test",
            actual_source="test",
            consensus_note="",
        )
        return event, surprise

    joined = (
        _event(2, "CPI", 1.5),
        _event(5, "CPI", 1.2),
        _event(8, "CPI", -1.1),
        _event(12, "CPI", -0.8),
    )
    all_events = tuple(event for event, _ in joined)

    for event, surprise in joined:
        entry = entry_bar_index(bars, event.release_ts)
        assert entry is not None
        bump = -2.0 if surprise.z > 0 else 2.0
        for offset in range(1, 7):
            idx = entry + offset
            bars[idx] = HourlyBar(
                time=bars[idx].time,
                close_price=bars[idx].close_price + bump,
            )

    result = evaluate_series_symbol(
        bars,
        joined,
        all_events,
        _config(horizons_hours=(6,), fee_noise_bar_pct=0.1),
        event_type="CPI",
        symbol="BTCUSDT",
    )
    metrics = result.horizons[0]
    assert metrics.hot_count >= 2
    assert metrics.cold_count >= 2
    assert metrics.hot_mean_return_pct < 0
    assert metrics.cold_mean_return_pct > 0
    assert metrics.h1_pass is True


def test_wrong_signed_edge_fails_ex_ante_gate() -> None:
    """Hot bucket positive returns must not pass when ex-ante sign expects down."""
    bars: list[HourlyBar] = []
    price = 100.0
    for hour in range(200):
        price += 0.01
        bars.append(
            HourlyBar(
                time=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=hour),
                close_price=price,
            )
        )

    release = datetime(2024, 1, 5, 13, 30, 0, tzinfo=UTC)
    event = MacroEvent("CPI", "2024-01-05", release, "bls.gov")
    surprise = MacroSurprise(
        event_type="CPI",
        release_date_et="2024-01-05",
        release_ts=release,
        metric="headline_cpi_mom_pct",
        actual=0.5,
        consensus=0.2,
        surprise=0.3,
        z=2.0,
        consensus_source="test",
        actual_source="test",
        consensus_note="",
    )
    entry = entry_bar_index(bars, release)
    assert entry is not None
    for offset in range(1, 7):
        idx = entry + offset
        bars[idx] = HourlyBar(
            time=bars[idx].time,
            close_price=bars[idx].close_price + 1.0,
        )

    result = evaluate_series_symbol(
        bars,
        ((event, surprise),),
        (event,),
        _config(horizons_hours=(6,)),
        event_type="CPI",
        symbol="BTCUSDT",
    )
    assert result.horizons[0].h1_pass is False


def test_rank_correlation_positive_for_monotone_series() -> None:
    xs = [-2.0, -1.0, 0.0, 1.0, 2.0]
    ys = [-2.0, -1.0, 0.0, 1.0, 2.0]
    assert _rank_correlation(xs, ys) == pytest.approx(1.0)


def test_decide_verdict_blocked_on_data() -> None:
    status, verdict, reasons = decide_surprise_verdict((), data_blocked=True)
    assert status == "BLOCKED_ON_DATA"
    assert verdict == "BLOCKED_ON_DATA"
    assert reasons
