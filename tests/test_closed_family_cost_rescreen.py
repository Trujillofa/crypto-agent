"""Tests for closed-family cost-corrected re-screen harness."""

from __future__ import annotations

from scripts.run_closed_family_cost_rescreen import (
    FILTER_CELLS,
    FROZEN_LANES,
    _pick_best_cell,
)


def test_frozen_lane_set_covers_mean_reversion_family() -> None:
    lane_ids = {lane.lane_id for lane in FROZEN_LANES}
    assert "sol-4h-rsi-reversal" in lane_ids
    assert "avax-4h-bollinger-strategy" in lane_ids
    assert "avax-4h-mean-reversion" in lane_ids
    assert "eth-4h-range-reversion-bounded" in lane_ids


def test_mean_reversion_lane_documented_skip() -> None:
    lane = next(item for item in FROZEN_LANES if item.lane_id == "avax-4h-mean-reversion")
    assert lane.skipped is True
    assert "pair_close_price" in lane.skip_reason


def test_filter_cells_use_corrected_costs_only() -> None:
    assert len(FILTER_CELLS) == 2
    combos = {
        (cell.cost_profile.name, cell.cost_profile.apply_global_trend_filter)
        for cell in FILTER_CELLS
    }
    assert combos == {("corrected", False), ("corrected", True)}
    for cell in FILTER_CELLS:
        assert cell.cost_profile.fee_rate == 0.0004
        assert cell.cost_profile.slippage_pct == 0.0002
        assert cell.cost_profile.funding_cadence == "scaled_8h"


def test_pick_best_cell_prefers_pass_then_sharpe() -> None:
    rows = {
        "cell_a": {
            "passes_gates": False,
            "wfo_sharpe": 0.10,
            "wfo_return_pct": 1.0,
            "wfo_trades": 20,
            "max_drawdown_pct": 5.0,
            "profit_concentration": 40.0,
            "verdict": "FAIL",
        },
        "cell_b": {
            "passes_gates": True,
            "wfo_sharpe": 0.55,
            "wfo_return_pct": 2.0,
            "wfo_trades": 25,
            "max_drawdown_pct": 6.0,
            "profit_concentration": 35.0,
            "verdict": "PASS",
        },
    }
    assert _pick_best_cell(rows) == "cell_b"


def test_all_runnable_lanes_use_standard_gate() -> None:
    for lane in FROZEN_LANES:
        if not lane.skipped:
            assert lane.gate_profile == "standard"
