from __future__ import annotations

from pathlib import Path

import yaml

from src.main import _deep_merge

_ORIGINAL_SETTINGS = """
mode: paper
agent_id: default
display_name: sol-4h-simple-ma
log_level: INFO

trading:
  pairs:
    - SOLUSDT
  timeframe: 4h

ingest:
  use_websocket: true

database:
  host: timescaledb
  port: 5432
  name: marketdata
  user: trading
  password: ""

telegram:
  enabled: true
  daily_summary_enabled: false
  daily_summary_send_empty: false
  bot_token: ""
  chat_id: ""
  rate_limit_seconds: 5
  allowed_updates:
    - message

ai:
  enabled: false
  provider: xai
  model: grok-4-1-fast-reasoning
  polling_interval: 1.0
  max_history: 10
  allowed_chat_ids: []
  api_key: ""

prometheus:
  port: 8000

trading_execution:
  enabled: true
  api_key: ""
  api_secret: ""
  test_mode: true
  order_size_usdt: 100.0
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  sl_atr_multiplier: 1.0
  tp_atr_multiplier: 3.0
  trailing_activate_atr: 2.5
  trailing_offset_atr: 1.5
  use_atr_sizing: true
  atr_multiplier: 1.0
  risk_per_trade_pct: 0.02
  exit_rules:
    time_stop_minutes: 240

futures:
  enabled: true
  symbols:
    - SOLUSDT
  default_leverage: 3
  max_leverage: 10
  margin_mode: isolated
  position_mode: one-way
  test_mode: true
  liquidation_buffer_pct: 5.0

strategy:
  evaluation_interval_seconds: 14400
  cooldown_candles: 1
  default_trading_mode: futures
  strategies:
    - name: simple_ma
      config: {}
  global_trend_filter_enabled: true
  global_trend_filter_buffer_pct: 0.05
  aggregator:
    min_agreement: 1
    buy_threshold: 0.5
    buy_threshold_uptrend: 0.5
    sell_threshold: -0.5
    min_confidence: 0.0
    btc_regime_filter_enabled: true
    btc_reference_symbol: BTCUSDT
    btc_dump_threshold_pct: -1.0
    btc_dump_require_below_ema200: true

reconciliation:
  enabled: true
  on_divergence: alert
  quantity_tolerance_pct: 1.0
  periodic_interval_seconds: 0
  dust_threshold_usdt: 1.0
"""

_ORIGINAL_BTC_4H = """
mode: paper
agent_id: btc-4h
display_name: btc-4h-simple-ma
log_level: INFO

trading:
  pairs:
    - BTCUSDT
  timeframe: 4h

ingest:
  use_websocket: true

database:
  host: timescaledb
  port: 5432
  name: marketdata
  user: trading
  password: ""

telegram:
  enabled: true
  daily_summary_enabled: false
  daily_summary_send_empty: false
  bot_token: ""
  chat_id: ""
  rate_limit_seconds: 5
  allowed_updates:
    - message

ai:
  enabled: false
  provider: xai
  model: grok-4-1-fast-reasoning
  polling_interval: 1.0
  max_history: 10
  allowed_chat_ids: []
  api_key: ""

prometheus:
  port: 8000

trading_execution:
  enabled: true
  api_key: ""
  api_secret: ""
  test_mode: true
  order_size_usdt: 100.0
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  sl_atr_multiplier: 2.5
  tp_atr_multiplier: 3.0
  trailing_activate_atr: 2.5
  trailing_offset_atr: 1.5
  use_atr_sizing: true
  atr_multiplier: 1.0
  risk_per_trade_pct: 0.02
  exit_rules:
    time_stop_minutes: 240

futures:
  enabled: true
  symbols:
    - BTCUSDT
  default_leverage: 3
  max_leverage: 10
  margin_mode: isolated
  position_mode: one-way
  test_mode: true
  liquidation_buffer_pct: 5.0

strategy:
  evaluation_interval_seconds: 14400
  cooldown_candles: 1
  default_trading_mode: futures
  strategies:
    - name: simple_ma
      config: {}
  global_trend_filter_enabled: true
  global_trend_filter_buffer_pct: 0.05
  aggregator:
    min_agreement: 1
    buy_threshold: 0.5
    buy_threshold_uptrend: 0.5
    sell_threshold: -0.5
    min_confidence: 0.0
    btc_regime_filter_enabled: false

reconciliation:
  enabled: true
  on_divergence: alert
  quantity_tolerance_pct: 1.0
  periodic_interval_seconds: 0
  dust_threshold_usdt: 1.0
"""

_ORIGINAL_SENTIMENT_MACRO = """
mode: paper
agent_id: sentiment-macro-bot
display_name: sentiment-macro-1h-multiasset
log_level: INFO

trading:
  pairs:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
  timeframe: 1h

ingest:
  use_websocket: true

database:
  host: timescaledb
  port: 5432
  name: marketdata
  user: trading
  password: ""

telegram:
  enabled: true
  daily_summary_enabled: true
  daily_summary_send_empty: false
  bot_token: ""
  chat_id: ""
  rate_limit_seconds: 5
  allowed_updates:
    - message

ai:
  enabled: true
  provider: xai
  model: grok-4-1-fast-reasoning
  polling_interval: 60.0
  max_history: 10
  allowed_chat_ids: []
  api_key: ""

prometheus:
  port: 8000

trading_execution:
  enabled: true
  api_key: ""
  api_secret: ""
  test_mode: true
  order_size_usdt: 100.0
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
  sl_atr_multiplier: 2.0
  tp_atr_multiplier: 4.5
  trailing_activate_atr: 1.5
  trailing_offset_atr: 1.0
  use_atr_sizing: true
  atr_multiplier: 1.0
  risk_per_trade_pct: 0.02
  exit_rules:
    time_stop_minutes: 1440

futures:
  enabled: true
  symbols:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
  default_leverage: 3
  max_leverage: 10
  margin_mode: isolated
  position_mode: one-way
  test_mode: true
  liquidation_buffer_pct: 5.0

strategy:
  evaluation_interval_seconds: 3600
  cooldown_candles: 1
  default_trading_mode: futures
  strategies:
    - name: sentiment_mean_reversion
      config:
        rsi_oversold: 35.0
        rsi_overbought: 65.0
        bb_distance_threshold: 0.005
        sentiment_gate_threshold: 35.0
        sentiment_panic_threshold: 20.0
        sentiment_boost_threshold: 65.0
  global_trend_filter_enabled: true
  global_trend_filter_buffer_pct: 0.0
  aggregator:
    min_agreement: 1
    buy_threshold: 0.6
    sell_threshold: -0.6
"""


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as file_handle:
        return yaml.safe_load(file_handle) or {}


def _load_yaml_string(raw: str) -> dict[str, object]:
    return yaml.safe_load(raw) or {}


def _assert_mapping_contains(actual: dict[str, object], expected: dict[str, object]) -> None:
    for key, expected_value in expected.items():
        assert key in actual
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            assert isinstance(actual_value, dict)
            _assert_mapping_contains(actual_value, expected_value)
        else:
            assert actual_value == expected_value


def test_merge_preserves_original_settings_yaml() -> None:
    base = _load_yaml(Path("config/base.yaml"))
    overlay = _load_yaml(Path("config/settings.yaml"))

    merged = _deep_merge(base, overlay)

    assert merged == _load_yaml_string(_ORIGINAL_SETTINGS)


def test_merge_preserves_original_btc_4h_yaml() -> None:
    base = _load_yaml(Path("config/base.yaml"))
    overlay = _load_yaml(Path("config/settings.btc-4h.yaml"))

    merged = _deep_merge(base, overlay)

    assert merged == _load_yaml_string(_ORIGINAL_BTC_4H)


def test_merge_preserves_original_sentiment_macro_yaml() -> None:
    base = _load_yaml(Path("config/base.yaml"))
    overlay = _load_yaml(Path("config/settings.sentiment_macro.yaml"))

    merged = _deep_merge(base, overlay)

    _assert_mapping_contains(merged, _load_yaml_string(_ORIGINAL_SENTIMENT_MACRO))
