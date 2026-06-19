"""Tests for overlay buy_threshold frequency sweep harness."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.run_overlay_threshold_sweep import (
    COST_PROFILE,
    FORWARD_VALIDATABLE_TRADES_PER_MONTH,
    LANE,
    THRESHOLD_GRID,
    _clamp_date_range,
    _decision_verdict,
    _resolve_threshold_config,
    _select_thresholds,
    _summary_row,
    _threshold_binding_audit,
    _threshold_label,
)


def test_threshold_grid_matches_spec() -> None:
    assert THRESHOLD_GRID == (0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.07, 1.27)


def test_lane_uses_overlay_live_config() -> None:
    assert LANE.base_config.name == "settings.sol_1h_trend_pullback_overlay_live.yaml"
    assert LANE.symbol == "SOLUSDT"
    assert LANE.timeframe == "1h"
    assert LANE.train_months == 6
    assert LANE.test_months == 3
    assert LANE.bootstrap == 500
    assert LANE.gate_profile == "standard"


def test_cost_profile_mirrors_production_filter_on() -> None:
    assert COST_PROFILE.fee_rate == 0.0004
    assert COST_PROFILE.slippage_pct == 0.0002
    assert COST_PROFILE.funding_cadence == "scaled_8h"
    assert COST_PROFILE.apply_global_trend_filter is True


def test_resolve_threshold_config_sets_all_four_keys(tmp_path: Path) -> None:
    resolved = _resolve_threshold_config(lane=LANE, threshold=0.70, output_dir=tmp_path)
    assert resolved.exists()
    binding = _threshold_binding_audit(resolved, symbol=LANE.symbol, threshold=0.70)
    assert len(binding) == 4
    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert raw["strategy"]["aggregator"]["buy_threshold"] == 0.70
    assert raw["strategy"]["per_symbol_aggregator_config"]["SOLUSDT"]["buy_threshold"] == 0.70


def test_select_thresholds_rejects_unknown() -> None:
    with pytest.raises(SystemExit):
        _select_thresholds([0.75])


def test_clamp_date_range_reports_shortfall() -> None:
    start, end, coverage = _clamp_date_range(
        requested_start="2024-01-01",
        requested_end="2026-06-01",
        data_start="2024-01-09T07:00:00+00:00",
        data_end="2026-02-23T19:00:00+00:00",
    )
    assert start == "2024-01-09"
    assert end == "2026-02-23"
    assert coverage["clamped_start"] is True
    assert coverage["clamped_end"] is True


def test_summary_row_computes_trades_per_month() -> None:
    payload = {
        "summary": {
            "wfo_windows": 4,
            "wfo_total_trades": 24,
            "wfo_total_return_pct": 5.0,
            "wfo_mean_sharpe": 0.6,
            "max_drawdown_pct": 8.0,
            "profit_concentration_pct": 40.0,
            "bootstrap_p_loss_pct": 12.0,
            "passes_gates": True,
            "failure_reasons": [],
        }
    }
    row = _summary_row(threshold=0.80, payload=payload, test_months=3)
    assert row["trades_per_month"] == 2.0
    assert row["wfo_total_trades"] == 24


def test_decision_verdict_rescuable() -> None:
    rows = [
        {
            "buy_threshold": 0.50,
            "trades_per_month": 3.0,
            "wfo_mean_sharpe": 0.4,
            "passes_gates": True,
        },
        {
            "buy_threshold": 0.80,
            "trades_per_month": 2.5,
            "wfo_mean_sharpe": 0.9,
            "passes_gates": True,
        },
    ]
    label, verdict = _decision_verdict(rows)
    assert label == "rescuable"
    assert _threshold_label(0.50) in verdict
    assert _threshold_label(0.80) in verdict


def test_decision_verdict_frequency_only() -> None:
    rows = [
        {
            "buy_threshold": 0.50,
            "trades_per_month": 3.0,
            "wfo_mean_sharpe": 0.4,
            "passes_gates": False,
        },
        {
            "buy_threshold": 1.27,
            "trades_per_month": 0.1,
            "wfo_mean_sharpe": 1.2,
            "passes_gates": True,
        },
    ]
    label, _ = _decision_verdict(rows)
    assert label == "frequency_only"


def test_decision_verdict_upstream_starvation() -> None:
    rows = [
        {
            "buy_threshold": threshold,
            "trades_per_month": 0.5,
            "wfo_mean_sharpe": 0.1,
            "passes_gates": False,
        }
        for threshold in THRESHOLD_GRID
    ]
    label, verdict = _decision_verdict(rows)
    assert label == "upstream"
    assert str(int(FORWARD_VALIDATABLE_TRADES_PER_MONTH)) in verdict
