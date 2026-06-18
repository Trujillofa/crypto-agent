"""Backtest engine default cost and funding-cadence correctness."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.backtest.cost_overrides import (
    LEGACY_FEE_RATE,
    LEGACY_SLIPPAGE_PCT,
    REALISTIC_FEE_RATE,
    REALISTIC_SLIPPAGE_PCT,
    effective_futures_funding_rate_per_bar,
)
from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.sentiment_mean_reversion import SentimentMeanReversionStrategy
from src.strategy.signals import Signal, SignalType


class BuyOnceStrategy(BaseStrategy):
    """Minimal strategy used only to hold a futures position open."""

    def get_name(self) -> str:
        return "BuyOnce"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


def _build_reader(data: list[dict[str, object]]) -> IndicatorReader:
    reader = IndicatorReader({})
    reader._connected = True

    async def _mock_fetch(*_args: object) -> list[dict[str, object]]:
        return data

    reader.fetch_range = _mock_fetch
    return reader


def test_backtest_config_default_round_trip_cost_pct() -> None:
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1h",
        start_date="2023-01-01",
        end_date="2023-01-02",
    )
    round_trip = 2.0 * (config.fee_rate + config.slippage_pct) * 100.0
    assert config.fee_rate == REALISTIC_FEE_RATE == 0.0004
    assert config.slippage_pct == REALISTIC_SLIPPAGE_PCT == 0.0002
    assert round_trip == pytest.approx(0.12)


def test_backtest_config_overrides_still_win() -> None:
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1h",
        start_date="2023-01-01",
        end_date="2023-01-02",
        fee_rate=LEGACY_FEE_RATE,
        slippage_pct=LEGACY_SLIPPAGE_PCT,
        funding_cadence="per_bar",
        futures_funding_rate=0.0002,
    )
    assert config.fee_rate == 0.001
    assert config.slippage_pct == 0.001
    assert config.funding_cadence == "per_bar"
    assert config.futures_funding_rate == 0.0002


def test_resolved_cost_audit_logs_new_defaults() -> None:
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2023-01-01",
        end_date="2023-01-02",
        futures_mode=True,
    )
    engine = BacktestEngine(config, IndicatorReader({}))
    audit = engine._resolved_cost_audit()

    assert audit["round_trip_cost_pct"] == pytest.approx(0.12)
    assert audit["funding_cadence"] == "scaled_8h"
    assert audit["effective_futures_funding_rate_per_bar"] == pytest.approx(0.0001 / 8.0)


@pytest.mark.asyncio
async def test_funding_scaled_8h_is_one_eighth_of_legacy_per_bar_over_1h_window() -> None:
    """24 open 1h bars: scaled cadence should charge 1/8 of legacy per-bar total."""
    start = datetime(2023, 1, 1, 0, 0, tzinfo=UTC)
    data = [
        {
            "time": (start + timedelta(hours=idx)).isoformat(),
            "close_price": 100.0,
            "atr_14": 10.0,
        }
        for idx in range(25)
    ]
    base_kwargs = {
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "start_date": "2023-01-01",
        "end_date": "2023-01-03",
        "initial_capital": 10_000.0,
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
        "use_atr_sizing": True,
        "risk_per_trade": 0.02,
        "atr_multiplier": 2.0,
        "futures_mode": True,
        "futures_leverage": 5,
        "futures_funding_rate": 0.0001,
        "apply_global_trend_filter": False,
        "strategy_classes": [BuyOnceStrategy],
        "aggregator_config": {"min_agreement": 1, "buy_threshold": 0.5},
    }

    legacy_engine = BacktestEngine(
        BacktestConfig(**base_kwargs, funding_cadence="per_bar"),
        _build_reader(data),
    )
    scaled_engine = BacktestEngine(
        BacktestConfig(**base_kwargs, funding_cadence="scaled_8h"),
        _build_reader(data),
    )

    legacy_result = await legacy_engine.run()
    scaled_result = await scaled_engine.run()

    assert legacy_result.total_trades == 1
    assert scaled_result.total_trades == 1
    assert scaled_result.trades[0].pnl == pytest.approx(legacy_result.trades[0].pnl / 8.0, rel=1e-6)


def test_effective_futures_funding_rate_per_bar_matches_cost_profile_helper() -> None:
    assert effective_futures_funding_rate_per_bar(
        0.0001, "1h", cadence="scaled_8h"
    ) == pytest.approx(0.0001 / 8.0)
    assert effective_futures_funding_rate_per_bar(0.0001, "1h", cadence="per_bar") == 0.0001


@pytest.mark.asyncio
async def test_sentiment_macro_replay_path_runs_with_new_cost_defaults(
    tmp_path: Path,
) -> None:
    """Regression: sentiment replay wiring still completes under corrected engine defaults."""
    replay_log = tmp_path / "sentiment_replay.jsonl"
    replay_log.write_text(
        json.dumps(
            {
                "type": "sentiment_score",
                "ts": "2023-01-01T00:00:00+00:00",
                "payload": {"symbol": "SOLUSDT", "score": 55.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    data = [
        {
            "time": "2023-01-01T00:00:00+00:00",
            "close_price": 100.0,
            "rsi_14": 30.0,
            "bb_lower_dist": 0.001,
            "atr_pct": 0.01,
            "ema_200": 90.0,
        },
        {
            "time": "2023-01-01T01:00:00+00:00",
            "close_price": 101.0,
            "rsi_14": 50.0,
            "bb_lower_dist": 0.05,
            "atr_pct": 0.01,
            "ema_200": 90.0,
        },
    ]
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=1000.0,
        futures_mode=True,
        futures_leverage=3,
        fixed_notional_usdt=22.0,
        apply_global_trend_filter=False,
        replay_sentiment_path=str(replay_log),
        replay_sentiment_max_age_seconds=24 * 3600,
        strategy_classes=[SentimentMeanReversionStrategy],
        strategy_configs=[
            {
                "rsi_oversold": 35.0,
                "rsi_overbought": 65.0,
                "bb_distance_threshold": 0.005,
                "sentiment_gate_threshold": 35.0,
                "sentiment_panic_threshold": 20.0,
                "sentiment_boost_threshold": 65.0,
                "volatility_regime_filter": False,
            }
        ],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    )

    result = await BacktestEngine(config, _build_reader(data)).run()

    assert result.total_trades >= 0
    assert result.final_equity > 0
