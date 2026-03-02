"""Integration tests for settings loading and defaults."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.main import _resolve_strategy_config, load_settings
from src.strategy import (
    CCIBreakoutStrategy,
    Signal,
    SignalType,
    VWAPReversionStrategy,
)


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
    strategy_classes, strategy_configs, _ = _resolve_strategy_config(settings.strategy)

    assert len(strategy_classes) == len(settings.strategy.strategies)
    assert len(strategy_configs) == len(settings.strategy.strategies)
    assert CCIBreakoutStrategy in strategy_classes
    assert VWAPReversionStrategy in strategy_classes


@pytest.mark.asyncio
async def test_full_flow_engine_to_executor():
    """Test full flow: IndicatorReader → StrategyEngine → Signal → Executor."""
    from src.features.reader import IndicatorReader
    from src.strategy import EngineConfig, SimpleMACrossoverStrategy, StrategyEngine

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
