import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class SingleTradeStrategy(BaseStrategy):
    def get_name(self):
        return "SingleTrade"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]
        # Buy at 100, Sell at 110
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        elif price == 110.0:
            return Signal(SignalType.SELL, symbol, price, 1.0, "Sell", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


class TestBacktestSlippage:
    @pytest.mark.asyncio
    async def test_slippage_execution(self):
        reader = IndicatorReader({})
        reader._connected = True

        # Scenario:
        # T1: 100.0 (Buy) -> Executed at 100 * 1.01 = 101.0
        # T2: 110.0 (Sell) -> Executed at 110 * 0.99 = 108.9
        data = [
            {"time": "2023-01-01T00:00:00", "close_price": 100.0, "atr_14": 1.0},
            {"time": "2023-01-01T00:01:00", "close_price": 110.0, "atr_14": 1.0},
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
            slippage_pct=0.01,  # 1% slippage
            strategy_classes=[SingleTradeStrategy],
            aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
        )

        engine = BacktestEngine(config, reader)
        result = await engine.run()

        assert len(result.trades) == 1
        trade = result.trades[0]

        # Verify Entry Price (Slippage applied)
        assert trade.entry_price == pytest.approx(101.0)

        # Verify Exit Price (Slippage applied)
        assert trade.exit_price == pytest.approx(108.9)

        # Verify Quantity (Based on entry price)
        # Cash 10000 / 101.0 = 99.0099
        expected_qty = 10000.0 / 101.0
        assert trade.quantity == pytest.approx(expected_qty)

        # Verify PnL
        # (108.9 - 101.0) * Qty
        expected_pnl = (108.9 - 101.0) * expected_qty
        assert trade.pnl == pytest.approx(expected_pnl)
