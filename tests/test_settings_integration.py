"""Integration tests for settings loading and defaults."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.main import (
    _build_strategy_registry,
    _resolve_strategy_config,
    _wire_optional_strategy_dependencies,
    load_settings,
)
from src.strategy import (
    BreakoutRetestStrategy,
    CCIBreakoutStrategy,
    EngineConfig,
    MacroVolatilityStrategy,
    SentimentMeanReversionStrategy,
    Signal,
    SignalType,
    StrategyEngine,
    TrendPullbackStrategy,
    VWAPReversionStrategy,
)
from src.strategy.mtf_template import MTFStrategyTemplate


def test_settings_default_safe():
    """Verify settings.yaml loads with safe defaults (paper mode with test mode)."""
    settings = load_settings(Path("config/settings.yaml"))

    # Paper trading is enabled by default for full pipeline testing
    # Safety is ensured by test_mode=True (uses Binance testnet, not real funds)
    assert (
        settings.trading_execution.enabled is True
    ), "Paper trading should be enabled by default for pipeline testing"

    # Test mode should be enabled by default (crucial safety check)
    assert (
        settings.trading_execution.test_mode is True
    ), "Test mode must be enabled by default for safety"

    with Path("config/settings.yaml").open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert settings.strategy.evaluation_interval_seconds == int(
        raw["strategy"]["evaluation_interval_seconds"]
    )


def test_settings_has_strategy_section():
    """Verify settings.yaml contains strategy section."""
    with Path("config/settings.yaml").open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    assert "strategy" in raw, "settings.yaml must contain 'strategy' section"
    assert (
        "evaluation_interval_seconds" in raw["strategy"]
    ), "strategy section must have 'evaluation_interval_seconds'"


def test_settings_all_required_sections():
    """Verify all required sections present in settings.yaml."""
    settings = load_settings(Path("config/settings.yaml"))

    # All sections should be present
    assert settings.mode in ("paper", "live"), "mode must be 'paper' or 'live'"
    assert settings.log_level in (
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
    ), "Invalid log level"
    assert len(settings.trading_pairs) > 0, "Must have at least one trading pair"
    assert settings.timeframe in ("1m", "5m", "15m", "1h", "4h"), "Invalid timeframe"
    assert settings.database is not None, "Database config required"
    assert settings.prometheus_port > 0, "Prometheus port required"
    assert settings.trading_execution is not None, "Trading execution config required"
    assert settings.strategy is not None, "Strategy config required"


def test_settings_telegram_config():
    """Verify telegram settings are loaded correctly."""
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.telegram is not None, "Telegram config required"
    assert settings.telegram.rate_limit_seconds == 5
    assert isinstance(settings.telegram.enabled, bool)


def test_settings_resolves_new_strategy_registry_entries():
    """Configured strategy names resolve to the expected strategy classes."""
    settings = load_settings(Path("config/settings.yaml"))
    strategy_classes, strategy_configs, _, _per_symbol = _resolve_strategy_config(settings.strategy)

    assert len(strategy_classes) == len(settings.strategy.strategies)
    assert len(strategy_configs) == len(settings.strategy.strategies)
    assert CCIBreakoutStrategy in strategy_classes
    assert VWAPReversionStrategy in strategy_classes


def test_replacement_config_resolves_trend_pullback():
    settings = load_settings(Path("config/settings.sol_trend_pullback.yaml"))
    strategy_classes, strategy_configs, aggregator_config, _per_symbol = _resolve_strategy_config(
        settings.strategy
    )

    assert strategy_classes == [TrendPullbackStrategy]
    assert len(strategy_configs) == 1
    assert aggregator_config["buy_threshold"] == 0.45


def test_sparse_replacement_config_resolves_trend_pullback_cluster():
    settings = load_settings(Path("config/settings.sol_trend_pullback_sparse.yaml"))
    strategy_classes, strategy_configs, aggregator_config, per_symbol = _resolve_strategy_config(
        settings.strategy
    )

    assert strategy_classes == [TrendPullbackStrategy]
    assert len(strategy_configs) == 1
    assert strategy_configs[0]["rsi_reclaim_level"] == 48
    assert strategy_configs[0]["min_trend_strength_pct"] == 0.006
    assert strategy_configs[0]["vwap_pullback_distance_pct"] == 0.05
    assert aggregator_config["buy_threshold"] == 0.45
    assert per_symbol["SOLUSDT"]["buy_threshold"] == 0.45


def test_replacement_config_resolves_breakout_retest():
    settings = load_settings(Path("config/settings.sol_breakout_retest.yaml"))
    strategy_classes, strategy_configs, aggregator_config, _per_symbol = _resolve_strategy_config(
        settings.strategy
    )

    assert strategy_classes == [BreakoutRetestStrategy]
    assert len(strategy_configs) == 1
    assert aggregator_config["buy_threshold"] == 0.45


def test_sentiment_macro_config_resolves_sentiment_strategy_only():
    settings = load_settings(Path("config/settings.sentiment_macro.yaml"))
    strategy_classes, strategy_configs, aggregator_config, _per_symbol = _resolve_strategy_config(
        settings.strategy
    )

    assert strategy_classes == [SentimentMeanReversionStrategy]
    assert len(strategy_configs) == 1
    assert settings.telegram.enabled is False
    assert settings.ai.enabled is True
    assert aggregator_config["buy_threshold"] == 0.6


def test_btc_mtf_paper_config_resolves_mtf_template():
    settings = load_settings(Path("config/settings.btc_1h_mtf.yaml"))
    strategy_classes, strategy_configs, aggregator_config, _per_symbol = _resolve_strategy_config(
        settings.strategy
    )

    assert settings.agent_id == "btc-1h-mtf"
    assert settings.trading_pairs == ["BTCUSDT"]
    assert settings.timeframe == "1h"
    assert settings.ai.enabled is False
    assert settings.trading_execution.enabled is True
    assert strategy_classes == [MTFStrategyTemplate]
    assert len(strategy_configs) == 1
    assert aggregator_config["buy_threshold"] == 0.7


def test_optional_strategy_registry_skips_missing_modules(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name in {"src.strategy.breakout_retest", "src.strategy.trend_pullback"}:
            raise ImportError(name)
        return real_import_module(name, package)

    monkeypatch.setattr("src.main.importlib.import_module", fake_import_module)

    strategy_registry = _build_strategy_registry()

    assert "breakout_retest" not in strategy_registry
    assert "trend_pullback" not in strategy_registry
    assert strategy_registry["cci_breakout"] is CCIBreakoutStrategy
    assert strategy_registry["vwap_reversion"] is VWAPReversionStrategy


def test_wire_optional_strategy_dependencies_sets_sentiment_scorer_and_leaves_macro_unwired():
    reader = MagicMock()
    engine = StrategyEngine(
        EngineConfig(
            symbols=["BTCUSDT"],
            strategy_classes=[SentimentMeanReversionStrategy, MacroVolatilityStrategy],
            strategy_configs=[{}, {}],
        ),
        reader,
    )

    _wire_optional_strategy_dependencies(engine, xai_client=object())

    strategies = engine._strategies["BTCUSDT"]  # pylint: disable=protected-access
    sentiment = next(
        strategy for strategy in strategies if isinstance(strategy, SentimentMeanReversionStrategy)
    )
    macro = next(
        strategy for strategy in strategies if isinstance(strategy, MacroVolatilityStrategy)
    )

    assert sentiment._scorer is not None  # pylint: disable=protected-access
    assert macro._event_feed is None  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_full_flow_engine_to_executor():
    """Test full flow: IndicatorReader → StrategyEngine → Signal → Executor."""
    from src.features.reader import IndicatorReader
    from src.strategy import SimpleMACrossoverStrategy

    # Mock database config
    db_config = {
        "host": "localhost",
        "port": 5432,
        "name": "test_db",
        "user": "test_user",
        "password": "test_pass",
    }

    # Mock IndicatorReader.fetch_latest to return 2 rows (warmup requirement)
    # We'll call evaluate twice to trigger crossover:
    # - First call: sets baseline (short < long)
    # - Second call: triggers crossover (short > long → BUY)
    mock_reader = MagicMock(spec=IndicatorReader)

    # First call: baseline data (short < long, no crossover yet)
    call_count = 0

    async def mock_fetch_latest(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Warmup: short < long
            return [
                {
                    "ema_12": 95.0,
                    "ema_26": 100.0,
                    "ema_50": 98.0,
                    "ema_200": 90.0,
                    "close_price": 99.0,
                },
                {
                    "ema_12": 96.0,
                    "ema_26": 100.0,
                    "ema_50": 98.0,
                    "ema_200": 90.0,
                    "close_price": 99.5,
                },
            ]
        else:
            # Crossover: short > long (BUY signal, price > ema_50 = uptrend)
            return [
                {
                    "ema_12": 96.0,
                    "ema_26": 100.0,
                    "ema_50": 98.0,
                    "ema_200": 90.0,
                    "close_price": 99.5,
                },
                {
                    "ema_12": 101.0,
                    "ema_26": 100.0,
                    "ema_50": 98.0,
                    "ema_200": 90.0,
                    "close_price": 102.0,
                },
            ]

    mock_reader.fetch_latest = mock_fetch_latest
    mock_reader.__aenter__ = AsyncMock(return_value=mock_reader)
    mock_reader.__aexit__ = AsyncMock(return_value=None)

    # Create engine config with correct list format
    engine_config = EngineConfig(
        symbols=["BTCUSDT"],
        database=db_config,
        timeframe="1m",
        evaluation_interval_seconds=60,
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs=[{"ema_short_period": 12, "ema_long_period": 26}],  # LIST not dict!
    )

    # Create engine with mock reader
    engine = StrategyEngine(config=engine_config, reader=mock_reader)

    # Mock signal handler
    received_signals = []

    async def mock_signal_handler(signal: Signal) -> None:
        received_signals.append(signal)

    # Run TWO evaluation cycles to trigger crossover
    # Cycle 1: warmup (sets baseline state)
    # Cycle 2: crossover detected (generates BUY signal)
    async with engine:
        await engine._evaluate_all(on_signal=mock_signal_handler)  # Warmup
        await engine._evaluate_all(on_signal=mock_signal_handler)  # Crossover

    # Verify signal was generated and passed to handler
    assert len(received_signals) == 1, "Should receive exactly one signal"
    assert received_signals[0].symbol == "BTCUSDT", "Signal should be for BTCUSDT"
    assert received_signals[0].type == SignalType.BUY, "Should be BUY signal (ema_12 > ema_26)"
