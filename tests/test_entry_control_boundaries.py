"""Offline execution-boundary regressions for durable entry admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.executor import TradingConfig, TradingExecutor
from src.execution.paper_executor import PaperExecutor, PaperTradingConfig
from src.risk.entry_control import EntryControl
from src.strategy.signals import Signal, SignalType


def signal(kind, mode):
    return Signal(kind, "BTCUSDT", 50000.0, 1.0, "drill", {}, trading_mode=mode)


def risk():
    r = MagicMock()
    r.is_trading_allowed.return_value = (True, "")
    r.is_close_allowed.return_value = (True, "")
    r.check_position_limit.return_value = (True, "")
    r._config.position_limits.max_open_positions = 10
    r._config.position_limits.max_position_pct = 0.1
    return r


def control_box():
    box = {"armed": True, "reduce_only": False, "mode": "ok"}

    async def fetch(ids):
        if box["mode"] == "down":
            raise OSError("offline")
        if box["mode"] == "missing":
            return []
        return [
            {
                "agent_id": "all",
                "armed": box["armed"],
                "reduce_only": box["reduce_only"],
                "reason": "operator pause",
            }
        ]

    return box, EntryControl(agent_id="paper-one", fetch_rows=fetch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,kind",
    [("spot", SignalType.BUY), ("futures", SignalType.BUY), ("futures", SignalType.SELL)],
)
async def test_paper_admission_and_exit(mode, kind):
    box, control = control_box()
    n = MagicMock()
    n.send_alert = AsyncMock()
    n.send_trade_alert = AsyncMock()
    ex = PaperExecutor(
        PaperTradingConfig(
            enabled=True, order_size_usdt=200, futures_symbols=["BTCUSDT"], allow_short_entry=True
        ),
        risk(),
        MagicMock(),
        notifier=n,
        entry_control=control,
    )
    s = signal(kind, mode)
    await ex.on_signal(s)
    assert len(ex._positions) == 1
    box["armed"] = False
    # Exits must not consult the brake even if it is unavailable.
    box["mode"] = "down"
    await ex.on_tick("BTCUSDT", 60000 if kind == SignalType.SELL else 40000, {})
    assert not ex._positions
    balance = ex._balance
    with pytest.raises(RuntimeError, match="Entry blocked by control"):
        await ex.on_signal(s)
    assert ex._balance == balance and not ex._positions
    box.update(mode="ok", armed=True)
    await ex.on_signal(s)
    assert len(ex._positions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", ["market", "limit"])
async def test_spot_transport_admission_and_exit(order_type):
    box, control = control_box()
    ex = TradingExecutor(
        TradingConfig(api_key="", api_secret="", enabled=True),
        risk(),
        MagicMock(),
        entry_control=control,
    )
    client = MagicMock()
    client.get_account_info = AsyncMock(return_value=SimpleNamespace(available_balance=1000))
    # Stop at the transport boundary, after recording a real dispatch attempt.
    transport = AsyncMock(side_effect=RuntimeError("transport sentinel"))
    setattr(client, f"place_{order_type}_order", transport)
    ex._client = client
    method = getattr(ex, f"place_{order_type}_order")

    async def call(side):
        if order_type == "market":
            return await method("BTCUSDT", side, quantity=10, price=100)
        return await method("BTCUSDT", side, price=100, quantity=10)

    box["armed"] = False
    with pytest.raises(RuntimeError, match="control"):
        await call("BUY")
    assert transport.await_count == 0
    with pytest.raises(RuntimeError, match="sentinel"):
        await call("SELL")
    assert transport.await_count == 1
    box["armed"] = True
    with pytest.raises(RuntimeError, match="sentinel"):
        await call("BUY")
    assert transport.await_count == 2
    box["mode"] = "down"
    with pytest.raises(RuntimeError, match="control"):
        await call("BUY")
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_missing_malformed_and_restrictive_flags_fail_closed():
    box, control = control_box()
    assert (await control.state(force=True)).entries_allowed
    for changes in (
        {"mode": "down"},
        {"mode": "missing"},
        {"mode": "ok", "armed": True, "reduce_only": None},
        {"reduce_only": True},
        {"reduce_only": False, "armed": False},
    ):
        box.update(changes)
        assert not (await control.state(force=True)).entries_allowed


@pytest.mark.asyncio
async def test_persisted_control_recreated_executor_and_fault_metrics(tmp_path):
    """Isolated disk row survives reconstruction; no production DB is touched."""
    import sqlite3

    from src.execution.entry_admission import refresh

    path = tmp_path / "control.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE controls (agent_id TEXT, armed INTEGER, reduce_only INTEGER)")
        conn.execute("INSERT INTO controls VALUES ('all', 1, 0)")

    async def fetch(ids):
        with sqlite3.connect(path) as conn:
            rows = conn.execute("SELECT agent_id, armed, reduce_only FROM controls").fetchall()
        return [{"agent_id": a, "armed": bool(b), "reduce_only": bool(c)} for a, b, c in rows]

    metrics = MagicMock()
    first = EntryControl(agent_id="paper-one", fetch_rows=fetch)
    assert (await refresh(first, metrics)).entries_allowed
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE controls SET armed=0")
    recreated = EntryControl(agent_id="paper-one", fetch_rows=fetch)
    assert not (await refresh(recreated, metrics)).entries_allowed
    assert metrics.record_entry_control.call_args.kwargs["fault"] is False
    path.unlink()
    assert not (await refresh(recreated, metrics)).entries_allowed
    assert metrics.record_entry_control.call_args.kwargs["fault"] is True


@pytest.mark.asyncio
async def test_exit_bypasses_stalled_entry_admission():
    import asyncio
    import time

    from src.execution.paper_executor import PaperPosition

    started, release = asyncio.Event(), asyncio.Event()

    async def stalled(_ids):
        started.set()
        await release.wait()
        return [{"agent_id": "all", "armed": False, "reduce_only": False}]

    n = MagicMock()
    n.send_alert = AsyncMock()
    n.send_trade_alert = AsyncMock()
    ex = PaperExecutor(
        PaperTradingConfig(enabled=True, order_size_usdt=200, stop_loss_pct=0.01),
        risk(),
        MagicMock(),
        notifier=n,
        entry_control=EntryControl(fetch_rows=stalled),
    )
    ex._positions["ETHUSDT:spot"] = PaperPosition(
        symbol="ETHUSDT",
        side="LONG",
        quantity=1,
        entry_price=100,
        open_time=time.time(),
        sl_price=99,
    )
    entry = asyncio.create_task(ex.on_signal(signal(SignalType.BUY, "spot")))
    await asyncio.wait_for(started.wait(), 1)
    try:
        await asyncio.wait_for(ex.on_tick("ETHUSDT", 90, {}), 1)
        assert "ETHUSDT:spot" not in ex._positions
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="Entry blocked by control"):
            await entry
