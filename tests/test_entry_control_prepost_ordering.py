"""The final entry decision must come after every awaited exchange step.

A fresh control read that is followed by an awaited time sync (or any other
exchange call) is not fresh: the operator can disarm while that call is in
flight and the order still goes out. These tests pin the order of work and the
clock-freshness fallback that keeps the POST valid without moving the sync back
behind the decision.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.futures_client import BinanceFuturesClient
from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor
from src.risk.entry_control import EntryControl


def _control(armed=True):
    async def fetch(_ids):
        return [{"agent_id": "all", "armed": armed, "reduce_only": False}]

    return EntryControl(fetch_rows=fetch)


def _executor(control):
    risk = MagicMock()
    for name in (
        "is_close_allowed",
        "is_trading_allowed",
        "check_max_leverage",
        "check_position_limit",
        "check_margin_usage",
    ):
        getattr(risk, name).return_value = (True, "")
    executor = FuturesTradingExecutor(
        FuturesTradingConfig(api_key="", api_secret="", enabled=True, position_mode="hedge"),
        risk,
        MagicMock(),
        entry_control=control,
    )
    client = BinanceFuturesClient("dummy", "dummy")
    client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(total_margin_balance=1000, available_balance=1000)
    )
    executor._client = client
    return executor, client


class _PostingSession:
    """Minimal session stand-in: records the POST, never reaches the network."""

    def __init__(self, events):
        self._events = events

    def post(self, *_args, **_kwargs):
        self._events.append("post")
        raise RuntimeError("POST sentinel")


async def test_disarm_during_clock_sync_is_seen_by_final_read_and_blocks_post():
    armed = True
    sync_started, release_sync = asyncio.Event(), asyncio.Event()
    posts: list[object] = []

    async def fetch(_ids):
        return [{"agent_id": "all", "armed": armed, "reduce_only": False}]

    control = EntryControl(fetch_rows=fetch)
    executor, client = _executor(control)

    async def sync():
        sync_started.set()
        await release_sync.wait()

    client._ensure_time_sync = sync
    cast(Any, client)._session = _PostingSession(posts)

    order = asyncio.create_task(
        executor.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100)
    )
    await asyncio.wait_for(sync_started.wait(), 1)

    # The operator disarms while the exchange clock sync is still in flight.
    armed = False
    assert not (await control.state(force=True)).entries_allowed
    release_sync.set()

    with pytest.raises(RuntimeError, match="Entry blocked by control"):
        await order
    assert posts == []


async def test_clock_sync_runs_before_final_admission_and_post_is_still_sent():
    events: list[str] = []

    async def fetch(_ids):
        events.append("admit")
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    control = EntryControl(fetch_rows=fetch)
    executor, client = _executor(control)

    async def sync():
        events.append("sync")

    client._ensure_time_sync = sync
    cast(Any, client)._session = _PostingSession(events)

    with pytest.raises(RuntimeError, match="POST sentinel"):
        await executor.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100)

    # Sync first, then the last admission read, then the order POST.
    assert events == ["sync", "admit", "post"]


async def test_stale_prepared_clock_resyncs_redecides_then_fails_closed():
    events: list[str] = []

    async def fetch(_ids):
        events.append("admit")
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    control = EntryControl(fetch_rows=fetch)
    executor, client = _executor(control)

    async def sync():
        events.append("sync")

    client._ensure_time_sync = sync
    cast(Any, client)._session = _PostingSession(events)
    # Pretend every admission read outlives the prepared timestamp.
    client.prepared_request_age_s = lambda: 3.0

    with pytest.raises(RuntimeError, match="timestamp freshness window"):
        await executor.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100)

    # One re-sync plus a second admission read; then the entry fails closed
    # instead of POSTing behind a stale decision. Nothing reached the exchange.
    assert events == ["sync", "admit", "sync", "admit"]


async def test_clock_consumed_by_another_submission_forces_resync_and_readmit():
    events: list[str] = []

    async def fetch(_ids):
        events.append("admit")
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    control = EntryControl(fetch_rows=fetch)
    executor, client = _executor(control)

    async def sync():
        events.append("sync")

    client._ensure_time_sync = sync
    cast(Any, client)._session = _PostingSession(events)
    ages = iter([None, 0.0])  # marker stolen, then fresh after the re-sync
    client.prepared_request_age_s = lambda: next(ages, 0.0)

    with pytest.raises(RuntimeError, match="POST sentinel"):
        await executor.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100)

    assert events == ["sync", "admit", "sync", "admit", "post"]


async def test_clock_sync_failure_rejects_the_staged_order():
    executor, client = _executor(_control())

    async def broken_sync():
        raise RuntimeError("time server unavailable")

    client._ensure_time_sync = broken_sync
    executor._staged_manager = MagicMock()
    executor._staged_manager.stage = AsyncMock(return_value=SimpleNamespace(order_id="stage-1"))
    executor._staged_manager.mark_rejected = AsyncMock()

    with pytest.raises(RuntimeError, match="time server unavailable"):
        await executor.place_futures_order("BTCUSDT", "BUY", 1, reference_price=100)

    executor._staged_manager.mark_rejected.assert_awaited_once_with(
        "stage-1", "exchange clock sync failed before submission"
    )


class _OkResponse:
    status = 200

    async def json(self):
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _GetSession:
    def get(self, *_args, **_kwargs):
        return _OkResponse()


async def test_prepared_clock_is_single_use_at_the_client():
    client = BinanceFuturesClient("dummy", "dummy")
    cast(Any, client)._session = _GetSession()
    syncs = 0

    async def counting_sync():
        nonlocal syncs
        syncs += 1

    client._ensure_time_sync = counting_sync

    await client.prepare_signed_request()
    assert syncs == 1
    assert client.prepared_request_age_s() is not None

    # The next signed request consumes the prepared clock without syncing again.
    await client._request("GET", "/fapi/v1/account", {}, signed=True)
    assert syncs == 1
    assert client.prepared_request_age_s() is None

    # A later signed request has no prepared clock and must sync on its own.
    await client._request("GET", "/fapi/v1/account", {}, signed=True)
    assert syncs == 2
