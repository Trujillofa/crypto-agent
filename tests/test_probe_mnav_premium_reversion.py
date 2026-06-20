"""Tests for the mNAV premium-reversion probe (no network)."""

import math
from datetime import date

import pytest

from scripts.probe_mnav_premium_reversion import (
    DisclosureRow,
    NameAudit,
    NameEdgeResult,
    ProbeConfig,
    analyze_mean_reversion,
    audit_data,
    build_mnav_series,
    compute_mnav,
    decide_verdict,
    detect_extreme_events,
    forward_fill_disclosures,
    normalize_shares_outstanding,
)

T0 = date(2024, 1, 1)


def _row(
    as_of: str,
    holdings: float,
    shares: float,
    *,
    ticker: str = "TEST",
    crypto: str = "BTC",
    symbol: str = "BTCUSDT",
) -> DisclosureRow:
    return DisclosureRow(
        ticker=ticker,
        crypto_symbol=crypto,
        binance_symbol=symbol,
        as_of_date=date.fromisoformat(as_of),
        holdings_units=holdings,
        shares_outstanding=shares,
        source="fixture",
    )


def _days(n: int) -> list[date]:
    from datetime import timedelta

    return [T0 + timedelta(days=i) for i in range(n)]


def _gate_config(**overrides) -> ProbeConfig:
    defaults = {
        "tickers": (),
        "start": "2024-01-01",
        "end": "2026-06-01",
        "trailing_window_days": 20,
        "extreme_pct": 10,
        "horizons": (5, 10),
        "min_names": 4,
        "min_names_edge": 3,
        "min_trading_days": 60,
        "random_baseline_samples": 50,
        "seed_csv": None,  # type: ignore[arg-type]
        "rng_seed": 7,
    }
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def test_forward_fill_is_point_in_time_not_look_ahead():
    """Today's disclosure must never apply to dates before as_of_date."""
    disclosures = [
        _row("2024-01-01", holdings=100, shares=1_000_000),
        _row("2024-06-01", holdings=200, shares=2_000_000),
    ]
    trading_days = _days(200)
    filled = forward_fill_disclosures(disclosures, trading_days)

    assert (T0, (100, 1_000_000)) == (trading_days[0], filled[trading_days[0]])
    assert filled[date(2024, 5, 31)] == (100, 1_000_000)
    assert filled[date(2024, 6, 1)] == (200, 2_000_000)
    assert filled[date(2024, 7, 1)] == (200, 2_000_000)
    assert date(2023, 12, 31) not in filled


def test_mstr_pre_split_shares_scaled_for_split_adjusted_prices():
    assert normalize_shares_outstanding("MSTR", date(2024, 7, 31), 19_067_000) == 190_670_000
    assert normalize_shares_outstanding("MSTR", date(2024, 8, 8), 190_670_000) == 190_670_000
    assert normalize_shares_outstanding("3350.T", date(2024, 7, 31), 181_692_180) == 181_692_180


def test_compute_mnav_matches_hand_calculation():
    # market_cap = 1_000_000 * 10 = 10_000_000; crypto_nav = 100 * 50_000 = 5_000_000
    assert compute_mnav(10.0, 50_000.0, 100.0, 1_000_000.0) == pytest.approx(2.0)


def test_detect_extreme_percentile_events():
    values = [1.0 + 0.05 * math.sin(i * 0.5) for i in range(60)]
    values[40] = 3.0  # spike in trailing window
    series = [(T0, value) for value in values]
    top, bottom, eligible = detect_extreme_events(
        series, trailing_window=20, extreme_pct=10, horizon=5
    )
    assert 40 in top
    assert 0 < (len(top) + len(bottom)) / eligible <= 0.30


def test_analyze_has_pulse_on_synthetic_mean_reversion():
    """Isolated top-extreme spikes that revert should clear significance + concentration gates."""
    values = [1.0] * 120
    for spike_idx in (40, 55, 70, 85):
        values[spike_idx] = 3.0
        for offset in range(1, 6):
            values[spike_idx + offset] = 3.0 - 0.5 * offset
    series = [(T0, value) for value in values]
    results = analyze_mean_reversion(
        series, _gate_config(trailing_window_days=20, horizons=(5,), rng_seed=0)
    )
    assert results[0].h1_pass is True
    assert results[0].edge_vs_baseline >= 0.02
    assert results[0].p_value < 0.05
    assert results[0].concentration_ok is True


def test_analyze_no_pulse_on_flat_series():
    series = [(T0, 1.5) for _ in range(80)]
    results = analyze_mean_reversion(series, _gate_config(trailing_window_days=20, horizons=(5,)))
    assert results[0].h1_pass is False


def _name_audit(*, usable: bool, ticker: str = "T") -> NameAudit:
    return NameAudit(
        ticker=ticker,
        rows=100 if usable else 10,
        span_days=200.0 if usable else 30.0,
        equity_source="fixture",
        max_disclosure_gap_days=30,
        stale_disclosure=False,
        usable=usable,
    )


def _edge_result(*, ticker: str, h1_pass: bool) -> NameEdgeResult:
    return NameEdgeResult(ticker=ticker, trading_days=100, horizon_results=(), h1_pass=h1_pass)


def test_decide_verdict_routing():
    audit_blocked = audit_data(
        [_name_audit(usable=False) for _ in range(3)],
        _gate_config(min_names=4),
    )
    assert decide_verdict(audit_blocked, (), _gate_config())[1] == "BLOCKED_ON_DATA"

    audit_ok = audit_data(
        [_name_audit(usable=True, ticker=f"T{i}") for i in range(4)],
        _gate_config(min_names=4),
    )
    one = _edge_result(ticker="ONLY", h1_pass=True)
    assert decide_verdict(audit_ok, [one], _gate_config(min_names_edge=3))[1] == "WEAK_EDGE"

    three = [_edge_result(ticker=f"T{i}", h1_pass=True) for i in range(3)]
    assert decide_verdict(audit_ok, three, _gate_config(min_names_edge=3))[1] == "HAS_PULSE"

    none = _edge_result(ticker="X", h1_pass=False)
    assert decide_verdict(audit_ok, [none], _gate_config())[1] == "NO_PULSE"


def test_build_mnav_series_joins_prices_and_disclosures():
    days = _days(5)
    equity = dict.fromkeys(days, 10.0)
    crypto = dict.fromkeys(days, 50000.0)
    filled = forward_fill_disclosures([_row("2024-01-01", 100, 1_000_000)], days)
    series = build_mnav_series(days, equity, crypto, filled)
    assert len(series) == 5
    assert series[0][1] == pytest.approx(2.0)
