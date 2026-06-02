import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyAt100SellAt103Strategy(BaseStrategy):
    def get_name(self):
        return "BuyAt100SellAt103"

    async def evaluate(self, symbol, indicators):
        price = indicators["close_price"]
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "Buy", indicators)
        if price == 103.0:
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


def _build_reader(data):
    reader = IndicatorReader({})
    reader._connected = True

    async def _mock_fetch(*_args):
        return data

    reader.fetch_range = _mock_fetch
    return reader


@pytest.mark.asyncio
async def test_futures_mode_false_matches_existing_spot_behavior():
    data = [
        {"time": "2023-01-01T00:00:00", "close_price": 100.0},
        {"time": "2023-01-01T00:01:00", "close_price": 101.0},
        {"time": "2023-01-01T00:02:00", "close_price": 102.0},
        {"time": "2023-01-01T00:03:00", "close_price": 103.0},
    ]
    base_kwargs = {
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "start_date": "2023-01-01",
        "end_date": "2023-01-02",
        "initial_capital": 10000.0,
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
        "apply_global_trend_filter": False,
        "strategy_classes": [BuyAt100SellAt103Strategy],
        "aggregator_config": {"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    }

    baseline = await BacktestEngine(BacktestConfig(**base_kwargs), _build_reader(data)).run()
    futures_disabled = await BacktestEngine(
        BacktestConfig(**base_kwargs, futures_mode=False),
        _build_reader(data),
    ).run()

    baseline_trade = baseline.trades[0]
    futures_disabled_trade = futures_disabled.trades[0]

    assert futures_disabled.total_trades == baseline.total_trades == 1
    assert futures_disabled.total_return == pytest.approx(baseline.total_return)
    assert futures_disabled.final_equity == pytest.approx(baseline.final_equity)
    assert futures_disabled_trade.entry_price == pytest.approx(baseline_trade.entry_price)
    assert futures_disabled_trade.exit_price == pytest.approx(baseline_trade.exit_price)
    assert futures_disabled_trade.quantity == pytest.approx(baseline_trade.quantity)
    assert futures_disabled_trade.pnl == pytest.approx(baseline_trade.pnl)
    assert futures_disabled_trade.return_pct == pytest.approx(baseline_trade.return_pct)
    assert futures_disabled_trade.margin_used == 0.0


def test_margin_is_reserved_on_open_and_returned_on_close():
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=1000.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        use_atr_sizing=True,
        risk_per_trade=0.02,
        atr_multiplier=2.0,
        futures_mode=True,
        futures_leverage=5,
    )

    engine = BacktestEngine(config, IndicatorReader({}))
    engine._open_long("2023-01-01T00:00:00", 100.0, atr=10.0)

    assert engine._position_qty == pytest.approx(1.0)
    assert engine._position_margin_used == pytest.approx(20.0)
    assert engine._cash == pytest.approx(980.0)

    engine._close_position("2023-01-01T00:01:00", 110.0)

    assert engine._cash == pytest.approx(1010.0)
    assert len(engine._trades) == 1
    assert engine._trades[0].margin_used == pytest.approx(20.0)
    assert engine._trades[0].pnl == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_funding_is_deducted_per_open_candle():
    data = [
        {"time": "2023-01-01T00:00:00", "close_price": 100.0, "atr_14": 10.0},
        {"time": "2023-01-01T00:01:00", "close_price": 100.0, "atr_14": 10.0},
        {"time": "2023-01-01T00:02:00", "close_price": 100.0, "atr_14": 10.0},
    ]
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=1000.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        use_atr_sizing=True,
        risk_per_trade=0.02,
        atr_multiplier=2.0,
        futures_mode=True,
        futures_leverage=5,
        futures_funding_rate=0.01,
        apply_global_trend_filter=False,
        strategy_classes=[BuyOnceStrategy],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
    )

    engine = BacktestEngine(config, _build_reader(data))
    result = await engine.run()

    assert result.total_trades == 1
    assert engine._cash == pytest.approx(998.0)
    assert result.trades[0].pnl == pytest.approx(-2.0)
    assert result.trades[0].margin_used == pytest.approx(20.0)


def test_liquidation_triggers_when_equity_is_wiped_out():
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=1000.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        futures_mode=True,
        futures_leverage=5,
        futures_funding_rate=0.0,
    )

    engine = BacktestEngine(config, IndicatorReader({}))
    engine._open_long("2023-01-01T00:00:00", 100.0, atr=0.0)

    assert engine._cash == pytest.approx(0.0)
    assert engine._position_margin_used == pytest.approx(1000.0)
    assert engine._check_liquidation("2023-01-01T00:01:00", 80.0) is True
    assert engine._cash == pytest.approx(0.0)
    assert len(engine._trades) == 1
    assert engine._trades[0].exit_reason == "LIQUIDATION"
    assert engine._trades[0].pnl == pytest.approx(-1000.0)


def test_leveraged_pnl_remains_linear_not_multiplier_scaled():
    config = BacktestConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_date="2023-01-01",
        end_date="2023-01-02",
        initial_capital=1000.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        futures_mode=True,
        futures_leverage=10,
        futures_funding_rate=0.0,
    )

    engine = BacktestEngine(config, IndicatorReader({}))
    engine._open_long("2023-01-01T00:00:00", 100.0, atr=0.0)
    engine._close_position("2023-01-01T00:01:00", 110.0)

    assert len(engine._trades) == 1
    assert engine._trades[0].quantity == pytest.approx(100.0)
    assert engine._trades[0].pnl == pytest.approx(1000.0)
    assert engine._cash == pytest.approx(2000.0)


def test_higher_leverage_allows_larger_position_sizes_with_same_cash():
    low_leverage_engine = BacktestEngine(
        BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1m",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=1000.0,
            fee_rate=0.0,
            slippage_pct=0.0,
            futures_mode=True,
            futures_leverage=2,
        ),
        IndicatorReader({}),
    )
    high_leverage_engine = BacktestEngine(
        BacktestConfig(
            symbol="BTCUSDT",
            timeframe="1m",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=1000.0,
            fee_rate=0.0,
            slippage_pct=0.0,
            futures_mode=True,
            futures_leverage=10,
        ),
        IndicatorReader({}),
    )

    low_qty = low_leverage_engine._calculate_entry_qty(100.0, atr=0.0)
    high_qty = high_leverage_engine._calculate_entry_qty(100.0, atr=0.0)

    assert low_qty == pytest.approx(20.0)
    assert high_qty == pytest.approx(100.0)
    assert high_qty > low_qty


def test_fixed_notional_caps_futures_position_size():
    engine = BacktestEngine(
        BacktestConfig(
            symbol="SOLUSDT",
            timeframe="1h",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=1000.0,
            fee_rate=0.0,
            futures_mode=True,
            futures_leverage=3,
            fixed_notional_usdt=22.0,
        ),
        IndicatorReader({}),
    )

    quantity = engine._calculate_entry_qty(80.0, atr=0.0)

    assert quantity == pytest.approx(22.0 / 80.0)


def test_fixed_notional_default_preserves_all_capital_futures_sizing():
    engine = BacktestEngine(
        BacktestConfig(
            symbol="SOLUSDT",
            timeframe="1h",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=1000.0,
            fee_rate=0.0,
            futures_mode=True,
            futures_leverage=3,
        ),
        IndicatorReader({}),
    )

    quantity = engine._calculate_entry_qty(80.0, atr=0.0)

    assert quantity == pytest.approx(37.5)


def test_quantity_step_truncates_fixed_notional_futures_size():
    engine = BacktestEngine(
        BacktestConfig(
            symbol="SOLUSDT",
            timeframe="1h",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=1000.0,
            fee_rate=0.0,
            futures_mode=True,
            futures_leverage=3,
            fixed_notional_usdt=22.0,
            quantity_step_size=0.01,
            min_notional_usdt=20.0,
        ),
        IndicatorReader({}),
    )

    quantity = engine._calculate_entry_qty(79.0, atr=0.0)

    assert quantity == pytest.approx(0.27)


def test_quantity_step_bumps_when_truncation_drops_below_min_notional():
    engine = BacktestEngine(
        BacktestConfig(
            symbol="ETHUSDT",
            timeframe="1h",
            start_date="2023-01-01",
            end_date="2023-01-02",
            initial_capital=1000.0,
            fee_rate=0.0,
            futures_mode=True,
            futures_leverage=3,
            fixed_notional_usdt=20.0,
            quantity_step_size=0.001,
            min_notional_usdt=20.0,
        ),
        IndicatorReader({}),
    )

    quantity = engine._calculate_entry_qty(2340.0, atr=0.0)

    assert quantity == pytest.approx(0.009)
    assert quantity * 2340.0 >= 20.0
