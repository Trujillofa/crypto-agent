"""Tests for futures configuration loading and validation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml

from src.main import load_settings


class TestFuturesConfig:
    """Test suite for futures configuration validation."""

    @pytest.fixture
    def base_config(self):
        """Create a base config with futures disabled."""
        return {
            "mode": "paper",
            "log_level": "INFO",
            "trading": {
                "pairs": ["BTCUSDT", "ETHUSDT"],
                "timeframe": "1m",
            },
            "database": {
                "host": "localhost",
                "port": 5432,
                "name": "marketdata",
                "user": "trading",
                "password": "test",
            },
            "prometheus": {
                "port": 8000,
            },
            "trading_execution": {
                "enabled": True,
                "test_mode": True,
                "order_size_usdt": 100.0,
            },
            "strategy": {
                "evaluation_interval_seconds": 60,
            },
            "telegram": {
                "enabled": False,
            },
            "ingest": {
                "use_websocket": False,
            },
        }

    def test_futures_default_disabled(self, base_config, tmp_path):
        """Test that futures is disabled by default."""
        base_config["futures"] = {"enabled": False}

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        settings = load_settings(config_path)

        # Should load without errors
        assert settings.mode == "paper"
        assert settings.trading_pairs == ["BTCUSDT", "ETHUSDT"]

    def test_futures_enabled_valid_config(self, base_config, tmp_path, monkeypatch):
        """Test loading valid futures configuration."""
        logger = MagicMock()
        monkeypatch.setattr("src.main.get_logger", lambda _name: logger)
        base_config["futures"] = {
            "enabled": True,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "default_leverage": 5,
            "max_leverage": 10,
            "margin_mode": "isolated",
            "position_mode": "one-way",
            "test_mode": True,
            "liquidation_buffer_pct": 5.0,
        }

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        settings = load_settings(config_path)

        # Should load without errors
        assert settings.mode == "paper"
        assert settings.trading_execution.test_mode is True
        assert "leverage=5x, max_leverage=10x" in logger.info.call_args.args[0]

    def test_futures_max_leverage_enforced_rejects_50x(self, base_config, tmp_path):
        """Test that leverage > 20x is rejected."""
        base_config["futures"] = {
            "enabled": True,
            "symbols": ["BTCUSDT"],
            "default_leverage": 5,
            "max_leverage": 50,  # Exceeds 20x hard cap
            "margin_mode": "isolated",
            "position_mode": "one-way",
        }

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        with pytest.raises(ValueError) as exc_info:
            load_settings(config_path)

        assert "exceeds hard safety cap of 20x" in str(exc_info.value)

    def test_futures_isolated_only_rejects_cross_margin(self, base_config, tmp_path):
        """Test that cross margin is rejected for MVP."""
        base_config["futures"] = {
            "enabled": True,
            "symbols": ["BTCUSDT"],
            "default_leverage": 5,
            "max_leverage": 10,
            "margin_mode": "cross",  # Not supported in MVP
            "position_mode": "one-way",
        }

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        with pytest.raises(ValueError) as exc_info:
            load_settings(config_path)

        assert "requires 'isolated' margin" in str(exc_info.value)

    def test_futures_one_way_only_rejects_hedge_mode(self, base_config, tmp_path):
        """Test that hedge mode is rejected for MVP."""
        base_config["futures"] = {
            "enabled": True,
            "symbols": ["BTCUSDT"],
            "default_leverage": 5,
            "max_leverage": 10,
            "margin_mode": "isolated",
            "position_mode": "hedge",  # Not supported in MVP
        }

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        with pytest.raises(ValueError) as exc_info:
            load_settings(config_path)

        assert "requires 'one-way' mode" in str(exc_info.value)

    def test_futures_exactly_20x_allowed(self, base_config, tmp_path):
        """Test that exactly 20x leverage is allowed (at the cap)."""
        base_config["futures"] = {
            "enabled": True,
            "symbols": ["BTCUSDT"],
            "default_leverage": 5,
            "max_leverage": 20,  # At the hard cap
            "margin_mode": "isolated",
            "position_mode": "one-way",
        }

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        # Should not raise
        settings = load_settings(config_path)
        assert settings is not None

    def test_spot_config_unchanged_without_futures(self, base_config, tmp_path):
        """Test that spot trading works normally without futures section."""
        # Don't add futures section

        config_path = tmp_path / "test_config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        settings = load_settings(config_path)

        # Spot configuration should work unchanged
        assert settings.mode == "paper"
        assert settings.trading_pairs == ["BTCUSDT", "ETHUSDT"]
        assert settings.trading_execution.order_size_usdt == 100.0
