"""Regression coverage for shared backtest construction and evidence primitives."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from src.backtest.artifacts import (
    create_manifest,
    fingerprint_rows,
    write_manifest,
)
from src.backtest.engine import BacktestConfig, BacktestResult
from src.backtest.factory import BacktestRequest, build_backtest_config
from src.backtest.sizing import calculate_futures_order_quantity


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        trading_execution=SimpleNamespace(
            stop_loss_pct=0.03,
            take_profit_pct=0.06,
            use_atr_sizing=True,
            atr_multiplier=1.5,
            risk_per_trade_pct=0.02,
        ),
        futures=SimpleNamespace(enabled=True, symbols=["SOLUSDT"], default_leverage=3),
    )


def _result(*, total_return: float = 0.0) -> BacktestResult:
    return BacktestResult(
        total_return=total_return,
        total_return_pct=0.0,
        max_drawdown=0.0,
        win_rate=0.0,
        total_trades=0,
        trades=[],
        final_equity=10_000.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        profit_factor=math.inf,
        avg_win_loss_ratio=0.0,
    )


def test_factory_centralizes_cli_equivalent_resolution() -> None:
    raw_config = {
        "strategy": {
            "global_trend_filter_enabled": False,
            "global_trend_filter_buffer_pct": 0.02,
        },
        "trading_execution": {
            "exit_rules": {
                "backtest_use_executor_exit_model": True,
                "time_stop_minutes": 90,
            }
        },
    }
    request = BacktestRequest(
        symbol="SOLUSDT",
        timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2024-02-01T00:00:00",
        initial_capital=2_000.0,
        allow_short=True,
        fixed_notional_usdt=20.0,
        quantity_step_size=0.01,
        min_notional_usdt=20.0,
    )

    config = build_backtest_config(
        request=request,
        settings=_settings(),
        raw_config=raw_config,
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={"min_agreement": 1},
        fee_rate=0.001,
        stop_loss_pct=0.01,
        take_profit_pct=0.02,
    )

    assert config.futures_mode is True
    assert config.futures_leverage == 3
    assert config.apply_global_trend_filter is False
    assert config.global_trend_filter_source == "config"
    assert config.use_executor_exit_model is True
    assert config.time_stop_minutes == 90
    assert config.stop_loss_pct == 0.01
    assert config.take_profit_pct == 0.02
    assert config.fixed_notional_usdt == 20.0
    assert config.quantity_step_size == 0.01


def test_factory_records_search_trend_filter_override() -> None:
    config = build_backtest_config(
        request=BacktestRequest(
            symbol="SOLUSDT",
            timeframe="1h",
            start="2024-01-01T00:00:00",
            end="2024-02-01T00:00:00",
            trend_filter_override=True,
        ),
        settings=_settings(),
        raw_config={"strategy": {"global_trend_filter_enabled": False}},
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={},
    )

    assert config.apply_global_trend_filter is True
    assert config.global_trend_filter_source == "caller_override"
    assert config.config_global_trend_filter_enabled is False


def test_shared_futures_sizing_matches_exchange_minimum_notional_rule() -> None:
    assert calculate_futures_order_quantity(
        order_size_usdt=20.0,
        price=2_340.0,
        quantity_step_size=0.001,
        min_notional_usdt=20.0,
    ) == pytest.approx(0.009)
    assert calculate_futures_order_quantity(
        order_size_usdt=20.0,
        price=2_340.0,
        quantity_step_size=0.001,
        min_notional_usdt=0.0,
    ) == pytest.approx(0.008)


def test_manifest_is_deterministic_and_rejects_conflicting_evidence(tmp_path) -> None:
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-01-01T00:00:00",
        end_date="2024-02-01T00:00:00",
    )
    rows = [{"time": "2024-01-01T00:00:00", "close_price": 100.0}]
    data_fingerprint = fingerprint_rows(rows)
    manifest = create_manifest(
        config=config,
        result=_result(),
        data_fingerprint=data_fingerprint,
        revision="abc123",
        source_config="config/settings.yaml",
    )

    assert (
        manifest.run_id
        == create_manifest(
            config=config,
            result=_result(),
            data_fingerprint=data_fingerprint,
            revision="abc123",
            source_config="config/settings.yaml",
        ).run_id
    )
    assert write_manifest(tmp_path, manifest).exists()
    assert write_manifest(tmp_path, manifest).exists()

    conflicting = create_manifest(
        config=config,
        result=_result(total_return=1.0),
        data_fingerprint=data_fingerprint,
        revision="abc123",
        source_config="config/settings.yaml",
    )
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        write_manifest(tmp_path, conflicting)
