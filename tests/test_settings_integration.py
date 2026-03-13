"""Integration tests for settings loading and defaults."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.main import _build_strategy_registry, _resolve_strategy_config, load_settings
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
    strategy_classes, strategy_configs, _, _per_symbol = _resolve_strategy_config(settings.strategy)

    assert len(strategy_classes) == len(settings.strategy.strategies)
    assert len(strategy_configs) == len(settings.strategy.strategies)
    assert CCIBreakoutStrategy in strategy_classes
    assert VWAPReversionStrategy in strategy_classes


def test_optional_strategy_registry_skips_missing_modules(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name in {"src.strategy.sentiment_mean_reversion", "src.strategy.macro_volatility"}:
            raise ImportError(name)
        return real_import_module(name, package)

    monkeypatch.setattr("src.main.importlib.import_module", fake_import_module)

    strategy_registry = _build_strategy_registry()

    assert "sentiment_mean_reversion" not in strategy_registry
    assert "macro_volatility" not in strategy_registry
    assert strategy_registry["cci_breakout"] is CCIBreakoutStrategy
    assert strategy_registry["vwap_reversion"] is VWAPReversionStrategy


@pytest.mark.asyncio
async def test_full_flow_engine_to_executor():
    """Test full flow: IndicatorReader → StrategyEngine → Signal → Executor."""
    from src.features.reader import IndicatorReader
    from src.strategy import EngineConfig, RSIReversalStrategy, StrategyEngine

    # Mock database config
    db_config = {
        "host": "localhost",
        "port": 5432,
        "name": "test_db",
        "user": "test_user",
        "password": "test_pass",
    }

    mock_reader = MagicMock(spec=IndicatorReader)

    call_count = 0

    async def mock_fetch_latest(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Warmup: RSI in oversold territory (no crossover yet)
            return [
                {
                    "rsi_14": 22.0,
                    "ema_12": 50000.0,
                    "ema_26": 49800.0,
                    "ema_200": 48000.0,
                    "close_price": 50000.0,
                },
                {
                    "rsi_14": 25.0,
                    "ema_12": 50050.0,
                    "ema_26": 49850.0,
                    "ema_200": 48000.0,
                    "close_price": 50100.0,
                },
            ]
        else:
            # Crossover: RSI crosses above oversold (25 → 35 = BUY)
            return [
                {
                    "rsi_14": 25.0,
                    "ema_12": 50050.0,
                    "ema_26": 49850.0,
                    "ema_200": 48000.0,
                    "close_price": 50100.0,
                },
                {
                    "rsi_14": 35.0,
                    "ema_12": 50200.0,
                    "ema_26": 49900.0,
                    "ema_200": 48000.0,
                    "close_price": 50500.0,
                },
            ]

    mock_reader.fetch_latest = mock_fetch_latest
    mock_reader.__aenter__ = AsyncMock(return_value=mock_reader)
    mock_reader.__aexit__ = AsyncMock(return_value=None)

    engine_config = EngineConfig(
        symbols=["BTCUSDT"],
        database=db_config,
        timeframe="1m",
        evaluation_interval_seconds=60,
        strategy_classes=[RSIReversalStrategy],
        strategy_configs=[{"rsi_period": 14, "oversold_threshold": 30, "overbought_threshold": 70}],
    )

    engine = StrategyEngine(config=engine_config, reader=mock_reader)

    received_signals = []

    async def mock_signal_handler(signal: Signal) -> None:
        received_signals.append(signal)

    async with engine:
        await engine._evaluate_all(on_signal=mock_signal_handler)  # Warmup
        await engine._evaluate_all(on_signal=mock_signal_handler)  # Crossover

    assert len(received_signals) == 1, "Should receive exactly one signal"
    assert received_signals[0].symbol == "BTCUSDT", "Signal should be for BTCUSDT"
    assert received_signals[0].type == SignalType.BUY, "Should be BUY signal (RSI crossed above oversold)"
