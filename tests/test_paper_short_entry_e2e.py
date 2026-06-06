"""Paper short-entry E2E: engine gate + paper executor wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.metrics import ExecutionMetrics
from src.execution.paper_executor import PaperExecutor, PaperTradingConfig
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.engine import EngineConfig, StrategyEngine
from src.strategy.signals import Signal, SignalType


class _SellFromFlatStrategy(BaseStrategy):
    def get_name(self) -> str:
        return "SellFromFlat"

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        return Signal(
            SignalType.SELL,
            symbol,
            indicators["close_price"],
            1.0,
            "short entry",
            indicators,
            trading_mode="futures",
        )


def _indicator_reader() -> IndicatorReader:
    reader = IndicatorReader({})
    reader._connected = True

    async def _mock_fetch_rows(_symbol: str, _timeframe: str, _limit: int) -> list[dict]:
        row = {
            "close_price": 50_000.0,
            "ema_12": 50_100.0,
            "ema_26": 49_900.0,
            "ema_200": 48_000.0,
            "atr_14": 250.0,
        }
        return [
            row,
            {**row, "time": "2024-06-01T15:00:00+00:00"},
        ]

    reader._fetch_rows = _mock_fetch_rows  # type: ignore[method-assign]
    return reader


def _paper_executor(*, allow_short_entry: bool) -> PaperExecutor:
    risk_manager = MagicMock()
    risk_manager.is_trading_allowed.return_value = (True, "")
    risk_manager.check_position_limit.return_value = (True, "")
    risk_manager._config.position_limits.max_open_positions = 10
    risk_manager._config.position_limits.max_position_pct = 0.10
    portfolio = MagicMock()
    portfolio.open_position = AsyncMock()
    notifier = MagicMock()
    notifier.send_alert = AsyncMock()
    notifier.send_trade_alert = AsyncMock()
    notifier.__aenter__ = AsyncMock(return_value=notifier)
    notifier.__aexit__ = AsyncMock(return_value=False)
    return PaperExecutor(
        PaperTradingConfig(
            enabled=True,
            order_size_usdt=200.0,
            futures_symbols=["BTCUSDT"],
            futures_leverage=3,
            allow_short_entry=allow_short_entry,
        ),
        risk_manager=risk_manager,
        metrics=MagicMock(spec=ExecutionMetrics),
        notifier=notifier,
        portfolio_manager=portfolio,
    )


@pytest.mark.asyncio
async def test_engine_and_paper_open_short_when_short_entry_enabled() -> None:
    engine = StrategyEngine(
        EngineConfig(
            symbols=["BTCUSDT"],
            strategy_classes=[_SellFromFlatStrategy],
            default_trading_mode="futures",
            aggregator_config={"sell_threshold": -0.5, "min_agreement": 1},
            global_trend_filter_enabled=False,
            allow_short_entry=True,
        ),
        _indicator_reader(),
    )
    engine.set_position_checker(lambda _symbol, _mode: False)
    executor = _paper_executor(allow_short_entry=True)

    async def on_signal(sig: Signal) -> None:
        await executor.on_signal(sig)

    await engine._evaluate_all(on_signal)

    pos = executor._positions.get("BTCUSDT:futures")
    assert pos is not None
    assert pos.side == "SHORT"
    executor._portfolio_manager.open_position.assert_awaited_once_with(
        symbol="BTCUSDT",
        quantity=pytest.approx(0.004),
        price=50_000.0,
        market="futures",
        position_side="SHORT",
    )


@pytest.mark.asyncio
async def test_default_config_suppresses_short_entry_end_to_end() -> None:
    engine = StrategyEngine(
        EngineConfig(
            symbols=["BTCUSDT"],
            strategy_classes=[_SellFromFlatStrategy],
            default_trading_mode="futures",
            aggregator_config={"sell_threshold": -0.5, "min_agreement": 1},
            global_trend_filter_enabled=False,
        ),
        _indicator_reader(),
    )
    engine.set_position_checker(lambda _symbol, _mode: False)
    executor = _paper_executor(allow_short_entry=False)

    received: list[Signal] = []

    async def on_signal(sig: Signal) -> None:
        received.append(sig)
        await executor.on_signal(sig)

    await engine._evaluate_all(on_signal)

    assert received == []
    assert executor._positions.get("BTCUSDT:futures") is None


@pytest.mark.asyncio
async def test_engine_forwards_sell_from_flat_when_short_entry_enabled() -> None:
    engine = StrategyEngine(
        EngineConfig(
            symbols=["BTCUSDT"],
            strategy_classes=[_SellFromFlatStrategy],
            default_trading_mode="futures",
            aggregator_config={"sell_threshold": -0.5, "min_agreement": 1},
            global_trend_filter_enabled=False,
            allow_short_entry=True,
        ),
        _indicator_reader(),
    )
    engine.set_position_checker(lambda _symbol, _mode: False)

    received: list[Signal] = []

    async def on_signal(sig: Signal) -> None:
        received.append(sig)

    await engine._evaluate_all(on_signal)

    assert len(received) == 1
    assert received[0].type == SignalType.SELL
    assert received[0].trading_mode == "futures"
