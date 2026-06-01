from __future__ import annotations

import pytest

from src.backtest.portfolio import PortfolioReplayConfig, PortfolioReplayEngine
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BuyAtFirstBarStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "BuyAtFirstBar"

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        signal_type = SignalType.BUY if indicators["close_price"] == 100.0 else SignalType.HOLD
        return Signal(signal_type, symbol, indicators["close_price"], 1.0, "test", indicators)


def _row(timestamp: str, close: float, *, high: float | None = None, low: float | None = None):
    return {
        "time": timestamp,
        "close_price": close,
        "high_price": close if high is None else high,
        "low_price": close if low is None else low,
        "atr_14": 1.0,
        "ema_200": 90.0,
    }


def _reader(rows_by_symbol: dict[str, list[dict[str, float]]]) -> IndicatorReader:
    reader = IndicatorReader({})

    async def fetch_range(symbol: str, *_args):
        return rows_by_symbol[symbol]

    reader.fetch_range = fetch_range
    return reader


def _config() -> PortfolioReplayConfig:
    return PortfolioReplayConfig(
        symbols=["ETHUSDT", "SOLUSDT"],
        timeframe="1h",
        start_date="2026-01-01",
        end_date="2026-01-02",
        strategy_classes=[BuyAtFirstBarStrategy],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.5},
        order_size_usdt=100.0,
        fee_rate=0.0,
        slippage_pct=0.0,
        sl_atr_multiplier=1.0,
        tp_atr_multiplier=3.0,
    )


@pytest.mark.asyncio
async def test_first_symbol_claims_slot_and_blocks_second_buy_in_same_cycle() -> None:
    rows = {
        "ETHUSDT": [_row("2026-01-01T00:00:00", 100.0), _row("2026-01-01T01:00:00", 101.0)],
        "SOLUSDT": [_row("2026-01-01T00:00:00", 100.0), _row("2026-01-01T01:00:00", 101.0)],
    }

    result = await PortfolioReplayEngine(_config(), _reader(rows)).run()

    assert [trade.symbol for trade in result.trades] == ["ETHUSDT"]
    assert result.skipped_slot_buys == 1


@pytest.mark.asyncio
async def test_atr_exit_releases_slot_before_next_signal_cycle() -> None:
    rows = {
        "ETHUSDT": [
            _row("2026-01-01T00:00:00", 100.0),
            _row("2026-01-01T01:00:00", 99.0, low=98.5),
        ],
        "SOLUSDT": [
            _row("2026-01-01T00:00:00", 101.0),
            _row("2026-01-01T01:00:00", 100.0),
        ],
    }

    result = await PortfolioReplayEngine(_config(), _reader(rows)).run()

    assert [trade.symbol for trade in result.trades] == ["ETHUSDT", "SOLUSDT"]
    assert result.trades[0].exit_reason == "STOP_LOSS"
