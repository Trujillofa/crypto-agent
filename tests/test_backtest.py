import pytest
from src.backtest.engine import BacktestEngine, BacktestConfig
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class AlwaysBuyStrategy(BaseStrategy):
    def get_name(self):
        return "AlwaysBuy"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]

        if price == 101.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy Trigger", indicators)
        elif price == 103.0:
            return Signal(
                SignalType.SELL, symbol, price, 1.0, "Sell Trigger", indicators
            )

        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


class TestBacktestEngine:
    @pytest.fixture
    def mock_reader(self):
        reader = IndicatorReader({})
        reader._connected = True

        data = [
            {"time": "2023-01-01T00:00:00", "close_price": 100.0, "ema_12": 100.0},
            {"time": "2023-01-01T00:01:00", "close_price": 101.0, "ema_12": 100.0},
            {"time": "2023-01-01T00:02:00", "close_price": 102.0, "ema_12": 100.0},
            {"time": "2023-01-01T00:03:00", "close_price": 103.0, "ema_12": 100.0},
            {"time": "2023-01-01T00:04:00", "close_price": 104.0, "ema_12": 100.0},
        ]

        async def _mock_fetch(*args):
            return data

        reader.fetch_range = _mock_fetch
        return reader

    @pytest.mark.asyncio
    async def test_simple_trade_cycle(self, mock_reader):
        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1m",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=10000.0,
            fee_rate=0.0,
            strategy_classes=[AlwaysBuyStrategy],
            aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
        )

        engine = BacktestEngine(config, mock_reader)
        result = await engine.run()

        assert result.total_trades == 1
        trade = result.trades[0]

        assert trade.entry_price == 101.0
        assert trade.exit_price == 103.0
        assert trade.side == "BUY"
        assert result.win_rate == 100.0
        assert result.total_return > 0

    @pytest.mark.asyncio
    async def test_fee_impact(self, mock_reader):
        config = BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1m",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=10000.0,
            fee_rate=0.01,
            strategy_classes=[AlwaysBuyStrategy],
            aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
        )

        engine = BacktestEngine(config, mock_reader)
        result = await engine.run()

        trade = result.trades[0]

        assert trade.pnl < 0
        assert result.total_return < 0
