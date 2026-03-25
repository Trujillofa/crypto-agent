import asyncio

import pytest

from src.main import _cancel_background_tasks, _should_mirror_spot_signal_to_futures
from src.strategy import Signal, SignalType


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
