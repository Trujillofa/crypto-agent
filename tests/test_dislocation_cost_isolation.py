"""Tests for dislocation 2×2 factorial isolation harness."""

from __future__ import annotations

from scripts.run_dislocation_cost_isolation import (
    FACTORIAL_CELLS,
    PR92_LEGACY,
    PR92_REALISTIC,
    _attribute_flip,
    _within_noise,
)


def test_factorial_cells_cover_all_four_combinations() -> None:
    assert len(FACTORIAL_CELLS) == 4
    combos = {
        (cell.cost_profile.name, cell.cost_profile.apply_global_trend_filter)
        for cell in FACTORIAL_CELLS
    }
    assert combos == {
        ("legacy", True),
        ("realistic", True),
        ("legacy", False),
        ("realistic", False),
    }


def test_cell1_and_cell4_map_to_pr92_baselines() -> None:
    by_id = {cell.cell_id: cell for cell in FACTORIAL_CELLS}
    assert by_id["cell1"].cost_profile.apply_global_trend_filter is True
    assert by_id["cell1"].cost_profile.name == "legacy"
    assert by_id["cell4"].cost_profile.apply_global_trend_filter is False
    assert by_id["cell4"].cost_profile.name == "realistic"


def test_within_noise_tolerance() -> None:
    assert _within_noise(-23.76, PR92_LEGACY["wfo_return_pct"], abs_tol=2.0)
    assert _within_noise(4.64, PR92_REALISTIC["wfo_return_pct"], abs_tol=2.0)
    assert not _within_noise(10.0, PR92_LEGACY["wfo_return_pct"], abs_tol=2.0)


def test_attribute_flip_filter_dominant() -> None:
    rows = {
        "cell1": _mock_row(wfo_sharpe=-0.73, wfo_return_pct=-23.76),
        "cell2": _mock_row(wfo_sharpe=-0.70, wfo_return_pct=-22.0),
        "cell3": _mock_row(wfo_sharpe=0.15, wfo_return_pct=4.64),
        "cell4": _mock_row(wfo_sharpe=0.18, wfo_return_pct=5.0),
    }
    verdict, _, _ = _attribute_flip(rows)
    assert verdict == "filter"


def test_attribute_flip_cost_dominant() -> None:
    rows = {
        "cell1": _mock_row(wfo_sharpe=-0.73, wfo_return_pct=-23.76),
        "cell2": _mock_row(wfo_sharpe=0.15, wfo_return_pct=4.64),
        "cell3": _mock_row(wfo_sharpe=-0.70, wfo_return_pct=-22.0),
        "cell4": _mock_row(wfo_sharpe=0.18, wfo_return_pct=5.0),
    }
    verdict, _, _ = _attribute_flip(rows)
    assert verdict == "cost"


def _mock_row(*, wfo_sharpe: float, wfo_return_pct: float) -> dict[str, object]:
    return {
        "wfo_sharpe": wfo_sharpe,
        "wfo_return_pct": wfo_return_pct,
        "total_return_pct": 0.0,
        "wfo_trades": 0,
        "max_drawdown_pct": 0.0,
        "profit_concentration": 0.0,
        "passes_gates": False,
        "verdict": "FAIL",
    }
