"""Short-side parity assertions and remaining runtime gaps (audit v0).

Backtest executor-exit parity for shorts is covered here and in
``test_backtest_executor_exit_model.py``. Remaining tests document runtime gaps
(engine, live futures, guards) not yet fixed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.execution.futures_executor import FuturesTradingExecutor
from src.execution.metrics import ExecutionMetrics
from src.execution.paper_executor import PaperExecutor, PaperTradingConfig
from src.features.reader import IndicatorReader
from src.risk.guards import GuardContext, PositionLimitGuard
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
            "short intent",
            indicators,
        )


def _mock_reader(rows: list[dict[str, object]]) -> IndicatorReader:
    reader = IndicatorReader({})
    reader._connected = True

    async def _fetch(*_args: object) -> list[dict[str, object]]:
        return rows

    reader.fetch_range = _fetch  # type: ignore[method-assign]
    return reader


def test_backtest_open_short_sets_inverted_atr_sl_tp() -> None:
    """Backtest short executor-exit parity: inverted ATR SL/TP at entry."""
    config = BacktestConfig(
        symbol="SOLUSDT",
        timeframe="1h",
        start_date="2024-06-01",
        end_date="2024-06-04",
        use_executor_exit_model=True,
        slippage_pct=0.0,
        sl_atr_multiplier=2.0,
        tp_atr_multiplier=4.0,
    )
    engine = BacktestEngine(config, _mock_reader([]))
    engine._open_short("2024-06-03T12:00:00", 100.0, 2.0)

    assert engine._position_qty < 0
    assert engine._position_entry_price == pytest.approx(100.0)
    assert engine._position_sl_price == pytest.approx(104.0)
    assert engine._position_tp_price == pytest.approx(92.0)
    assert engine._position_high_water_mark == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_strategy_engine_suppresses_sell_from_flat() -> None:
    """P0 gap: runtime path cannot open shorts via SELL consensus."""
    reader = IndicatorReader({})
    reader._connected = True

    async def _mock_fetch_rows(_symbol: str, _timeframe: str, _limit: int) -> list[dict]:
        return [
            {"close_price": 100.0, "ema_200": 90.0},
            {"time": "2024-06-01T15:00:00+00:00", "close_price": 100.0, "ema_200": 90.0},
        ]

    reader._fetch_rows = _mock_fetch_rows  # type: ignore[method-assign]

    config = EngineConfig(
        symbols=["SOLUSDT"],
        strategy_classes=[_SellFromFlatStrategy],
        aggregator_config={"sell_threshold": -0.5, "min_agreement": 1},
        global_trend_filter_enabled=False,
    )
    engine = StrategyEngine(config, reader)
    engine.set_position_checker(lambda _symbol, _mode: False)

    received: list[Signal] = []

    async def on_signal(sig: Signal) -> None:
        received.append(sig)

    await engine._evaluate_all(on_signal)

    # SELL-from-flat is suppressed to HOLD; HOLD is not forwarded to on_signal.
    assert received == []


def test_position_limit_guard_auto_passes_sell() -> None:
    """P1 gap: short-entry SELL would bypass position-limit guard."""
    guard = PositionLimitGuard(max_position_pct=0.1, risk_manager=MagicMock())
    context = GuardContext(
        symbol="SOLUSDT",
        side="SELL",
        quantity=1.0,
        price=100.0,
        current_position=0.0,
        portfolio_value=10_000.0,
    )
    result = guard.check(context)
    assert result.passed is True


@pytest.mark.asyncio
async def test_paper_short_requires_explicit_allow_short_entry() -> None:
    """Paper parity exists but is off by default (matches live LONG-only)."""
    config = PaperTradingConfig(
        enabled=True,
        futures_symbols=["BTCUSDT"],
        allow_short_entry=False,
    )
    risk_manager = MagicMock()
    risk_manager.is_trading_allowed.return_value = (True, "")
    risk_manager.check_position_limit.return_value = (True, "")
    portfolio = MagicMock()
    portfolio.open_position = AsyncMock()
    executor = PaperExecutor(
        config,
        risk_manager=risk_manager,
        metrics=MagicMock(spec=ExecutionMetrics),
        portfolio_manager=portfolio,
    )
    signal = Signal(
        SignalType.SELL,
        "BTCUSDT",
        50_000.0,
        0.8,
        "short",
        {"atr_14": 250.0},
        trading_mode="futures",
    )

    await executor.on_signal(signal)
    assert executor._positions.get("BTCUSDT:futures") is None


def test_futures_executor_documents_long_only_mvp() -> None:
    """Live executor is explicitly LONG-only until short branch is implemented."""
    doc = FuturesTradingExecutor.on_signal.__doc__ or ""
    assert "LONG-only" in doc
