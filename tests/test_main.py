import asyncio

import pytest

from src.main import (
    _cancel_background_tasks,
    _collect_required_timeframes,
    _should_mirror_spot_signal_to_futures,
)
from src.strategy import Signal, SignalType
from src.strategy.mtf_template import MTFStrategyTemplate
from src.strategy.simple_ma import SimpleMACrossoverStrategy


@pytest.mark.asyncio
async def test_cancel_background_tasks_skips_none() -> None:
    async def never_finishes() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(never_finishes())

    await _cancel_background_tasks(None, task, None)

    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_background_tasks_handles_completed_task() -> None:
    async def finishes_immediately() -> str:
        return "done"

    task = asyncio.create_task(finishes_immediately())
    await task

    await _cancel_background_tasks(task)

    assert task.done()
    assert task.result() == "done"


def test_should_mirror_spot_signal_to_futures_requires_explicit_opt_in() -> None:
    signal = Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=50000.0,
        confidence=1.0,
        reason="test",
        indicators={},
        trading_mode="spot",
    )

    assert (
        _should_mirror_spot_signal_to_futures(
            signal,
            mirror_spot_to_futures=False,
            futures_symbols={"BTCUSDT"},
        )
        is False
    )
    assert (
        _should_mirror_spot_signal_to_futures(
            signal,
            mirror_spot_to_futures=True,
            futures_symbols={"BTCUSDT"},
        )
        is True
    )


def test_should_mirror_spot_signal_to_futures_never_remirrors_futures_signal() -> None:
    signal = Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=50000.0,
        confidence=1.0,
        reason="test",
        indicators={},
        trading_mode="futures",
    )

    assert (
        _should_mirror_spot_signal_to_futures(
            signal,
            mirror_spot_to_futures=True,
            futures_symbols={"BTCUSDT"},
        )
        is False
    )


def test_collect_required_timeframes_includes_main_and_declared() -> None:
    """Single-TF strategies yield only the configured main timeframe."""
    tfs = _collect_required_timeframes([SimpleMACrossoverStrategy], "4h")
    assert tfs == {"4h"}

    """MTF strategies pull in their declared TFs (entry/regime etc.)."""
    tfs = _collect_required_timeframes([MTFStrategyTemplate], "1h")
    assert tfs == {"1h", "4h"}

    """Collector is fully generic over dict values (matches engine + backtest)."""

    class _GenericMTF:
        REQUIRED_TIMEFRAMES = {"entry": "15m", "trend": "1d", "ignored": 123}

    tfs = _collect_required_timeframes([_GenericMTF], "1h")
    assert tfs == {"1h", "15m", "1d"}

    """Mixed strategies produce the union."""
    tfs = _collect_required_timeframes([SimpleMACrossoverStrategy, MTFStrategyTemplate], "1h")
    assert tfs == {"1h", "4h"}
