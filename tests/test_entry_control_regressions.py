"""Race, cancellation and actual transport-boundary regressions (offline)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.entry_admission import refresh
from src.execution.executor import TradingConfig, TradingExecutor
from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor
from src.execution.paper_executor import PaperExecutor, PaperTradingConfig
from src.execution.staged_orders import StagedOrderManager
from src.risk.entry_control import EntryControl
from src.strategy.signals import Signal, SignalType


def row(armed):
    return [{"agent_id": "all", "armed": armed, "reduce_only": False}]


def risk():
    r = MagicMock()
    r.is_trading_allowed.return_value = (True, "")
    r.is_close_allowed.return_value = (True, "")
    r.check_max_leverage.return_value = (True, "")
    r.check_position_limit.return_value = (True, "")
    r.check_margin_usage.return_value = (True, "")
    return r


async def test_reads_serialize_and_later_disarm_updates_cache_and_metrics():
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return row(True)
        return row(False)

    control, metrics = EntryControl(fetch_rows=fetch), MagicMock()
    old = asyncio.create_task(refresh(control, metrics))
    await started.wait()
    new = asyncio.create_task(refresh(control, metrics))
    await asyncio.sleep(0)
    assert calls == 1
    release.set()
    assert (await old).entries_allowed
    assert not (await new).entries_allowed
    assert not (await control.state()).entries_allowed
    assert metrics.record_entry_control.call_args.kwargs["armed"] is False


async def test_forced_failure_invalidates_cached_allow():
    fail = False

    async def fetch(_):
        if fail:
            raise OSError("down")
        return row(True)

    control = EntryControl(fetch_rows=fetch)
    assert (await control.state(force=True)).entries_allowed
    fail = True
    assert not (await control.state(force=True)).entries_allowed
    assert not (await control.state()).entries_allowed


@pytest.mark.parametrize("order_type", ["market", "limit"])
async def test_cancel_during_spot_admission_rejects_only_unsent_stage(order_type):
    started = asyncio.Event()

    async def fetch(_):
        started.set()
        await asyncio.Event().wait()

    manager = StagedOrderManager(auto_commit=True)
    ex = TradingExecutor(
        TradingConfig(api_key="", api_secret="", enabled=True),
        risk(),
        MagicMock(),
        staged_manager=manager,
        entry_control=EntryControl(fetch_rows=fetch),
    )
    ex._client = MagicMock()
    ex._client.get_account_info = AsyncMock(return_value=SimpleNamespace(available_balance=1000))
    transport = AsyncMock()
    setattr(ex._client, f"place_{order_type}_order", transport)
    method = getattr(ex, f"place_{order_type}_order")
    task = asyncio.create_task(method("BTCUSDT", "BUY", quantity=10, price=100))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.get_stats()["EXECUTING"] == 0
    assert manager.get_stats()["REJECTED"] == 1
    assert not ex._entry_lock.locked()
    transport.assert_not_awaited()


async def test_futures_final_boundary_observes_disarm_and_exits_bypass():
    state = {"armed": True}
    reads = []

    async def fetch(_):
        reads.append(1)
        return row(state["armed"])

    async def account():
        state["armed"] = False  # disarm while the preflight is in progress
        return SimpleNamespace(total_margin_balance=1000, available_balance=1000)

    ex = FuturesTradingExecutor(
        FuturesTradingConfig(api_key="", api_secret="", enabled=True),
        risk(),
        MagicMock(),
        entry_control=EntryControl(fetch_rows=fetch),
    )
    # A prior cached allow must not override the final forced decision.
    assert (await ex._entry_control.state()).entries_allowed
    ex._client = MagicMock()
    ex._client.get_account_info = AsyncMock(side_effect=account)
    ex._client.place_order = AsyncMock(side_effect=RuntimeError("transport sentinel"))
    with pytest.raises(RuntimeError, match="control"):
        await ex.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100)
    ex._client.place_order.assert_not_awaited()
    count = len(reads)
    for side, position_side in [("SELL", "LONG"), ("BUY", "SHORT")]:
        with pytest.raises(RuntimeError, match="transport sentinel"):
            await ex.place_futures_order(
                "BTCUSDT", side, 1, position_side=position_side, reduce_only=True
            )
    assert len(reads) == count
    assert ex._client.place_order.await_count == 2


async def test_cancel_paper_pre_fill_rejects_stage_without_mutation():
    started = asyncio.Event()

    async def fetch(_):
        started.set()
        await asyncio.Event().wait()
        return row(True)

    manager = StagedOrderManager(auto_commit=True)
    notifier = MagicMock()
    notifier.send_alert = AsyncMock()
    notifier.send_trade_alert = AsyncMock()
    r = risk()
    r._config.position_limits.max_open_positions = 10
    r._config.position_limits.max_position_pct = 0.1
    ex = PaperExecutor(
        PaperTradingConfig(enabled=True, order_size_usdt=200),
        r,
        MagicMock(),
        notifier=notifier,
        staged_manager=manager,
        entry_control=EntryControl(fetch_rows=fetch),
    )
    before = ex._balance
    signal = Signal(SignalType.BUY, "BTCUSDT", 50000.0, 1.0, "test", {}, trading_mode="spot")
    task = asyncio.create_task(ex.on_signal(signal))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.get_stats()["EXECUTING"] == 0
    assert manager.get_stats()["REJECTED"] == 1
    assert ex._balance == before and not ex._positions


async def test_cancel_futures_admission_rejects_stage_without_transport():
    started = asyncio.Event()

    async def fetch(_):
        started.set()
        await asyncio.Event().wait()
        return row(True)

    manager = StagedOrderManager(auto_commit=True)
    ex = FuturesTradingExecutor(
        FuturesTradingConfig(api_key="", api_secret="", enabled=True),
        risk(),
        MagicMock(),
        staged_manager=manager,
        entry_control=EntryControl(fetch_rows=fetch),
    )
    ex._client = MagicMock()
    ex._client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(total_margin_balance=1000, available_balance=1000)
    )
    ex._client.place_order = AsyncMock()
    task = asyncio.create_task(ex.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.get_stats()["REJECTED"] == 1
    ex._client.place_order.assert_not_awaited()


async def test_cancel_after_spot_transport_handoff_is_not_called_rejected():
    started = asyncio.Event()

    async def fetch(_):
        return row(True)

    async def transport(**_):
        started.set()
        await asyncio.Event().wait()

    manager = StagedOrderManager(auto_commit=True)
    ex = TradingExecutor(
        TradingConfig(api_key="", api_secret="", enabled=True),
        risk(),
        MagicMock(),
        staged_manager=manager,
        entry_control=EntryControl(fetch_rows=fetch),
    )
    ex._client = MagicMock()
    ex._client.get_account_info = AsyncMock(return_value=SimpleNamespace(available_balance=1000))
    ex._client.place_market_order = AsyncMock(side_effect=transport)
    task = asyncio.create_task(ex.place_market_order("BTCUSDT", "BUY", quantity=10, price=100))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.get_stats()["EXECUTING"] == 1  # outcome unknown after handoff
    assert manager.get_stats()["REJECTED"] == 0
