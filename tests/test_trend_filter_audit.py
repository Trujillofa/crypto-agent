"""Global trend filter audit visibility tests (Task 3)."""

from __future__ import annotations

import pytest

from scripts.experiment_autopilot import _build_backtest_config, _resolve_global_trend_filter
from src.backtest.cost_overrides import legacy_cost_profile, realistic_cost_profile
from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyOnceStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "BuyOnce"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


def _base_raw_config(*, trend_filter_enabled: bool | None = None) -> dict[str, object]:
    strategy: dict[str, object] = {"global_trend_filter_buffer_pct": 0.01}
    if trend_filter_enabled is not None:
        strategy["global_trend_filter_enabled"] = trend_filter_enabled
    return {"strategy": strategy, "trading_execution": {}, "futures": {"enabled": False}}


class _StubSettings:
    trading_execution = type(
        "TE",
        (),
        {
            "stop_loss_pct": 0.0,
            "take_profit_pct": 0.0,
            "use_atr_sizing": False,
            "atr_multiplier": 1.5,
            "risk_per_trade_pct": 0.02,
        },
    )()
    futures = None


def test_resolve_trend_filter_engine_default() -> None:
    apply, source, explicit = _resolve_global_trend_filter(
        raw_config=_base_raw_config(),
        disable_trend_filter=False,
        cost_profile=None,
    )
    assert apply is True
    assert source == "engine_default"
    assert explicit is None


def test_resolve_trend_filter_cost_profile_override() -> None:
    apply, source, explicit = _resolve_global_trend_filter(
        raw_config=_base_raw_config(trend_filter_enabled=True),
        disable_trend_filter=False,
        cost_profile=realistic_cost_profile(apply_global_trend_filter=False),
    )
    assert apply is False
    assert source == "cost_profile_override"
    assert explicit is True


def test_resolved_audit_includes_trend_filter_fields() -> None:
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2023-01-01",
        end_date="2023-01-02",
        apply_global_trend_filter=False,
        global_trend_filter_buffer_pct=0.01,
        global_trend_filter_source="cli_override",
        config_global_trend_filter_enabled=False,
    )
    audit = BacktestEngine(config, IndicatorReader({}))._resolved_cost_audit()
    assert audit["global_trend_filter_active"] is False
    assert audit["global_trend_filter_buffer_pct"] == 0.01
    assert audit["global_trend_filter_source"] == "cli_override"
    assert audit["config_global_trend_filter_enabled"] is False


@pytest.mark.asyncio
async def test_explicit_true_blocks_buy_below_ema200() -> None:
    reader = IndicatorReader({})
    reader._connected = True
    data = [
        {"time": "2023-01-01T00:00:00", "close_price": 100.0, "ema_200": 120.0},
        {"time": "2023-01-01T00:01:00", "close_price": 100.0, "ema_200": 120.0},
    ]

    async def _mock_fetch(*_args: object) -> list[dict[str, object]]:
        return data

    reader.fetch_range = _mock_fetch

    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=10000.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        apply_global_trend_filter=True,
        global_trend_filter_buffer_pct=0.01,
        global_trend_filter_source="engine_default",
        strategy_classes=[BuyOnceStrategy],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
    )
    result = await BacktestEngine(config, reader).run()
    assert result.total_trades == 0


@pytest.mark.asyncio
async def test_explicit_false_allows_buy_below_ema200() -> None:
    reader = IndicatorReader({})
    reader._connected = True
    data = [
        {"time": "2023-01-01T00:00:00", "close_price": 100.0, "ema_200": 120.0},
        {"time": "2023-01-01T00:01:00", "close_price": 100.0, "ema_200": 120.0},
    ]

    async def _mock_fetch(*_args: object) -> list[dict[str, object]]:
        return data

    reader.fetch_range = _mock_fetch

    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=10000.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        apply_global_trend_filter=False,
        global_trend_filter_source="cli_override",
        strategy_classes=[BuyOnceStrategy],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
    )
    result = await BacktestEngine(config, reader).run()
    assert result.total_trades == 1


def test_build_backtest_config_records_explicit_yaml_value() -> None:
    config = _build_backtest_config(
        settings=_StubSettings(),
        raw_config=_base_raw_config(trend_filter_enabled=False),
        symbol="SOLUSDT",
        timeframe="1h",
        start="2024-01-01",
        end="2024-06-01",
        strategy_classes=[],
        strategy_configs=[],
        aggregator_config={},
        initial_capital=10000.0,
        disable_trend_filter=False,
        replay_sentiment_path=None,
        replay_sentiment_max_age_hours=None,
        cost_profile=legacy_cost_profile(),
    )
    assert config.apply_global_trend_filter is True
    assert config.global_trend_filter_source == "cost_profile_override"
    assert config.config_global_trend_filter_enabled is False
    assert config.global_trend_filter_buffer_pct == 0.01
