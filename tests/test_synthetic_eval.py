from __future__ import annotations

import inspect
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.backtest.artifacts import fingerprint_rows
from src.backtest.engine import BacktestEngine
from src.backtest.experiment_autopilot import ExperimentSummary, GateConfig, evaluate_gates
from src.backtest.models import BacktestConfig, BacktestResult
from src.backtest.synthetic import fit_two_state_regime, generate_regime_path, generate_stress_path
from src.backtest.synthetic_eval import (
    DEFAULT_REGIME_PARAMS,
    MAX_EVAL_BARS,
    MIN_EVAL_BARS,
    SyntheticEvalResult,
    SyntheticPathRecord,
    _rate_from_named,
    _rate_from_outcomes,
    _run_path,
    decimal_close_returns,
    eval_bars_from_trade_rate,
    evaluate_synthetic_pass_rate,
    maybe_evaluate_synthetic_pass_rate,
    score_engine_result,
    score_path,
)
from src.backtest.synthetic_reader import (
    SyntheticIndicatorReader,
    blowout_funding_settlements,
)
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.strategy.simple_ma import SimpleMACrossoverStrategy


class BuyHoldStrategy(BaseStrategy):
    """Buy the first bar and hold for the rest of the path."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._opened = False

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        price = float(indicators["close_price"])
        if not self._opened:
            self._opened = True
            return Signal(SignalType.BUY, symbol, price, 1.0, "buy-hold", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "hold", indicators)


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


def _dummy_config() -> BacktestConfig:
    return BacktestConfig(
        symbol="SYNTH",
        timeframe="1h",
        start_date="2020-01-01T00:00:00+00:00",
        end_date="2020-02-01T00:00:00+00:00",
    )


async def test_disabled_skips_compute() -> None:
    with patch(
        "src.backtest.synthetic_eval.evaluate_synthetic_pass_rate",
        new_callable=AsyncMock,
    ) as mock_evaluate:
        result = await maybe_evaluate_synthetic_pass_rate(_dummy_config(), enabled=False)

    mock_evaluate.assert_not_awaited()
    mock_evaluate.assert_not_called()
    assert result.status == "not_run"
    assert result.pass_rate_pct == 0.0


async def test_enabled_forwards_to_evaluate() -> None:
    scored = SyntheticEvalResult(
        status="scored",
        pass_rate_pct=80.0,
        scored_paths=5,
        total_paths=6,
        zero_trade_paths=1,
    )
    with patch(
        "src.backtest.synthetic_eval.evaluate_synthetic_pass_rate",
        new_callable=AsyncMock,
        return_value=scored,
    ) as mock_evaluate:
        result = await maybe_evaluate_synthetic_pass_rate(_dummy_config(), enabled=True)

    mock_evaluate.assert_awaited_once()
    assert result is scored


def test_render_not_run_is_not_zero_percent() -> None:
    from scripts.experiment_autopilot import _render_markdown

    summary = _passing_summary(synthetic_eval_status="not_run", synthetic_pass_rate_pct=0.0)
    markdown = _render_markdown(
        summary=summary,
        windows=[],
        gates=GateConfig(),
        config_path=Path("config/settings.yaml"),
    )
    assert "not_run" in markdown
    synth_lines = [line for line in markdown.splitlines() if "Synthetic pass rate" in line]
    assert synth_lines
    assert "0.00%" not in synth_lines[0]


def test_gate_not_run_inert_when_disabled() -> None:
    summary = _passing_summary(synthetic_eval_status="not_run", synthetic_pass_rate_pct=0.0)
    for gates in (GateConfig(), GateConfig(min_synthetic_pass_rate_pct=0.0)):
        failures = evaluate_gates(summary, gates)
        assert not any("min_synthetic_pass_rate_pct failed" in reason for reason in failures)


def test_gate_not_run_fails_when_enabled() -> None:
    gates = GateConfig(min_synthetic_pass_rate_pct=50.0)
    for status in ("not_run", ""):
        summary = _passing_summary(synthetic_eval_status=status)
        failures = evaluate_gates(summary, gates)
        assert "min_synthetic_pass_rate_pct failed (not_run)" in failures


async def test_futures_v2_synthetic_eval_requires_settlements() -> None:
    candles, _states = generate_regime_path(
        DEFAULT_REGIME_PARAMS,
        n_bars=280,
        start_price=100.0,
        seed=1,
    )
    reader = SyntheticIndicatorReader(candles, warmup_bars=200)
    config = BacktestConfig(
        symbol="SYNTH",
        timeframe="1h",
        start_date=reader.eval_start.isoformat(),
        end_date=reader.eval_end.isoformat(),
        execution_profile="execution_parity_v2",
        futures_mode=True,
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs=[None],
        apply_global_trend_filter=False,
    )
    with pytest.raises(ValueError, match="Missing historical funding settlements"):
        await BacktestEngine(config, reader).run()


async def test_futures_v2_eval_completes_with_settlements() -> None:
    config = BacktestConfig(
        symbol="SYNTH",
        timeframe="1h",
        start_date="2020-01-01T00:00:00+00:00",
        end_date="2020-02-01T00:00:00+00:00",
        execution_profile="execution_parity_v2",
        futures_mode=True,
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs=[None],
        apply_global_trend_filter=False,
    )
    result = await evaluate_synthetic_pass_rate(config, eval_bars=80, warmup_bars=200)
    assert result.status in {"scored", "inconclusive"}


async def test_funding_blowout_changes_funding_paid() -> None:
    warmup = 200
    candles = generate_stress_path(
        "funding_blowout",
        n_bars=warmup + 80,
        start_price=100.0,
        seed=1,
        timeframe="1h",
    )
    eval_candles = candles[warmup:]
    settlements = blowout_funding_settlements(eval_candles)
    reader = SyntheticIndicatorReader(candles, warmup_bars=warmup, funding=settlements)
    config = BacktestConfig(
        symbol="SYNTH",
        timeframe="1h",
        start_date=reader.eval_start.isoformat(),
        end_date=reader.eval_end.isoformat(),
        execution_profile="execution_parity_v2",
        futures_mode=True,
        strategy_classes=[BuyHoldStrategy],
        strategy_configs=[None],
        apply_global_trend_filter=False,
        fee_rate=0.0,
        slippage_pct=0.0,
    )
    result = await BacktestEngine(config, reader).run()
    assert result.trades
    assert any(abs(trade.funding_paid) > 0 for trade in result.trades)
    assert any(item.funding_rate != 0.0 for item in settlements)


async def test_run_path_returns_backtest_result() -> None:
    config = BacktestConfig(
        symbol="SYNTH",
        timeframe="1h",
        start_date="2020-01-01T00:00:00+00:00",
        end_date="2020-02-01T00:00:00+00:00",
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs=[None],
        apply_global_trend_filter=False,
    )
    candles, _states = generate_regime_path(
        DEFAULT_REGIME_PARAMS,
        n_bars=280,
        start_price=100.0,
        seed=1,
    )
    result = await _run_path(config, candles, 200)
    assert hasattr(result, "total_return_pct")
    assert hasattr(result, "max_drawdown")


def test_cli_has_no_synthetic_threshold() -> None:
    from scripts.run_autoresearch import _gate_config_from_profile

    autopilot = Path("scripts/experiment_autopilot.py").read_text(encoding="utf-8")
    assert "--min-synthetic" not in autopilot
    construction = inspect.getsource(_gate_config_from_profile)
    assert "min_synthetic_pass_rate" not in construction


def _engine_result(
    *,
    total_trades: int,
    total_return_pct: float,
    max_drawdown: float,
) -> BacktestResult:
    return BacktestResult(
        total_return=0.0,
        total_return_pct=total_return_pct,
        max_drawdown=max_drawdown,
        win_rate=0.0,
        total_trades=total_trades,
        trades=[],
        final_equity=10000.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        profit_factor=0.0,
        avg_win_loss_ratio=0.0,
    )


def _named_path(
    *,
    name: str,
    kind: str,
    outcome: str,
    seed: int = 0,
) -> SyntheticPathRecord:
    return SyntheticPathRecord(
        name=name,
        kind=kind,
        seed=seed,
        trades=0 if outcome == "skip" else 1,
        total_return_pct=1.0 if outcome == "pass" else -1.0,
        max_drawdown_pct=1.0,
        outcome=outcome,
    )


def test_decimal_close_returns() -> None:
    assert decimal_close_returns([]) == []
    assert decimal_close_returns([100.0]) == []
    returns = decimal_close_returns([100, 110, 99])
    assert returns[0] == 0.1
    assert returns[1] == float(Decimal("99") / Decimal("110") - 1)
    assert decimal_close_returns([0.0, 10.0]) == [0.0]


def test_score_engine_regime_uses_total_return() -> None:
    empty = _engine_result(total_trades=0, total_return_pct=5.0, max_drawdown=0.5)
    assert score_engine_result(empty, kind="regime") is None
    passed = score_engine_result(
        _engine_result(total_trades=2, total_return_pct=1.0, max_drawdown=0.5),
        kind="regime",
    )
    failed = score_engine_result(
        _engine_result(total_trades=2, total_return_pct=-1.0, max_drawdown=0.01),
        kind="regime",
    )
    assert passed is True
    assert failed is False


def test_score_engine_stress_uses_max_drawdown() -> None:
    passed = score_engine_result(
        _engine_result(total_trades=2, total_return_pct=-50.0, max_drawdown=0.05),
        kind="stress",
        max_drawdown_pct=10.0,
    )
    failed = score_engine_result(
        _engine_result(total_trades=2, total_return_pct=50.0, max_drawdown=0.20),
        kind="stress",
        max_drawdown_pct=10.0,
    )
    assert passed is True
    assert failed is False


def test_separate_coverage_inconclusive() -> None:
    records = [
        _named_path(name="regime_0", kind="regime", outcome="pass"),
        _named_path(name="regime_1", kind="regime", outcome="pass"),
        _named_path(name="regime_2", kind="regime", outcome="pass"),
    ]
    result = _rate_from_named(records)
    assert result.status == "inconclusive"
    assert result.pass_rate_pct == 0.0
    assert result.regime_scored == 3
    assert result.stress_scored == 0
    assert result.regime_pass_rate_pct == 100.0


def test_separate_coverage_scored() -> None:
    records = [
        _named_path(name="regime_0", kind="regime", outcome="pass"),
        _named_path(name="regime_1", kind="regime", outcome="pass"),
        _named_path(name="march_2020_gap", kind="stress", outcome="pass"),
        _named_path(name="funding_blowout", kind="stress", outcome="pass"),
    ]
    result = _rate_from_named(records)
    assert result.status == "scored"
    assert result.pass_rate_pct == 100.0
    assert result.regime_scored == 2
    assert result.stress_scored == 2


async def test_evaluate_requires_fit_when_require_fit() -> None:
    with pytest.raises(ValueError, match="require_fit"):
        await evaluate_synthetic_pass_rate(_dummy_config(), require_fit=True)


async def test_evaluate_fits_two_state() -> None:
    fit_closes = [100.0, 100.2, 100.1, 99.8, 95.0, 93.5, 94.0, 100.5, 101.0]
    result = await evaluate_synthetic_pass_rate(
        _dummy_config(),
        require_fit=True,
        fit_start="2020-01-01T00:00:00+00:00",
        fit_end="2020-06-01T00:00:00+00:00",
        fit_closes=fit_closes,
        eval_bars=80,
        n_regime_paths=1,
        include_stress=False,
    )
    assert result.status == "inconclusive"
    assert result.fit is not None
    expected = fit_two_state_regime(decimal_close_returns(fit_closes))
    assert result.fit.params["mu_calm"] == expected.mu_calm
    assert result.fit.params["sigma_calm"] == expected.sigma_calm
    assert result.fit.params["mu_stress"] == expected.mu_stress
    assert result.fit.params["sigma_stress"] == expected.sigma_stress
    assert result.fit.params["p_calm_to_stress"] == expected.p_calm_to_stress
    assert result.fit.params["p_stress_to_calm"] == expected.p_stress_to_calm
    assert result.fit.params["p_start_stress"] == expected.p_start_stress
    assert result.fit.row_fingerprint == fingerprint_rows(
        [{"close": close} for close in fit_closes]
    )
    assert result.fit.fit_start == "2020-01-01T00:00:00+00:00"
    assert result.fit.fit_end == "2020-06-01T00:00:00+00:00"


async def test_runtime_evidence_records_bounds() -> None:
    result = await evaluate_synthetic_pass_rate(
        _dummy_config(),
        require_fit=True,
        fit_start="2020-01-01T00:00:00+00:00",
        fit_end="2020-06-01T00:00:00+00:00",
        fit_closes=[100.0, 101.0, 99.0, 102.0, 90.0, 91.0],
        eval_bars=80,
        n_regime_paths=1,
        include_stress=False,
    )
    assert result.runtime is not None
    assert result.runtime.min_eval_bars == MIN_EVAL_BARS
    assert result.runtime.max_eval_bars == MAX_EVAL_BARS
    assert result.runtime.min_eval_bars == 480
    assert result.runtime.max_eval_bars == 4000
    assert result.runtime.generate_min_ms >= 0.0
    assert result.runtime.generate_max_ms >= 0.0


def test_diagnostic_does_not_affect_passes_gates() -> None:
    scored_zero = _passing_summary(
        synthetic_eval_status="scored",
        synthetic_pass_rate_pct=0,
    )
    failures = evaluate_gates(scored_zero, GateConfig())
    assert not any("min_synthetic" in reason for reason in failures)

    passing = _passing_summary()
    passing_failures = evaluate_gates(passing, GateConfig())
    assert passing_failures == []
    assert not any("min_synthetic" in reason for reason in passing_failures)


def test_cli_has_synthetic_diagnostic_not_threshold() -> None:
    autopilot = Path("scripts/experiment_autopilot.py").read_text(encoding="utf-8")
    assert "--synthetic-diagnostic" in autopilot
    assert "--synthetic-fit-start" in autopilot
    assert "--min-synthetic" not in autopilot
