from __future__ import annotations

from src.backtest.experiment_autopilot import ExperimentSummary, GateConfig, evaluate_gates
from src.backtest.synthetic_eval import (
    MAX_EVAL_BARS,
    MIN_EVAL_BARS,
    _rate_from_outcomes,
    eval_bars_from_trade_rate,
    score_path,
)


def _passing_summary(**overrides: object) -> ExperimentSummary:
    payload: dict[str, object] = {
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "start": "2024-01-01",
        "end": "2025-01-01",
        "total_trades": 24,
        "win_rate": 55.0,
        "total_return_pct": 8.0,
        "max_drawdown_pct": 9.0,
        "sharpe_ratio": 0.8,
        "wfo_windows": 2,
        "wfo_total_trades": 24,
        "wfo_mean_sharpe": 0.7,
        "wfo_total_return_pct": 4.5,
        "bootstrap_p_loss_pct": 20.0,
        "mc_drawdown_p95_pct": 12.0,
        "mc_drawdown_p50_pct": 6.0,
        "synthetic_pass_rate_pct": 5.0,
        "profit_concentration_pct": 35.0,
        "passes_gates": False,
        "failure_reasons": [],
    }
    payload.update(overrides)
    return ExperimentSummary(**payload)  # type: ignore[arg-type]


def test_path_passes_empty() -> None:
    assert score_path([], kind="regime") is None
    assert score_path([], kind="stress") is None


def test_score_path_regime_ignores_drawdown() -> None:
    assert score_path([-20.0], kind="regime", min_return_pct=-25.0) is True


def test_score_path_stress_ignores_return() -> None:
    assert score_path([-5.0], kind="stress", max_drawdown_pct=10.0) is True
    assert score_path([-20.0], kind="stress", max_drawdown_pct=10.0) is False


def test_rate_zero_trade_excluded_and_inconclusive() -> None:
    result = _rate_from_outcomes([None, None, None, None, None, True])
    assert result.status == "inconclusive"
    assert result.pass_rate_pct == 0.0
    assert result.scored_paths == 1
    assert result.total_paths == 6
    assert result.zero_trade_paths == 5


def test_rate_scored_when_coverage_met() -> None:
    result = _rate_from_outcomes([True, True, True, None, None, None])
    assert result.status == "scored"
    assert result.pass_rate_pct == 100.0
    assert result.scored_paths == 3
    assert result.zero_trade_paths == 3


def test_eval_bars_from_trade_rate_scales_and_clamps() -> None:
    higher_rate = eval_bars_from_trade_rate(historical_trades=100, historical_bars=1000)
    lower_rate = eval_bars_from_trade_rate(historical_trades=8, historical_bars=1000)
    assert higher_rate == MIN_EVAL_BARS
    assert lower_rate > higher_rate
    assert lower_rate >= MIN_EVAL_BARS
    assert eval_bars_from_trade_rate(historical_trades=0, historical_bars=1000) == MIN_EVAL_BARS
    assert eval_bars_from_trade_rate(historical_trades=1, historical_bars=100_000) == MAX_EVAL_BARS


def test_gate_inert_at_zero() -> None:
    summary = _passing_summary(
        synthetic_pass_rate_pct=5.0,
        synthetic_eval_status="inconclusive",
    )
    for gates in (GateConfig(), GateConfig(min_synthetic_pass_rate_pct=0.0)):
        failures = evaluate_gates(summary, gates)
        assert not any("min_synthetic_pass_rate_pct failed" in reason for reason in failures)


def test_gate_inconclusive_fires_when_enabled() -> None:
    summary = _passing_summary(synthetic_eval_status="inconclusive")
    gates = GateConfig(min_synthetic_pass_rate_pct=50.0)
    failures = evaluate_gates(summary, gates)
    assert "min_synthetic_pass_rate_pct failed (inconclusive coverage)" in failures
