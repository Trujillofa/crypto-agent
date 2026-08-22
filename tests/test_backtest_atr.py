import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyOnceStrategy(BaseStrategy):
    def get_name(self):
        return "BuyOnce"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]
        # Buy at 100
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


class TestBacktestATR:
    @pytest.mark.asyncio
    async def test_atr_sizing_logic(self):
        reader = IndicatorReader({})
        reader._connected = True

        data = [
            {"time": "2023-01-01T00:00:00", "close_price": 100.0, "atr_14": 2.0},
        ]

        async def _mock_fetch(*args):
            return data

        reader.fetch_range = _mock_fetch

        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1m",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=10000.0,
            fee_rate=0.0,
            use_atr_sizing=True,
            risk_per_trade=0.02,  # 2% = $200
            atr_multiplier=2.0,  # Stop = 4.0
            strategy_classes=[BuyOnceStrategy],
            aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
        )

        engine = BacktestEngine(config, reader)

        # Add a second candle to close it
        data.append({"time": "2023-01-01T00:01:00", "close_price": 105.0, "atr_14": 2.0})

        result = await engine.run()

        assert len(result.trades) == 1
        trade = result.trades[0]

        # Risk = 200. Stop = 4.0. Qty = 50.
        assert trade.quantity == pytest.approx(50.0)
