import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyThenSellStrategy(BaseStrategy):
    def get_name(self):
        return "BuyThenSell"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        if price == 101.0:
            return Signal(SignalType.SELL, symbol, price, 1.0, "Sell", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


class BuyOnceStrategy(BaseStrategy):
    def get_name(self):
        return "BuyOnce"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


class SellOnceStrategy(BaseStrategy):
    def get_name(self):
        return "SellOnce"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]
        if price == 100.0:
            return Signal(SignalType.SELL, symbol, price, 1.0, "Short", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "Hold", indicators)


@pytest.mark.asyncio
async def test_executor_exit_model_ignores_strategy_sell_and_hits_take_profit():
    reader = IndicatorReader({})

    data = [
        {
            "time": "2023-01-01T00:00:00",
            "close_price": 100.0,
            "high_price": 100.2,
            "low_price": 99.8,
            "atr_14": 1.0,
        },
        {
            "time": "2023-01-01T00:01:00",
            "close_price": 101.0,
            "high_price": 101.2,
            "low_price": 100.8,
            "atr_14": 1.0,
        },
        {
            "time": "2023-01-01T00:02:00",
            "close_price": 102.2,
            "high_price": 102.2,
            "low_price": 101.8,
            "atr_14": 1.0,
        },
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
        slippage_pct=0.0,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=2.0,
        trailing_activate_atr=1.5,
        trailing_offset_atr=1.0,
        use_executor_exit_model=True,
        ignore_signal_sells=True,
        strategy_classes=[BuyThenSellStrategy],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    )

    result = await BacktestEngine(config, reader).run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(102.0)
    assert trade.exit_reason == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_executor_exit_model_trails_stop_after_activation():
    reader = IndicatorReader({})

    data = [
        {
            "time": "2023-01-01T00:00:00",
            "close_price": 100.0,
            "high_price": 100.1,
            "low_price": 99.9,
            "atr_14": 2.0,
        },
        {
            "time": "2023-01-01T00:01:00",
            "close_price": 104.0,
            "high_price": 104.0,
            "low_price": 103.5,
            "atr_14": 2.0,
        },
        {
            "time": "2023-01-01T00:02:00",
            "close_price": 102.5,
            "high_price": 103.0,
            "low_price": 101.8,
            "atr_14": 2.0,
        },
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
        slippage_pct=0.0,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        sl_atr_multiplier=1.0,
        tp_atr_multiplier=5.0,
        trailing_activate_atr=1.5,
        trailing_offset_atr=1.0,
        use_executor_exit_model=True,
        ignore_signal_sells=True,
        strategy_classes=[BuyOnceStrategy],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
    )

    result = await BacktestEngine(config, reader).run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(102.0)
    assert trade.exit_reason == "STOP_LOSS"


@pytest.mark.asyncio
async def test_short_executor_exit_model_hits_take_profit():
    reader = IndicatorReader({})
    data = [
        {
            "time": "2023-01-01T00:00:00",
            "close_price": 100.0,
            "high_price": 100.2,
            "low_price": 99.8,
            "atr_14": 1.0,
        },
        {
            "time": "2023-01-01T00:01:00",
            "close_price": 99.0,
            "high_price": 99.2,
            "low_price": 98.8,
            "atr_14": 1.0,
        },
        {
            "time": "2023-01-01T00:02:00",
            "close_price": 97.8,
            "high_price": 98.2,
            "low_price": 97.8,
            "atr_14": 1.0,
        },
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
        slippage_pct=0.0,
        allow_short=True,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=2.0,
        trailing_activate_atr=1.5,
        trailing_offset_atr=1.0,
        use_executor_exit_model=True,
        strategy_classes=[SellOnceStrategy],
        aggregator_config={"min_agreement": 1, "sell_threshold": -0.5},
        apply_global_trend_filter=False,
    )

    result = await BacktestEngine(config, reader).run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "SELL"
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.exit_reason == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_short_executor_exit_model_hits_stop_loss():
    reader = IndicatorReader({})
    data = [
        {
            "time": "2023-01-01T00:00:00",
            "close_price": 100.0,
            "high_price": 100.2,
            "low_price": 99.8,
            "atr_14": 1.0,
        },
        {
            "time": "2023-01-01T00:01:00",
            "close_price": 101.0,
            "high_price": 101.2,
            "low_price": 100.8,
            "atr_14": 1.0,
        },
        {
            "time": "2023-01-01T00:02:00",
            "close_price": 101.6,
            "high_price": 101.6,
            "low_price": 101.2,
            "atr_14": 1.0,
        },
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
        slippage_pct=0.0,
        allow_short=True,
        sl_atr_multiplier=1.5,
        tp_atr_multiplier=5.0,
        use_executor_exit_model=True,
        strategy_classes=[SellOnceStrategy],
        aggregator_config={"min_agreement": 1, "sell_threshold": -0.5},
        apply_global_trend_filter=False,
    )

    result = await BacktestEngine(config, reader).run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_price == pytest.approx(101.5)
    assert trade.exit_reason == "STOP_LOSS"


@pytest.mark.asyncio
async def test_short_executor_exit_model_trails_stop_after_activation():
    reader = IndicatorReader({})
    data = [
        {
            "time": "2023-01-01T00:00:00",
            "close_price": 100.0,
            "high_price": 100.1,
            "low_price": 99.9,
            "atr_14": 2.0,
        },
        {
            "time": "2023-01-01T00:01:00",
            "close_price": 96.0,
            "high_price": 96.5,
            "low_price": 96.0,
            "atr_14": 2.0,
        },
        {
            "time": "2023-01-01T00:02:00",
            "close_price": 98.5,
            "high_price": 98.5,
            "low_price": 98.0,
            "atr_14": 2.0,
        },
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
        slippage_pct=0.0,
        allow_short=True,
        sl_atr_multiplier=1.0,
        tp_atr_multiplier=5.0,
        trailing_activate_atr=1.5,
        trailing_offset_atr=1.0,
        use_executor_exit_model=True,
        strategy_classes=[SellOnceStrategy],
        aggregator_config={"min_agreement": 1, "sell_threshold": -0.5},
        apply_global_trend_filter=False,
    )

    result = await BacktestEngine(config, reader).run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.exit_reason == "STOP_LOSS"
