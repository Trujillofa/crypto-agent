from __future__ import annotations

from datetime import datetime

import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.session_liquidity import SessionLiquidityRouterConfig
from src.strategy.signals import Signal, SignalType


class BuyEveryBarStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "BuyEveryBar"

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        price = indicators["close_price"]
        return Signal(SignalType.BUY, symbol, price, 1.0, "always buy", indicators)


def _build_reader(rows: list[dict[str, object]]) -> IndicatorReader:
    reader = IndicatorReader({})
    reader._connected = True

    async def _mock_fetch(*_args: object) -> list[dict[str, object]]:
        return rows

    reader.fetch_range = _mock_fetch  # type: ignore[method-assign]
    return reader


def _row(hour: int, close: float = 100.0) -> dict[str, object]:
    return {
        "time": datetime(2024, 6, 3, hour, 0, 0),
        "close_price": close,
        "high_price": close + 1.0,
        "low_price": close - 1.0,
        "ema_200": 50.0,
        "atr_14": 1.0,
    }


@pytest.mark.asyncio
async def test_backtest_records_blocked_buy_count() -> None:
    rows = [_row(15), _row(16, 101.0), _row(17, 102.0)]
    base_kwargs = {
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "start_date": "2024-06-01",
        "end_date": "2024-06-04",
        "initial_capital": 10000.0,
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
        "apply_global_trend_filter": False,
        "strategy_classes": [BuyEveryBarStrategy],
        "aggregator_config": {"min_agreement": 1, "buy_threshold": 0.5},
    }
    ungated = await BacktestEngine(
        BacktestConfig(**base_kwargs),
        _build_reader(rows),
    ).run()
    gated = await BacktestEngine(
        BacktestConfig(
            **base_kwargs,
            session_liquidity_router=SessionLiquidityRouterConfig(
                enabled=True,
                allowed_windows=("americas",),
            ),
        ),
        _build_reader(rows),
    ).run()

    assert gated.blocked_buy_count > 0
    assert ungated.blocked_buy_count == 0
    assert gated.total_trades <= ungated.total_trades
