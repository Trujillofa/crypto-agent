"""Tests for configuration loading in src/main.py."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.main import load_settings


class TestLoadSettings:
    """Test suite for load_settings function."""

    @pytest.fixture
    def config_file(self):
        """Create a temporary config file."""
        config = {
            "mode": "paper",
            "log_level": "DEBUG",
            "trading": {
                "pairs": ["BTCUSDT", "ETHUSDT"],
                "timeframe": "1m",
            },
            "database": {
                "host": "localhost",
                "port": 5432,
            },
            "prometheus": {
                "port": 8080,
            },
            "trading_execution": {
                "enabled": True,
                "test_mode": True,
                "order_size_usdt": 50.0,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            path = Path(f.name)

        yield path

        if path.exists():
            path.unlink()

    def test_load_valid_config(self, config_file: Path) -> None:
        """Test loading a valid configuration file."""
        settings = load_settings(config_file)

        assert settings.mode == "paper"
        assert settings.log_level == "DEBUG"
        assert settings.trading_pairs == ["BTCUSDT", "ETHUSDT"]
        assert settings.timeframe == "1m"
        assert settings.prometheus_port == 8080
        assert settings.trading_execution.enabled is True
        assert settings.trading_execution.order_size_usdt == 50.0

    def test_load_defaults(self) -> None:
        """Test loading config with missing fields uses defaults."""
        config = {
            "trading": {"pairs": ["BTCUSDT"]},
            "database": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        settings = load_settings(config_path)

        assert settings.mode == "paper"  # Default
        assert settings.log_level == "INFO"  # Default
        assert settings.prometheus_port == 8000  # Default
        assert settings.trading_execution.enabled is False  # Default

        config_path.unlink()

    def test_env_var_override(self, config_file: Path) -> None:
        """Test environment variables override empty config values.

        Default config has test_mode=true, so testnet env vars are used.
        """
        with patch.dict(
            os.environ,
            {
                "BINANCE_TESTNET_API_KEY": "env_key",
                "BINANCE_TESTNET_API_SECRET": "env_secret",
                "XAI_API_KEY": "xai_env_key",
                "DEEPSEEK_API_KEY": "deepseek_env_key",
            },
        ):
            settings = load_settings(config_file)
            assert settings.trading_execution.api_key == "env_key"
            assert settings.trading_execution.api_secret == "env_secret"
            assert settings.ai.api_key == "xai_env_key"
            assert settings.ai.fallback_api_key == "deepseek_env_key"

    def test_invalid_type_raises_error(self) -> None:
        """Test that invalid types in config raise ValueError."""
        config = {
            "trading": {"pairs": "not-a-list"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        with pytest.raises(ValueError, match="Expected list of strings"):
            load_settings(config_path)

        config_path.unlink()

    def test_missing_pairs_raises_error(self) -> None:
        """Test that missing trading pairs raise ValueError."""
        config = {
            "trading": {"pairs": []},
            "database": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        with pytest.raises(ValueError, match="No trading pairs configured"):
            load_settings(config_path)

        config_path.unlink()

    def test_explicit_mirror_spot_to_futures_flag_loads(self) -> None:
        """Test explicit opt-in for spot->futures mirroring loads correctly."""
        config = {
            "trading": {"pairs": ["BTCUSDT"]},
            "database": {},
            "strategy": {
                "mirror_spot_to_futures": True,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        settings = load_settings(config_path)

        assert settings.strategy.mirror_spot_to_futures is True

        config_path.unlink()
