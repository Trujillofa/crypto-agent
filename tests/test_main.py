import asyncio

import pytest

from src.main import _cancel_background_tasks, _resolve_signal_routing
from src.strategy.signals import Signal, SignalType


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


def _make_signal(trading_mode: str = "spot") -> Signal:
    return Signal(
        type=SignalType.BUY,
        symbol="BTCUSDT",
        price=50000.0,
        confidence=0.8,
        reason="test",
        indicators={"atr_14": 250.0},
        trading_mode=trading_mode,
    )


def test_resolve_signal_routing_reroutes_to_futures_for_futures_default() -> None:
    signal = _make_signal("spot")

    routed, should_mirror = _resolve_signal_routing(signal, {"BTCUSDT"}, "futures")

    assert should_mirror is False
    assert routed.trading_mode == "futures"
    assert routed.symbol == signal.symbol
    assert routed.price == signal.price


def test_resolve_signal_routing_preserves_spot_and_mirrors_for_spot_default() -> None:
    signal = _make_signal("spot")

    routed, should_mirror = _resolve_signal_routing(signal, {"BTCUSDT"}, "spot")

    assert should_mirror is True
    assert routed is signal


def test_resolve_signal_routing_preserves_explicit_futures_signal() -> None:
    signal = _make_signal("futures")

    routed, should_mirror = _resolve_signal_routing(signal, {"BTCUSDT"}, "futures")

    assert should_mirror is False
    assert routed is signal
