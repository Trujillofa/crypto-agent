"""Tests for the cross-venue basis probe.

Covers --smoke / --verdict-output (guard-consumable output) plus the
dislocation analysis with hand-computed expectations. No DB or network.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.probe_cross_venue_basis import (
    CrossVenueProbeConfig,
    _cheap_smoke_test,
    analyze_cross_venue,
    build_pair_spread_bars,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _row(
    hour: int, exchange: str, basis: float, close: float, low: float, premium: float = 0.0
) -> dict:
    return {
        "time": T0 + timedelta(hours=hour),
        "exchange": exchange,
        "basis_bps": basis,
        "premium_index": premium,
        "close_price": close,
        "low_price": low,
    }


def _gate_config(**overrides) -> CrossVenueProbeConfig:
    defaults = {
        "venues": ("va", "vb"),
        "symbols": ("SOLUSDT",),
        "timeframe": "1h",
        "start": "2024-01-01",
        "end": "2024-01-04",
        "tail_pcts": (5,),
        "forward_bars_12h": 12,
        "forward_bars_24h": 24,
        "min_events_per_pair": 2,
        "min_events_pooled": 2,
        "min_mean_forward_pct": 0.15,
        "min_mae_improvement_pct": 10.0,
        "max_concentration_pct": 50.0,
    }
    defaults.update(overrides)
    return CrossVenueProbeConfig(**defaults)


def _spread_fixture_rows(closes: list[float], lows: list[float]) -> list[dict]:
    """60 hourly bars; venue vb flat, venue va = i*0.1 bps except spikes 50 at bars 10/30.

    Hand-computed: tail-5 high threshold over the 60 spread values is 5.805
    (interpolated between the two largest non-spike values 5.8 and 5.9), so the
    extreme-positive events with a valid 12/24-bar forward window are exactly
    bars 10 and 30 (bar 59 qualifies but has no forward window).
    """
    rows: list[dict] = []
    for i in range(60):
        basis_a = 50.0 if i in (10, 30) else i * 0.1
        rows.append(_row(i, "va", basis_a, closes[i], lows[i]))
        rows.append(_row(i, "vb", 0.0, closes[i], lows[i]))
    return rows


def test_build_pair_spread_bars_signs_and_alignment():
    """Spread is A-minus-B per pair; times missing a venue are skipped."""
    rows = [
        _row(0, "va", 10.0, 100.0, 99.0, premium=0.002),
        _row(0, "vb", 3.0, 100.0, 99.0, premium=0.0005),
        _row(1, "va", 7.0, 100.0, 99.0),  # vb missing at hour 1 -> skipped
        _row(2, "vb", 4.0, 100.0, 99.0),  # va missing at hour 2 -> skipped
    ]
    pairs = build_pair_spread_bars(rows, ("va", "vb"))
    assert list(pairs.keys()) == ["va-vb"]
    bars = pairs["va-vb"]
    assert len(bars) == 1
    assert bars[0].basis_bps == 7.0  # 10.0 - 3.0
    assert bars[0].premium_index == 0.0015  # 0.002 - 0.0005
    assert bars[0].close_price == 100.0


def test_analyze_has_pulse_on_real_price_edge():
    """Spread spikes followed by +1% price moves must pass the forward gate.

    Hand-computed: events at bars 10 and 30; closes[22] = closes[42] = 101.0 so
    forward_12h is exactly +1.0% for both events (24h forward is 0%). Mean
    forward 1.0% > 0.15% gate; concentration = max/sum = 50% <= 50% gate.
    """
    closes = [100.0] * 60
    closes[22] = 101.0
    closes[42] = 101.0
    lows = [99.0] * 60
    rows_by_symbol = {"SOLUSDT": _spread_fixture_rows(closes, lows)}

    report = analyze_cross_venue(rows_by_symbol, _gate_config())

    assert report.verdict == "HAS_PULSE"
    assert "SOLUSDT|va-vb:extreme_positive:basis_bps:tail5" in report.passing_scenarios

    summary = next(s for s in report.symbols if s.symbol == "SOLUSDT|va-vb")
    scenario = next(
        s
        for s in summary.scenarios
        if s.metric == "basis_bps" and s.tail_pct == 5 and str(s.kind) == "extreme_positive"
    )
    assert len(scenario.events) == 2
    assert [e.forward_12h_pct for e in scenario.events] == pytest.approx([1.0, 1.0])


def test_analyze_weak_edge_when_prices_flat():
    """Anti-tautology: spread extremes with no price edge must NOT fire.

    Same spread spikes as the HAS_PULSE fixture, but flat prices: every forward
    return is 0%, MAE improvement is 0, so all gates fail -> WEAK_EDGE (events
    exist but show no edge), never HAS_PULSE from spread convergence alone.
    """
    closes = [100.0] * 60
    lows = [100.0] * 60
    rows_by_symbol = {"SOLUSDT": _spread_fixture_rows(closes, lows)}

    report = analyze_cross_venue(rows_by_symbol, _gate_config())

    assert report.verdict == "WEAK_EDGE"
    assert report.passing_scenarios == ()


def test_analyze_no_pulse_on_empty_rows():
    report = analyze_cross_venue({"SOLUSDT": []}, _gate_config())
    assert report.verdict == "NO_PULSE"
    assert report.passing_scenarios == ()


def test_cheap_smoke_helper():
    """The internal smoke helper should return a valid NO_PULSE report."""
    report = _cheap_smoke_test()
    assert report.verdict == "NO_PULSE"
    assert "SMOKE" in report.note


def test_smoke_cli_no_data(tmp_path: Path):
    """Running with --smoke should succeed, print NO_PULSE, and optionally write verdict JSON."""
    verdict_path = tmp_path / "verdict.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/probe_cross_venue_basis.py",
            "--smoke",
            "--verdict-output",
            str(verdict_path),
            "--venues",
            "binance_usdm,bybit",
            "--symbols",
            "BTCUSDT",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert result.returncode == 0
    assert "NO_PULSE" in result.stdout
    assert "SMOKE" in result.stdout
    assert verdict_path.exists()
    data = json.loads(verdict_path.read_text())
    assert data["verdict"] == "NO_PULSE"
    assert "SMOKE" in data["note"]
    assert "venues" in data["config"]


def test_verdict_output_format(tmp_path: Path):
    """--verdict-output must produce a minimal guard-consumable structure even in smoke."""
    verdict_path = tmp_path / "out.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/probe_cross_venue_basis.py",
            "--smoke",
            "--verdict-output",
            str(verdict_path),
        ],
        capture_output=True,
        cwd=Path(__file__).parent.parent,
        check=True,
    )
    data = json.loads(verdict_path.read_text())
    assert set(data.keys()) >= {"verdict", "note", "generated_at", "config"}
    assert data["verdict"] in ("NO_PULSE", "WEAK_EDGE", "HAS_PULSE")
    # Ensure the timestamp is valid ISO 8601 (no duplicate offset+Z etc.)
    from datetime import datetime

    datetime.fromisoformat(data["generated_at"])  # must parse without error
