"""Execution-parity v2 uses completed-bar signals and next-open fills."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import FundingSettlement, IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyThenSellStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "BuyThenSell"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        if price == 100.0:
            return Signal(SignalType.BUY, symbol, price, 1.0, "buy", indicators)
        if price == 106.0:
            return Signal(SignalType.SELL, symbol, price, 1.0, "sell", indicators)
        return Signal(SignalType.HOLD, symbol, price, 0.0, "hold", indicators)


class BuyOnceStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "BuyOnce"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        signal_type = SignalType.BUY if price == 100.0 else SignalType.HOLD
        return Signal(
            signal_type,
            symbol,
            price,
            1.0 if signal_type == SignalType.BUY else 0.0,
            "x",
            indicators,
        )


class ShortOnceStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "ShortOnce"

    async def evaluate(self, symbol: str, indicators: dict[str, object]) -> Signal:
        price = float(indicators["close_price"])
        signal_type = SignalType.SELL if price == 100.0 else SignalType.HOLD
        return Signal(
            signal_type,
            symbol,
            price,
            1.0 if signal_type == SignalType.SELL else 0.0,
            "x",
            indicators,
        )


def _reader(
    rows: list[dict[str, object]], settlements: list[FundingSettlement] | None = None
) -> IndicatorReader:
    reader = IndicatorReader({})

    async def fetch_range(*_args: object) -> list[dict[str, object]]:
        return rows

    async def fetch_funding(*_args: object) -> list[FundingSettlement]:
        return settlements or []

    reader.fetch_range = fetch_range  # type: ignore[method-assign]
    reader.fetch_funding_settlements = fetch_funding  # type: ignore[method-assign]
    return reader


def _config(strategy: type[BaseStrategy], **overrides: object) -> BacktestConfig:
    values: dict[str, object] = {
        "symbol": "SOLUSDT",
        "timeframe": "1h",
        "start_date": "2024-01-01T00:00:00",
        "end_date": "2024-01-02T00:00:00",
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
        "apply_global_trend_filter": False,
        "execution_profile": "execution_parity_v2",
        "strategy_classes": [strategy],
        "aggregator_config": {"min_agreement": 1, "buy_threshold": 0.5, "sell_threshold": -0.5},
    }
    values.update(overrides)
    return BacktestConfig(**values)


@pytest.mark.asyncio
async def test_v2_fills_signal_at_next_bar_open_and_records_trace() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 105.0, "close_price": 106.0},
        {"time": "2024-01-01T02:00:00", "open_price": 110.0, "close_price": 111.0},
    ]
    result = await BacktestEngine(_config(BuyThenSellStrategy), _reader(rows)).run()

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(105.0)
    assert trade.exit_price == pytest.approx(110.0)
    assert trade.signal_time == "2024-01-01T00:00:00"
    assert trade.fill_source == "next_bar_open"
    assert result.queued_signal_count == 2


@pytest.mark.asyncio
async def test_v2_marks_final_bar_signal_unfilled() -> None:
    rows = [{"time": "2024-01-01T00:00:00", "open_price": 99.0, "close_price": 100.0}]
    result = await BacktestEngine(_config(BuyOnceStrategy), _reader(rows)).run()

    assert result.total_trades == 0
    assert result.queued_signal_count == 1
    assert result.unfilled_signal_count == 1


@pytest.mark.asyncio
async def test_v2_applies_signed_funding_at_recorded_settlement() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 100.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 100.0, "close_price": 100.0},
        {"time": "2024-01-01T08:00:00", "open_price": 100.0, "close_price": 100.0},
    ]
    settlements = [FundingSettlement(datetime.fromisoformat("2024-01-01T08:00:00"), 0.01, 100.0)]
    config = _config(
        BuyOnceStrategy,
        futures_mode=True,
        fixed_notional_usdt=100.0,
        futures_leverage=1,
        end_date="2024-01-01T08:00:00",
    )
    result = await BacktestEngine(config, _reader(rows, settlements)).run()

    assert result.trades[0].funding_paid == pytest.approx(1.0)
    assert result.trades[0].pnl == pytest.approx(-1.0)
    assert result.funding_settlement_count == 1


@pytest.mark.asyncio
async def test_v2_short_receives_positive_funding() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 100.0, "close_price": 100.0},
        {"time": "2024-01-01T01:00:00", "open_price": 100.0, "close_price": 100.0},
        {"time": "2024-01-01T08:00:00", "open_price": 100.0, "close_price": 100.0},
    ]
    settlements = [FundingSettlement(datetime.fromisoformat("2024-01-01T08:00:00"), 0.01, 100.0)]
    config = _config(
        ShortOnceStrategy,
        futures_mode=True,
        allow_short=True,
        fixed_notional_usdt=100.0,
        futures_leverage=1,
        end_date="2024-01-01T08:00:00",
    )
    result = await BacktestEngine(config, _reader(rows, settlements)).run()

    assert result.trades[0].funding_paid == pytest.approx(-1.0)
    assert result.trades[0].pnl == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_v2_rejects_missing_in_range_funding_settlement() -> None:
    rows = [
        {"time": "2024-01-01T00:00:00", "open_price": 100.0, "close_price": 100.0},
        {"time": "2024-01-01T08:00:00", "open_price": 100.0, "close_price": 100.0},
    ]
    config = _config(BuyOnceStrategy, futures_mode=True, end_date="2024-01-01T08:00:00")

    with pytest.raises(ValueError, match="Missing historical funding settlements"):
        await BacktestEngine(config, _reader(rows)).run()
