"""Offline transport and snapshot-order tests for the durable entry brake."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor
from src.risk.entry_control import EntryControl


def row(armed):
    return [{"agent_id": "all", "armed": armed, "reduce_only": False}]


def executor(control, mode="hedge"):
    risk = MagicMock()
    for name in (
        "is_close_allowed",
        "is_trading_allowed",
        "check_max_leverage",
        "check_position_limit",
        "check_margin_usage",
    ):
        getattr(risk, name).return_value = (True, "")
    ex = FuturesTradingExecutor(
        FuturesTradingConfig(api_key="", api_secret="", enabled=True, position_mode=mode),
        risk,
        MagicMock(),
        entry_control=control,
    )
    ex._client = MagicMock()
    ex._client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(total_margin_balance=1000, available_balance=1000)
    )
    ex._client.place_order = AsyncMock(side_effect=RuntimeError("transport sentinel"))
    # Hedge closes are proven against the leg the exchange actually holds; both
    # legs are present here so a request for either direction can be verified.
    ex._client.get_position_risk = AsyncMock(
        return_value=[
            SimpleNamespace(position_side="LONG", position_amt=1.0, entry_price=100.0),
            SimpleNamespace(position_side="SHORT", position_amt=-1.0, entry_price=100.0),
        ]
    )
    return ex


@pytest.mark.parametrize(
    "side,leg",
    [
        ("BUY", "LONG"),
        ("SELL", "SHORT"),
        ("BUY", "BOTH"),
        ("SELL", "BOTH"),
        ("buy", "SHORT"),
        ("BUY", ""),
        ("BUY", None),
    ],
)
async def test_invalid_hedge_exit_never_reaches_transport(side, leg):
    async def fetch(_):
        return row(False)

    ex = executor(EntryControl(fetch_rows=fetch))
    with pytest.raises(ValueError, match="invalid hedge"):
        await ex.place_futures_order("BTCUSDT", side, 1, position_side=leg, reduce_only=True)
    ex._client.get_account_info.assert_not_awaited()
    ex._client.place_order.assert_not_awaited()


@pytest.mark.parametrize("side,leg", [("SELL", "LONG"), ("BUY", "SHORT")])
async def test_hedge_true_exits_bypass_inhibited_control(side, leg):
    async def fetch(_):
        raise AssertionError("exit must not read control")

    ex = executor(EntryControl(fetch_rows=fetch))
    with pytest.raises(RuntimeError, match="transport sentinel"):
        await ex.place_futures_order("BTCUSDT", side, 1, position_side=leg, reduce_only=True)
    assert ex._client.place_order.await_args.kwargs["reduce_only"] is False
    assert ex._client.place_order.await_args.kwargs["position_side"] == leg


async def test_one_way_exit_keeps_exchange_reduce_only():
    ex = executor(EntryControl(), mode="one-way")
    with pytest.raises(RuntimeError, match="transport sentinel"):
        await ex.place_futures_order("BTCUSDT", "BUY", 1, position_side="SHORT", reduce_only=True)
    assert ex._client.place_order.await_args.kwargs["reduce_only"] is True
    assert ex._client.place_order.await_args.kwargs["position_side"] == "BOTH"


async def test_order_read_waits_for_actual_snapshot_and_blocks_disarm():
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return row(False)

    control = EntryControl(fetch_rows=fetch)
    ex = executor(control)
    observer = asyncio.create_task(control.state(force=True))
    await started.wait()
    order = asyncio.create_task(ex.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100))
    await asyncio.sleep(0.02)
    assert calls == 1
    release.set()
    assert not (await observer).entries_allowed
    with pytest.raises(RuntimeError, match="control"):
        await order
    assert calls == 2
    ex._client.place_order.assert_not_awaited()


async def test_cache_waits_for_refresh_and_cancel_invalidates_old_allow():
    started = asyncio.Event()
    calls = 0

    async def fetch(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await asyncio.Event().wait()
        return row(calls == 1)

    control = EntryControl(fetch_rows=fetch)
    assert (await control.state()).entries_allowed
    refresh = asyncio.create_task(control.state(force=True))
    await started.wait()
    cached_reader = asyncio.create_task(control.state())
    await asyncio.sleep(0)
    assert not cached_reader.done()
    refresh.cancel()
    with pytest.raises(asyncio.CancelledError):
        await refresh
    assert not (await cached_reader).entries_allowed
    assert calls == 3
