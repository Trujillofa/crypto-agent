from __future__ import annotations

from pathlib import Path

import yaml

from src.utils.config_doctor import Severity, analyze_config


def _base_config() -> dict[str, object]:
    return {
        "mode": "paper",
        "trading": {"pairs": ["SOLUSDT"], "timeframe": "4h"},
        "database": {"host": "localhost", "port": 5432, "name": "marketdata", "user": "trading"},
        "prometheus": {"port": 8000},
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        "ai": {
            "enabled": False,
            "provider": "xai",
            "model": "grok-4-1-fast-reasoning",
            "api_key": "",
        },
        "trading_execution": {
            "enabled": True,
            "test_mode": True,
            "order_size_usdt": 100.0,
            "use_atr_sizing": True,
            "atr_multiplier": 1.0,
            "risk_per_trade_pct": 0.02,
        },
        "strategy": {
            "default_trading_mode": "futures",
            "evaluation_interval_seconds": 14400,
            "strategies": [{"name": "rsi_reversal", "config": {}}],
            "aggregator": {
                "min_agreement": 1,
                "buy_threshold": 0.8,
                "buy_threshold_uptrend": 0.8,
                "sell_threshold": -0.6,
            },
        },
        "futures": {
            "enabled": True,
            "symbols": ["SOLUSDT"],
            "default_leverage": 3,
            "max_leverage": 10,
            "margin_mode": "isolated",
            "position_mode": "one-way",
            "test_mode": True,
            "liquidation_buffer_pct": 5.0,
        },
    }


def _write_yaml(path: Path, content: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(content, handle, sort_keys=False)


def test_detects_impossible_buy_threshold(tmp_path: Path) -> None:
    config = _base_config()
    config["strategy"]["aggregator"]["buy_threshold"] = 1.2  # type: ignore[index]

    config_path = tmp_path / "settings.invalid.yaml"
    _write_yaml(config_path, config)

    report = analyze_config(config_path)

    codes = {finding.code for finding in report.findings}
    assert "BUY_THRESHOLD_IMPOSSIBLE" in codes


def test_detects_unused_per_symbol_override(tmp_path: Path) -> None:
    config = _base_config()
    config["strategy"]["per_symbol_aggregator_config"] = {  # type: ignore[index]
        "BNBUSDT": {"buy_threshold": 0.8, "sell_threshold": -0.6, "min_agreement": 1}
    }

    config_path = tmp_path / "settings.unused.yaml"
    _write_yaml(config_path, config)

    report = analyze_config(config_path)

    codes = {finding.code for finding in report.findings}
    assert "PER_SYMBOL_UNUSED" in codes


def test_detects_futures_mode_disabled(tmp_path: Path) -> None:
    config = _base_config()
    config["futures"]["enabled"] = False  # type: ignore[index]

    config_path = tmp_path / "settings.mode-mismatch.yaml"
    _write_yaml(config_path, config)

    report = analyze_config(config_path)

    codes = {finding.code for finding in report.findings}
    assert "FUTURES_MODE_DISABLED" in codes


def test_valid_config_has_no_errors(tmp_path: Path) -> None:
    config = _base_config()
    config_path = tmp_path / "settings.valid.yaml"
    _write_yaml(config_path, config)

    report = analyze_config(config_path)

    assert all(finding.severity != Severity.ERROR for finding in report.findings)
