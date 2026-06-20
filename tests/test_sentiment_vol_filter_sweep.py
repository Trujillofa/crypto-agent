"""Tests for sentiment-macro volatility-filter sweep harness."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.run_sentiment_vol_filter_sweep import (
    COST_PROFILE,
    FILTER_OFF_ARM,
    FORWARD_VALIDATABLE_TRADES_PER_MONTH,
    LANE,
    SYNTHETIC_SENTIMENT_SCORE,
    THRESHOLD_GRID,
    SweepArm,
    _arm_binding_audit,
    _build_synthetic_sentiment_log,
    _clamp_date_range,
    _decision_verdict,
    _resolve_arm_config,
    _select_arms,
    _summary_row,
    _threshold_arm_id,
)


def test_threshold_grid_matches_spec() -> None:
    assert THRESHOLD_GRID == (0.005, 0.0065, 0.0080, 0.0085, 0.0100, 0.0125)


def test_lane_uses_sentiment_macro_config() -> None:
    assert LANE.base_config.name == "settings.sentiment_macro.yaml"
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


def test_resolve_arm_config_sets_threshold(tmp_path: Path) -> None:
    arm = SweepArm(arm_id="0.0085", atr_pct_threshold=0.0085)
    resolved = _resolve_arm_config(lane=LANE, arm=arm, output_dir=tmp_path)
    binding = _arm_binding_audit(resolved, arm=arm)
    assert binding["strategy.strategies[0].config.atr_pct_threshold"] == 0.0085
    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert len(raw["strategy"]["strategies"]) == 1
    assert raw["strategy"]["strategies"][0]["config"]["atr_pct_threshold"] == 0.0085


def test_resolve_arm_config_filter_off(tmp_path: Path) -> None:
    arm = SweepArm(arm_id=FILTER_OFF_ARM, volatility_regime_filter=False)
    resolved = _resolve_arm_config(lane=LANE, arm=arm, output_dir=tmp_path)
    binding = _arm_binding_audit(resolved, arm=arm)
    assert binding["strategy.strategies[0].config.volatility_regime_filter"] is False
    with resolved.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    assert raw["strategy"]["strategies"][0]["config"]["volatility_regime_filter"] is False


def test_select_arms_rejects_unknown_threshold() -> None:
    with pytest.raises(SystemExit):
        _select_arms(thresholds=[0.007], arm_ids=None)


def test_select_arms_rejects_both_filters() -> None:
    with pytest.raises(SystemExit):
        _select_arms(thresholds=[0.005], arm_ids=["filter_off"])


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


def test_build_synthetic_sentiment_log_writes_hourly_events(tmp_path: Path) -> None:
    path = _build_synthetic_sentiment_log(
        output_path=tmp_path / "synthetic-sentiment-72.jsonl",
        symbol="SOLUSDT",
        effective_start="2024-01-09",
        effective_end="2024-01-11",
        score=SYNTHETIC_SENTIMENT_SCORE,
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 48
    assert '"score": 72' in lines[0]


def test_summary_row_computes_trades_per_month() -> None:
    arm = SweepArm(arm_id="0.0085", atr_pct_threshold=0.0085)
    payload = {
        "summary": {
            "wfo_windows": 3,
            "wfo_total_trades": 18,
            "wfo_total_return_pct": 5.0,
            "wfo_mean_sharpe": 0.6,
            "max_drawdown_pct": 8.0,
            "profit_concentration_pct": 40.0,
            "bootstrap_p_loss_pct": 12.0,
            "passes_gates": True,
            "failure_reasons": [],
        }
    }
    row = _summary_row(arm=arm, payload=payload, test_months=3)
    assert row["trades_per_month"] == 2.0


def test_decision_verdict_rescuable() -> None:
    rows = [
        {
            "arm_id": "0.0085",
            "atr_pct_threshold": 0.0085,
            "trades_per_month": 3.0,
            "wfo_mean_sharpe": 0.9,
            "passes_gates": True,
        }
    ]
    label, verdict = _decision_verdict(rows)
    assert label == "rescuable"
    assert _threshold_arm_id(0.0085) in verdict


def test_decision_verdict_frequency_only() -> None:
    rows = [
        {
            "arm_id": "0.0085",
            "atr_pct_threshold": 0.0085,
            "trades_per_month": 3.0,
            "wfo_mean_sharpe": 0.4,
            "passes_gates": False,
        },
        {
            "arm_id": FILTER_OFF_ARM,
            "atr_pct_threshold": None,
            "trades_per_month": 0.5,
            "wfo_mean_sharpe": 1.2,
            "passes_gates": True,
        },
    ]
    label, _ = _decision_verdict(rows)
    assert label == "frequency_only"


def test_decision_verdict_upstream_when_filter_off_starved() -> None:
    rows = [
        {
            "arm_id": arm_id,
            "atr_pct_threshold": threshold,
            "trades_per_month": 0.5,
            "wfo_mean_sharpe": 0.1,
            "passes_gates": False,
        }
        for arm_id, threshold in ((_threshold_arm_id(v), v) for v in THRESHOLD_GRID)
    ] + [
        {
            "arm_id": FILTER_OFF_ARM,
            "atr_pct_threshold": None,
            "trades_per_month": 0.4,
            "wfo_mean_sharpe": 0.2,
            "passes_gates": False,
        }
    ]
    label, verdict = _decision_verdict(rows)
    assert label == "upstream"
    assert str(int(FORWARD_VALIDATABLE_TRADES_PER_MONTH)) in verdict
