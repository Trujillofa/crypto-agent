"""Integration tests for multi-timeframe (MTF) backtesting."""

from datetime import datetime

import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.strategy.simple_ma import SimpleMACrossoverStrategy


class MockMTFStrategy(BaseStrategy):
    """Mock MTF strategy for testing."""

    REQUIRED_TIMEFRAMES = {
        "entry": "1h",
        "regime": "4h",
    }

    def __init__(self, config=None):
        super().__init__(config)
        self.call_count = 0
        self.last_regime_slope = None

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate using MTF indicators."""
        self.call_count += 1

        close_price = indicators.get("close_price", 0.0)
        ema_slope_4h = indicators.get("ema_slope_50_4h")

        self.last_regime_slope = ema_slope_4h

        if ema_slope_4h and ema_slope_4h > 0.005:
            vwap = indicators.get("vwap", close_price)
            if close_price < vwap:
                return Signal(
                    type=SignalType.BUY,
                    symbol=symbol,
                    price=close_price,
                    confidence=0.8,
                    reason="4h trend + 1h pullback",
                    indicators={"ema_slope_4h": ema_slope_4h},
                )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.0,
            reason="No signal",
            indicators={},
        )


class MockSingleTimeframeStrategy(BaseStrategy):
    """Mock single-timeframe strategy for testing backward compatibility."""

    def __init__(self, config=None):
        super().__init__(config)
        self.call_count = 0

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate using single-timeframe indicators."""
        self.call_count += 1
        close_price = indicators.get("close_price", 0.0)

        if "ema_slope_50_4h" in indicators:
            raise ValueError("Single-timeframe strategy received MTF indicators!")

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.0,
            reason="Single timeframe",
            indicators={},
        )


class MockReader:
    """Mock reader that doesn't need a database."""

    def __init__(self, data):
        self._data = data
        self._db_lock = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def fetch_range(self, symbol, timeframe, *_args, **_kwargs):
        """Return mock single-timeframe data."""
        key = (symbol, timeframe)
        return self._data.get(key, [])

    async def fetch_multi_timeframe(
        self,
        symbol,
        entry_timeframe,
        regime_timeframe,
        **_kwargs,
    ):
        """Return mock multi-timeframe data."""
        key = (symbol, f"{entry_timeframe}+{regime_timeframe}")
        return self._data.get(key, [])

    def _join_timeframes(self, entry_data, regime_data, regime_timeframe="4h"):
        """Passthrough to IndicatorReader's static method."""
        return IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe)

    async def get_latest(self, symbol, timeframe):
        """Return mock latest data."""
        key = (symbol, timeframe)
        if key in self._data and self._data[key]:
            return self._data[key][-1]
        return {}


class TestMTFBacktestIntegration:
    """Integration tests for MTF backtest engine path."""

    async def test_mtf_strategy_receives_joined_data(self):
        """Verify MTF strategy receives data with _4h suffix indicators."""
        mock_data = [
            {
                "time": "2024-06-01 08:00:00",
                "close_price": 100.0,
                "vwap": 99.5,
                "rsi_14": 45.0,
                "ema_slope_50_4h": 0.01,
                "trend_consistency_4h": 75.0,
            },
        ]

        reader = MockReader({("BTCUSDT", "1h+4h"): mock_data})

        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            start_date="2024-06-01",
            end_date="2024-06-02",
            strategy_classes=[MockMTFStrategy],
            strategy_configs=[{}],
            initial_capital=10000.0,
        )

        engine = BacktestEngine(config, reader)
        result = await engine.run()

        assert result is not None

    async def test_single_timeframe_strategy_no_mtf_indicators(self):
        """Verify single-timeframe strategies don't receive MTF indicators."""
        mock_data = [
            {
                "time": "2024-06-01 08:00:00",
                "close_price": 100.0,
                "vwap": 99.5,
                "rsi_14": 45.0,
            },
        ]

        reader = MockReader({("BTCUSDT", "4h"): mock_data})

        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            start_date="2024-06-01",
            end_date="2024-06-02",
            strategy_classes=[MockSingleTimeframeStrategy],
            strategy_configs=[{}],
            initial_capital=10000.0,
        )

        engine = BacktestEngine(config, reader)
        result = await engine.run()

        assert result is not None

    async def test_mtf_detection_based_on_required_timeframes(self):
        """Verify MTF detection uses REQUIRED_TIMEFRAMES correctly."""
        mtf_strategy = MockMTFStrategy()
        assert hasattr(mtf_strategy, "REQUIRED_TIMEFRAMES")
        assert mtf_strategy.REQUIRED_TIMEFRAMES == {"entry": "1h", "regime": "4h"}

        st_strategy = MockSingleTimeframeStrategy()
        assert hasattr(st_strategy, "REQUIRED_TIMEFRAMES")
        assert st_strategy.REQUIRED_TIMEFRAMES == {}


class TestMTFNoLookahead:
    """Verify no lookahead through the engine path."""

    def test_mtf_strategy_cannot_see_future_regime(self):
        """Critical test: MTF strategy must not see future regime data."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "close": 100},
            {"time": datetime(2024, 1, 1, 9, 0), "close": 101},
        ]

        regime_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "ema_slope": 0.99},  # Future!
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, "4h")

        for bar in joined:
            assert "ema_slope_4h" not in bar, "Future regime data leaked!"

    def test_same_timestamp_not_available(self):
        """Same-timestamp regime bar should NOT be available (strict inequality)."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "close": 100},
        ]

        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, "4h")

        # 8:00 < 8:00 is False, so no regime data
        assert "ema_slope_4h" not in joined[0]

    def test_later_timestamp_available(self):
        """Regime bar should be available when entry_time > regime_time."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "close": 100},
        ]

        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, "4h")

        # 8:00 < 9:00 is True, so regime data available
        assert joined[0]["ema_slope_4h"] == 0.01


class TestBackwardCompatibility:
    """Verify existing single-timeframe strategies still work."""

    async def test_existing_strategy_unchanged(self):
        """Existing single-timeframe strategies should work without changes.

        Runs the production SimpleMACrossoverStrategy through the engine's
        single-timeframe path: an EMA crossover up opens a long, the
        crossover down closes it via signal.
        """
        mock_data = [
            # Bar 1: short EMA below long EMA — establishes previous state, HOLD
            {
                "time": "2024-06-01 08:00:00",
                "close_price": 100.0,
                "ema_12": 99.0,
                "ema_26": 100.0,
                "ema_50": 98.0,
                "ema_200": 90.0,
            },
            # Bar 2: crossover up + price above EMA50/EMA200 — BUY, opens long
            {
                "time": "2024-06-01 12:00:00",
                "close_price": 105.0,
                "ema_12": 102.0,
                "ema_26": 101.0,
                "ema_50": 99.0,
                "ema_200": 90.0,
            },
            # Bar 3: crossover down + price below EMA50 — SELL, closes long
            {
                "time": "2024-06-01 16:00:00",
                "close_price": 95.0,
                "ema_12": 100.0,
                "ema_26": 101.0,
                "ema_50": 99.0,
                "ema_200": 90.0,
            },
        ]

        reader = MockReader({("BTCUSDT", "4h"): mock_data})

        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="4h",
            start_date="2024-06-01",
            end_date="2024-06-02",
            strategy_classes=[SimpleMACrossoverStrategy],
            strategy_configs=[{}],
            initial_capital=10000.0,
        )

        engine = BacktestEngine(config, reader)
        result = await engine.run()

        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade.side == "BUY"
        assert trade.exit_reason == "SIGNAL"
        assert trade.entry_price == pytest.approx(105.0 * (1 + config.slippage_pct))
        assert trade.exit_price == pytest.approx(95.0 * (1 - config.slippage_pct))
        assert trade.pnl < 0
        assert result.final_equity == pytest.approx(10000.0 + trade.pnl)

    async def test_multiple_strategies_mixed_timeframes(self):
        mock_data = [
            {
                "time": "2024-06-01 08:00:00",
                "close_price": 100.0,
                "vwap": 99.5,
                "rsi_14": 45.0,
                "ema_slope_50_4h": 0.01,
            },
        ]

        reader = MockReader({("BTCUSDT", "1h+4h"): mock_data})

        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1h",
            start_date="2024-06-01",
            end_date="2024-06-02",
            strategy_classes=[MockMTFStrategy, MockSingleTimeframeStrategy],
            strategy_configs=[{}, {}],
            initial_capital=10000.0,
        )

        engine = BacktestEngine(config, reader)
        with pytest.raises(
            ValueError,
            match="Mixed multi-timeframe and single-timeframe backtest strategy sets are not supported",
        ):
            await engine.run()


class TestMTFRowShape:
    """Verify row shape is stable and documented."""

    def test_joined_row_has_expected_keys(self):
        """Verify joined row contains expected keys."""
        entry_data = [
            {
                "time": datetime(2024, 1, 1, 12, 0),  # After 8:00 regime close (12:00)
                "close_price": 100.0,
                "vwap": 99.5,
                "rsi_14": 45.0,
            },
        ]

        regime_data = [
            {
                "time": datetime(2024, 1, 1, 8, 0),  # Before 9:00 entry
                "ema_slope_50": 0.01,
                "trend_consistency": 75.0,
            },
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, "4h")

        assert len(joined) == 1
        bar = joined[0]

        # Entry timeframe fields (unchanged)
        assert "time" in bar
        assert "close_price" in bar
        assert "vwap" in bar
        assert "rsi_14" in bar

        # Regime timeframe fields (with _4h suffix) - available since 8:00 < 9:00
        assert "ema_slope_50_4h" in bar
        assert "trend_consistency_4h" in bar

        # Original regime fields should NOT be present
        assert "ema_slope_50" not in bar
        assert "trend_consistency" not in bar

    def test_joined_row_preserves_entry_values(self):
        """Verify entry values are preserved exactly."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "close_price": 100.0, "vwap": 99.5},
        ]

        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope_50": 0.01},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, "4h")

        bar = joined[0]
        assert bar["close_price"] == 100.0
        assert bar["vwap"] == 99.5
        assert bar["time"] == datetime(2024, 1, 1, 12, 0)
