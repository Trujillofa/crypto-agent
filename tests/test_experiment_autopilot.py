from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

from scripts.experiment_autopilot import _build_backtest_config
from src.backtest.experiment_autopilot import (
    ExperimentSummary,
    GateConfig,
    add_months,
    bootstrap_loss_probability_pct,
    build_wfo_windows,
    evaluate_gates,
    profit_concentration_pct,
)


def test_add_months_clamps_day() -> None:
    from datetime import datetime

    base = datetime.fromisoformat("2024-01-31T00:00:00")
    result = add_months(base, 1)
    assert result.isoformat().startswith("2024-02-29")


def test_build_wfo_windows_produces_expected_count() -> None:
    windows = build_wfo_windows(
        start="2024-01-01T00:00:00",
        end="2025-01-01T00:00:00",
        train_months=6,
        test_months=3,
    )
    assert len(windows) == 1
    assert windows[0].test_start.startswith("2024-07-01")
    assert windows[0].test_end.startswith("2024-10-01")


def test_bootstrap_loss_probability_extremes() -> None:
    assert bootstrap_loss_probability_pct([1.0, 2.0, 3.0], iterations=100, seed=7) == 0.0
    assert bootstrap_loss_probability_pct([-1.0, -2.0, -3.0], iterations=100, seed=7) == 100.0


def test_profit_concentration_pct() -> None:
    concentration = profit_concentration_pct([1.0, 1.0, 2.0])
    assert round(concentration, 2) == 50.00


def test_evaluate_gates_flags_failures() -> None:
    summary = ExperimentSummary(
        symbol="SOLUSDT",
        timeframe="4h",
        start="2024-01-01",
        end="2025-01-01",
        total_trades=10,
        win_rate=40.0,
        total_return_pct=-5.0,
        max_drawdown_pct=18.0,
        sharpe_ratio=-0.2,
        wfo_windows=1,
        wfo_total_trades=6,
        wfo_mean_sharpe=0.1,
        wfo_total_return_pct=-2.0,
        bootstrap_p_loss_pct=60.0,
        profit_concentration_pct=90.0,
        passes_gates=False,
        failure_reasons=[],
    )
    gates = GateConfig()

    failures = evaluate_gates(summary, gates)

    assert any("min_wfo_trades failed" in failure for failure in failures)
    assert any("min_wfo_sharpe failed" in failure for failure in failures)
    assert any("max_drawdown_pct failed" in failure for failure in failures)
    assert any("max_bootstrap_p_loss_pct failed" in failure for failure in failures)
    assert any("min_oos_return_pct failed" in failure for failure in failures)
    assert any("max_profit_concentration_pct failed" in failure for failure in failures)


def test_experiment_summary_can_be_rebuilt_with_updated_gate_fields() -> None:
    summary_seed = ExperimentSummary(
        symbol="SOLUSDT",
        timeframe="4h",
        start="2024-01-01",
        end="2025-01-01",
        total_trades=24,
        win_rate=55.0,
        total_return_pct=8.0,
        max_drawdown_pct=9.0,
        sharpe_ratio=0.8,
        wfo_windows=2,
        wfo_total_trades=24,
        wfo_mean_sharpe=0.7,
        wfo_total_return_pct=4.5,
        bootstrap_p_loss_pct=20.0,
        profit_concentration_pct=35.0,
        passes_gates=False,
        failure_reasons=[],
    )

    summary_payload = asdict(summary_seed)
    summary_payload["passes_gates"] = True
    summary_payload["failure_reasons"] = []

    summary = ExperimentSummary(**summary_payload)

    assert summary.passes_gates is True
    assert summary.failure_reasons == []


def test_evaluate_gates_can_disable_full_period_trade_gate() -> None:
    summary = ExperimentSummary(
        symbol="SOLUSDT",
        timeframe="4h",
        start="2024-01-01",
        end="2025-01-01",
        total_trades=2,
        win_rate=60.0,
        total_return_pct=6.0,
        max_drawdown_pct=8.0,
        sharpe_ratio=0.5,
        wfo_windows=2,
        wfo_total_trades=22,
        wfo_mean_sharpe=0.6,
        wfo_total_return_pct=3.0,
        bootstrap_p_loss_pct=20.0,
        profit_concentration_pct=40.0,
        passes_gates=False,
        failure_reasons=[],
    )
    gates = GateConfig(min_trades=0, min_wfo_trades=20)

    failures = evaluate_gates(summary, gates)

    assert not any("min_trades failed" in failure for failure in failures)
    assert not any("min_wfo_trades failed" in failure for failure in failures)


def test_build_backtest_config_preserves_replay_and_executor_exit_fields() -> None:
    settings = SimpleNamespace(
        trading_execution=SimpleNamespace(
            stop_loss_pct=0.01,
            take_profit_pct=0.03,
            use_atr_sizing=False,
            atr_multiplier=1.0,
            risk_per_trade_pct=0.02,
        )
    )
    raw_config = {
        "trading_execution": {
            "sl_atr_multiplier": 1.5,
            "tp_atr_multiplier": 3.5,
            "trailing_activate_atr": 1.5,
            "trailing_offset_atr": 1.0,
            "exit_rules": {
                "backtest_use_executor_exit_model": True,
                "backtest_ignore_signal_sells": False,
                "time_stop_minutes": 1440,
            },
        },
        "strategy": {"global_trend_filter_buffer_pct": 0.05},
    }

    config = _build_backtest_config(
        settings=settings,
        raw_config=raw_config,
        symbol="BTCUSDT",
        timeframe="1h",
        start="2026-03-27T12:00:00+00:00",
        end="2026-04-27T01:00:00+00:00",
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={"buy_threshold": 0.6, "sell_threshold": -0.6},
        initial_capital=10000.0,
        disable_trend_filter=False,
        replay_sentiment_path="data/event_log_sentiment-macro-bot.jsonl",
        replay_sentiment_max_age_hours=24.0,
    )

    assert config.use_executor_exit_model is True
    assert config.ignore_signal_sells is False
    assert config.sl_atr_multiplier == 1.5
    assert config.tp_atr_multiplier == 3.5
    assert config.trailing_activate_atr == 1.5
    assert config.trailing_offset_atr == 1.0
    assert config.time_stop_minutes == 1440
    assert config.global_trend_filter_buffer_pct == 0.05
    assert config.replay_sentiment_path == "data/event_log_sentiment-macro-bot.jsonl"
    assert config.replay_sentiment_max_age_seconds == 24 * 3600
