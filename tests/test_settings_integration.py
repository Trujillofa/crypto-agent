"""Integration tests for settings loading and defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.main import load_settings


def test_settings_default_safe():
    """Verify settings.yaml loads with safe defaults (disabled, test mode)."""
    settings = load_settings(Path("config/settings.yaml"))

    # Trading execution should be disabled by default
    assert settings.trading_execution.enabled is False, (
        "Trading should be disabled by default"
    )

    # Test mode should be enabled by default
    assert settings.trading_execution.test_mode is True, (
        "Test mode should be enabled by default"
    )

    # Strategy config should load with default interval
    assert settings.strategy.evaluation_interval_seconds == 60, (
        "Default evaluation interval should be 60 seconds"
    )


def test_settings_has_strategy_section():
    """Verify settings.yaml contains strategy section."""
    with Path("config/settings.yaml").open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    assert "strategy" in raw, "settings.yaml must contain 'strategy' section"
    assert "evaluation_interval_seconds" in raw["strategy"], (
        "strategy section must have 'evaluation_interval_seconds'"
    )


def test_settings_all_required_sections():
    """Verify all required sections present in settings.yaml."""
    settings = load_settings(Path("config/settings.yaml"))

    # All sections should be present
    assert settings.mode in ("paper", "live"), "mode must be 'paper' or 'live'"
    assert settings.log_level in ("DEBUG", "INFO", "WARNING", "ERROR"), (
        "Invalid log level"
    )
    assert len(settings.trading_pairs) > 0, "Must have at least one trading pair"
    assert settings.timeframe in ("1m", "5m", "15m", "1h"), "Invalid timeframe"
    assert settings.database is not None, "Database config required"
    assert settings.prometheus_port > 0, "Prometheus port required"
    assert settings.trading_execution is not None, "Trading execution config required"
    assert settings.strategy is not None, "Strategy config required"
