"""Tests for configuration loading in src/main.py."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.main import AISettings, _opencode_go_key, _xai_fallback_key, load_settings


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

    def test_accepts_deepseek_provider(self, config_file: Path) -> None:
        """DeepSeek may be the configured primary when xAI is not usable."""
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {"enabled": True, "provider": "deepseek", "fallback_model": "deepseek-v4-pro"}
        config_file.write_text(yaml.dump(raw))
        settings = load_settings(config_file)
        assert settings.ai.provider == "deepseek"
        assert settings.ai.fallback_model == "deepseek-v4-pro"

    def test_accepts_zai_provider(self, config_file: Path, monkeypatch) -> None:
        """Z.AI (GLM) may be the configured primary via its OpenAI-compat endpoint."""
        monkeypatch.delenv("ZAI_API_KEY", raising=False)
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {"enabled": True, "provider": "zai", "model": "glm-5.3"}
        config_file.write_text(yaml.dump(raw))
        settings = load_settings(config_file)
        assert settings.ai.provider == "zai"
        assert settings.ai.model == "glm-5.3"
        assert settings.ai.zai_base_url == "https://api.z.ai/api/paas/v4"
        assert settings.ai.zai_api_key == ""  # empty unless ZAI_API_KEY is set

    def test_loads_opencode_go_key_from_environment(self, config_file: Path, monkeypatch) -> None:
        """OpenCode Go credentials are sourced from the runtime env, not YAML."""
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-opencode-key")
        monkeypatch.delenv("OPENCODE_GO_ENABLED", raising=False)
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {"enabled": True, "provider": "zai", "model": "glm-5.3-flash"}
        config_file.write_text(yaml.dump(raw))

        settings = load_settings(config_file)

        assert settings.ai.opencode_go_api_key == "test-opencode-key"
        assert settings.ai.opencode_go_base_url == "https://opencode.ai/zen/go/v1"
        assert settings.ai.opencode_go_enabled is False

    def test_opencode_go_requires_explicit_enable_flag(
        self, config_file: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-opencode-key")
        monkeypatch.setenv("OPENCODE_GO_ENABLED", "true")
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {"enabled": True, "provider": "zai", "model": "glm-5.3-flash"}
        config_file.write_text(yaml.dump(raw))

        settings = load_settings(config_file)

        assert settings.ai.opencode_go_enabled is True

    def test_xai_fallback_is_explicitly_opt_in(self, config_file: Path, monkeypatch) -> None:
        monkeypatch.delenv("XAI_FALLBACK_ENABLED", raising=False)
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {"enabled": True, "provider": "zai", "model": "glm-5.3-flash"}
        config_file.write_text(yaml.dump(raw))

        settings = load_settings(config_file)
        assert settings.ai.xai_fallback_enabled is False

        monkeypatch.setenv("XAI_FALLBACK_ENABLED", "true")
        settings = load_settings(config_file)
        assert settings.ai.xai_fallback_enabled is True

    def test_provider_keys_are_not_used_until_explicitly_enabled(self) -> None:
        settings = AISettings(
            enabled=True,
            provider="zai",
            model="glm-5.3-flash",
            polling_interval=60,
            max_history=100,
            allowed_chat_ids=[],
            api_key="test-xai-key",
            opencode_go_api_key="test-opencode-key",
        )
        assert _xai_fallback_key(settings) == ""
        assert _opencode_go_key(settings) == ""

        enabled = replace(settings, xai_fallback_enabled=True, opencode_go_enabled=True)
        assert _xai_fallback_key(enabled) == "test-xai-key"
        assert _opencode_go_key(enabled) == "test-opencode-key"

    def test_loads_ollama_and_nvidia_credentials_from_environment(
        self, config_file: Path, monkeypatch
    ) -> None:
        """Provider secrets and routing values come from the environment, not YAML."""
        monkeypatch.setenv("OLLAMA_API_KEY", "test-ollama-key")
        monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
        monkeypatch.setenv("OLLAMA_MODEL", "gemma4:31b")
        monkeypatch.setenv("NVIDIA_MODEL", "nvidia/test-model")
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {"enabled": True, "provider": "zai", "model": "glm-5.3-flash"}
        config_file.write_text(yaml.dump(raw))

        settings = load_settings(config_file)

        assert settings.ai.ollama_api_key == "test-ollama-key"
        assert settings.ai.ollama_model == "gemma4:31b"
        assert settings.ai.ollama_base_url == "https://ollama.com/v1"
        assert settings.ai.nvidia_api_key == "test-nvidia-key"
        assert settings.ai.nvidia_model == "nvidia/test-model"
        assert settings.ai.nvidia_base_url == "https://integrate.api.nvidia.com/v1"

    def test_blank_provider_models_use_safe_defaults(self, config_file: Path, monkeypatch) -> None:
        monkeypatch.setenv("OLLAMA_MODEL", "")
        monkeypatch.setenv("NVIDIA_MODEL", "")
        monkeypatch.setenv("OLLAMA_BASE_URL", "")
        monkeypatch.setenv("NVIDIA_BASE_URL", "")
        raw = yaml.safe_load(config_file.read_text())
        raw["ai"] = {
            "enabled": True,
            "provider": "zai",
            "model": "glm-5.3-flash",
            "opencode_go_base_url": "   ",
            "fallback_base_url": " ",
            "zai_base_url": "   ",
        }
        config_file.write_text(yaml.dump(raw))

        settings = load_settings(config_file)

        assert settings.ai.ollama_model == "gemma4:31b"
        assert settings.ai.nvidia_model == "minimaxai/minimax-m3.1-flash"
        assert settings.ai.ollama_base_url == "https://ollama.com/v1"
        assert settings.ai.nvidia_base_url == "https://integrate.api.nvidia.com/v1"
        assert settings.ai.opencode_go_base_url == "https://opencode.ai/zen/go/v1"
        assert settings.ai.zai_base_url == "https://api.z.ai/api/paas/v4"
        assert settings.ai.fallback_base_url == "https://api.deepseek.com/v1"

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

    def test_allow_short_entry_defaults_false(self) -> None:
        config = {
            "trading": {"pairs": ["BTCUSDT"]},
            "database": {},
            "strategy": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config, f)
            config_path = Path(f.name)

        settings = load_settings(config_path)

        assert settings.strategy.allow_short_entry is False

        config_path.unlink()
