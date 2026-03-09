from __future__ import annotations

import pytest

from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.engine import EngineConfig, StrategyEngine
from src.strategy.signals import Signal, SignalType


class MockStrategy(BaseStrategy):
    def __init__(self, config=None):
        super().__init__(config)
        self.signal_to_emit = SignalType.HOLD
        self.confidence = 0.0

    async def evaluate(self, symbol, indicators):
        return Signal(
            type=self.signal_to_emit,
            symbol=symbol,
            price=indicators["close_price"],
            confidence=self.confidence,
            reason="Mock Reason",
            indicators=indicators,
        )

    def get_name(self):
        return "MockStrategy"


class TestStrategyEngineIntegration:
    @pytest.fixture
    def mock_reader(self):
        reader = IndicatorReader({})
        reader._connected = True

        async def _mock_fetch_rows(s, t, limit):
            return [
                {
                    "close_price": 100.0,
                    "ema_12": 100.0,
                    "ema_26": 99.0,
                    "ema_200": 90.0,
                },
                {
                    "close_price": 101.0,
                    "ema_12": 101.0,
                    "ema_26": 100.0,
                    "ema_200": 90.0,
                },
            ]

        reader._fetch_rows = _mock_fetch_rows
        return reader

    @pytest.mark.asyncio
    async def test_aggregation_flow(self, mock_reader):
        config = EngineConfig(
            symbols=["BTCUSDT"],
            strategy_classes=[MockStrategy, MockStrategy],
            aggregator_config={
                "buy_threshold": 1.5,
                "min_agreement": 2,
            },
        )

        engine = StrategyEngine(config, mock_reader)

        strat1 = engine._strategies["BTCUSDT"][0]
        strat2 = engine._strategies["BTCUSDT"][1]

        strat1.signal_to_emit = SignalType.BUY
        strat1.confidence = 0.9

        strat2.signal_to_emit = SignalType.BUY
        strat2.confidence = 0.8

        received_signals = []

        async def on_signal(sig):
            received_signals.append(sig)

        await engine._evaluate_all(on_signal)

        assert len(received_signals) == 1
        final_sig = received_signals[0]
        assert final_sig.type == SignalType.BUY
        assert final_sig.confidence == 1.0
        assert "Consensus BUY" in final_sig.reason

    @pytest.fixture
    def below_ema200_reader(self):
        reader = IndicatorReader({})
        reader._connected = True

        async def _mock_fetch_rows(symbol, timeframe, limit):
            return [
                {
                    "close_price": 640.0,
                    "ema_12": 640.0,
                    "ema_26": 638.0,
                    "ema_200": 700.0,
                },
                {
                    "close_price": 650.0,
                    "ema_12": 650.0,
                    "ema_26": 640.0,
                    "ema_200": 700.0,
                },
            ]

        reader._fetch_rows = _mock_fetch_rows
        return reader

    @pytest.mark.asyncio
    async def test_logs_hold_reason_when_buy_is_blocked_by_global_trend_filter(
        self, below_ema200_reader, caplog
    ):
        config = EngineConfig(
            symbols=["BNBUSDT"],
            timeframe="4h",
            strategy_classes=[MockStrategy],
            aggregator_config={
                "buy_threshold": 0.8,
                "min_agreement": 1,
            },
        )

        engine = StrategyEngine(config, below_ema200_reader)
        strategy = engine._strategies["BNBUSDT"][0]
        strategy.signal_to_emit = SignalType.BUY
        strategy.confidence = 0.9

        received_signals = []

        async def on_signal(sig):
            received_signals.append(sig)

        with caplog.at_level("INFO"):
            await engine._evaluate_all(on_signal)

        assert received_signals == []
        assert "Blocked by Global Trend Filter (Price < EMA200) for BNBUSDT" in caplog.text
        assert (
            "Consensus HOLD for BNBUSDT: Blocked by Global Trend Filter (Price < EMA200)"
            in caplog.text
        )

    @pytest.mark.asyncio
    async def test_global_trend_filter_disabled_allows_buy_below_ema200(self, below_ema200_reader):
        config = EngineConfig(
            symbols=["BNBUSDT"],
            timeframe="4h",
            strategy_classes=[MockStrategy],
            aggregator_config={
                "buy_threshold": 0.8,
                "min_agreement": 1,
            },
            global_trend_filter_enabled=False,
        )

        engine = StrategyEngine(config, below_ema200_reader)
        strategy = engine._strategies["BNBUSDT"][0]
        strategy.signal_to_emit = SignalType.BUY
        strategy.confidence = 0.9

        received_signals = []

        async def on_signal(sig):
            received_signals.append(sig)

        await engine._evaluate_all(on_signal)

        assert len(received_signals) == 1
        assert received_signals[0].type == SignalType.BUY
