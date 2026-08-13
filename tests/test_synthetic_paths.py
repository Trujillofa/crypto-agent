from __future__ import annotations

import statistics

from src.backtest.experiment_autopilot import ExperimentSummary, GateConfig, evaluate_gates
from src.backtest.synthetic import (
    RegimeParams,
    close_returns_pct,
    fit_two_state_regime,
    generate_regime_path,
    generate_stress_path,
    synthetic_pass_rate_pct,
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


def test_regime_path_seed_determinism() -> None:
    params = RegimeParams(
        mu_calm=0.0005,
        sigma_calm=0.004,
        mu_stress=-0.001,
        sigma_stress=0.02,
        p_calm_to_stress=0.1,
        p_stress_to_calm=0.2,
        p_start_stress=0.3,
    )
    candles_a, states_a = generate_regime_path(params, n_bars=80, start_price=100.0, seed=42)
    candles_b, states_b = generate_regime_path(params, n_bars=80, start_price=100.0, seed=42)
    candles_c, states_c = generate_regime_path(params, n_bars=80, start_price=100.0, seed=99)

    closes_a = [candle.close_price for candle in candles_a]
    closes_b = [candle.close_price for candle in candles_b]
    closes_c = [candle.close_price for candle in candles_c]
    assert closes_a == closes_b
    assert states_a == states_b
    assert closes_a != closes_c
    assert states_a != states_c


def test_stress_path_seed_determinism() -> None:
    candles_a = generate_stress_path("march_2020_gap", n_bars=40, start_price=100.0, seed=11)
    candles_b = generate_stress_path("march_2020_gap", n_bars=40, start_price=100.0, seed=11)
    candles_c = generate_stress_path("march_2020_gap", n_bars=40, start_price=100.0, seed=23)

    closes_a = [candle.close_price for candle in candles_a]
    closes_b = [candle.close_price for candle in candles_b]
    closes_c = [candle.close_price for candle in candles_c]
    assert closes_a == closes_b
    assert closes_a != closes_c


def test_stressed_state_has_higher_realised_vol() -> None:
    mixed: list[float] = []
    for _ in range(40):
        mixed.extend([0.001, -0.001, 0.0005, -0.0008])
        mixed.extend([0.04, -0.035, 0.05, -0.045])
    params = fit_two_state_regime(mixed)
    candles, states = generate_regime_path(params, n_bars=400, start_price=100.0, seed=7)
    rets = close_returns_pct(candles)
    stress_rets = [rets[i] for i in range(len(rets)) if states[i + 1] == "stress"]
    calm_rets = [rets[i] for i in range(len(rets)) if states[i + 1] == "calm"]
    assert len(stress_rets) >= 20
    assert len(calm_rets) >= 20
    assert statistics.stdev(stress_rets) > statistics.stdev(calm_rets)

    forced_stress = RegimeParams(
        mu_calm=0.0,
        sigma_calm=0.005,
        mu_stress=0.0,
        sigma_stress=0.03,
        p_calm_to_stress=0.0,
        p_stress_to_calm=0.0,
        p_start_stress=1.0,
    )
    forced_calm = RegimeParams(
        mu_calm=0.0,
        sigma_calm=0.005,
        mu_stress=0.0,
        sigma_stress=0.03,
        p_calm_to_stress=0.0,
        p_stress_to_calm=0.0,
        p_start_stress=0.0,
    )
    stress_path, _ = generate_regime_path(forced_stress, n_bars=400, start_price=100.0, seed=7)
    calm_path, _ = generate_regime_path(forced_calm, n_bars=400, start_price=100.0, seed=7)
    assert statistics.stdev(close_returns_pct(stress_path)) > statistics.stdev(
        close_returns_pct(calm_path)
    )


def test_march_2020_has_gap() -> None:
    candles = generate_stress_path("march_2020_gap", n_bars=40, start_price=100.0, seed=3)
    has_gap = any(
        curr.open_price <= 0.85 * prev.close_price
        for prev, curr in zip(candles, candles[1:], strict=False)
    )
    assert has_gap


def test_flat_wide_spread_range() -> None:
    start_price = 100.0
    candles = generate_stress_path("flat_wide_spread", n_bars=30, start_price=start_price, seed=5)
    assert all(abs(candle.close_price / start_price - 1.0) <= 0.0015 for candle in candles)
    mean_range = statistics.fmean(
        (candle.high_price - candle.low_price) / candle.close_price for candle in candles
    )
    assert mean_range >= 0.015


def test_synthetic_pass_rate_helper() -> None:
    paths = [[1.0, 2.0], [-1.0, 0.5]]
    assert synthetic_pass_rate_pct(paths, lambda _path: True) == 100.0
    assert synthetic_pass_rate_pct(paths, lambda _path: False) == 0.0
    assert synthetic_pass_rate_pct([], lambda _path: True) == 0.0
    assert synthetic_pass_rate_pct(paths, lambda path: sum(path) > 0.0) == 50.0


def test_gate_inert_at_zero() -> None:
    summary = _passing_summary(
        synthetic_pass_rate_pct=5.0,
        synthetic_eval_status="inconclusive",
    )
    for gates in (GateConfig(), GateConfig(min_synthetic_pass_rate_pct=0.0)):
        failures = evaluate_gates(summary, gates)
        assert not any("min_synthetic_pass_rate_pct failed" in reason for reason in failures)


def test_gate_fires_when_enabled() -> None:
    summary = _passing_summary(synthetic_pass_rate_pct=20.0, synthetic_eval_status="scored")
    gates = GateConfig(min_synthetic_pass_rate_pct=50.0)
    failures = evaluate_gates(summary, gates)
    assert "min_synthetic_pass_rate_pct failed (20.00% < 50.00%)" in failures
