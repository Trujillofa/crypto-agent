import pytest
from pathlib import Path
import yaml
from src.main import load_settings

def test_settings_default_trading_mode_spot():
    """Verify default_trading_mode defaults to 'spot'."""
    # We use the actual settings.yaml which we just updated to 'spot'
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.strategy.default_trading_mode == "spot"

def test_settings_invalid_trading_mode(tmp_path):
    """Verify that an invalid trading_mode raises ValueError."""
    config_file = tmp_path / "invalid_settings.yaml"
    
    # Load base config
    with open("config/settings.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    # Set invalid trading mode
    config["strategy"]["default_trading_mode"] = "invalid_mode"
    
    with open(config_file, "w") as f:
        yaml.dump(config, f)
        
    with pytest.raises(ValueError, match="is invalid. Must be 'spot' or 'futures'"):
        load_settings(config_file)

def test_settings_futures_trading_mode(tmp_path):
    """Verify that 'futures' is a valid trading_mode."""
    config_file = tmp_path / "futures_settings.yaml"
    
    # Load base config
    with open("config/settings.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    # Set futures trading mode
    config["strategy"]["default_trading_mode"] = "futures"
    
    with open(config_file, "w") as f:
        yaml.dump(config, f)
        
    settings = load_settings(config_file)
    assert settings.strategy.default_trading_mode == "futures"
