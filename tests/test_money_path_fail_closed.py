"""Fail-closed regressions for the futures/spot money path.

Each test pins one behaviour that used to fail *open*: booking a number the
exchange never reported, releasing risk for volume that was still held, or
reporting a filled order as FAILED (and therefore retryable).

The theme is that a fill is final. Once the exchange accepts an order, local
uncertainty must be surfaced as an accounting gap and must never be resolved by
guessing a price, a quantity, or a balance.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.binance_client import BinancePrivateClient
from src.execution.executor import TradingConfig, TradingExecutor
from src.execution.futures_client import FuturesOrderInfo
from src.execution.futures_executor import (
    FuturesTradingConfig,
    FuturesTradingExecutor,
    PositionStateDivergence,
    PostFillAccountingError,
)
from src.portfolio.models import Position
from src.risk.entry_control import EntryControl
from src.strategy.signals import Signal, SignalType

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _risk():
    risk = MagicMock()
    for name in (
        "is_close_allowed",
        "is_trading_allowed",
        "check_max_leverage",
        "check_position_limit",
        "check_margin_usage",
        "check_liquidation_buffer",
    ):
        getattr(risk, name).return_value = (True, "")
    return risk


def _control():
    async def fetch(_ids):
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    return EntryControl(fetch_rows=fetch)


def _futures(
    mode: str = "hedge",
    *,
    legs: list[tuple[str, float]] | None = None,
    leg_reads: list[list[tuple[str, float]]] | None = None,
    balance: float | None = 1000.0,
):
    """Build a futures executor.

    ``legs`` is the held position as the exchange reports it. For a close that
    fills, pass ``leg_reads`` instead so the post-fill read can differ.
    """
    executor = FuturesTradingExecutor(
        FuturesTradingConfig(
            api_key="",
            api_secret="",
            enabled=True,
            position_mode=mode,
            symbols=["BTCUSDT"],
        ),
        _risk(),
        MagicMock(),
        entry_control=_control(),
    )
    client = MagicMock()
    if balance is None:

        async def _no_account():
            raise RuntimeError("account read unavailable")

        client.get_account_info = AsyncMock(side_effect=_no_account)
    else:
        client.get_account_info = AsyncMock(
            return_value=SimpleNamespace(total_margin_balance=balance, available_balance=balance)
        )
    reads = leg_reads if leg_reads is not None else ([legs] if legs is not None else [[]])
    responses = [
        [SimpleNamespace(position_side=leg, position_amt=amount) for leg, amount in read]
        for read in reads
    ]

    def _next_positions(*_args, **_kwargs):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    client.get_position_risk = AsyncMock(side_effect=_next_positions)
    executor._client = client
    executor._notifier = AsyncMock()
    executor._portfolio_manager = MagicMock()
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), 0.0, 0.0))
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 0.0))
    executor._portfolio_manager.update_quantity = AsyncMock()
    return executor


def _order(executed: float = 1.0, price: float | None = 100.0, status: str = "FILLED"):
    return FuturesOrderInfo(
        order_id="42",
        symbol="BTCUSDT",
        side="SELL",
        position_side="LONG",
        order_type="MARKET",
        quantity=executed,
        price=price,
        status=status,
        executed_quantity=executed,
        create_time=1,
        reduce_only=True,
    )


def _track(executor, amount: float = 1.0, entry: float = 100.0):
    executor._positions["BTCUSDT"] = {
        "amount": amount,
        "side": "LONG",
        "entry_price": entry,
        "leverage": 1,
    }


# --------------------------------------------------------------------------- #
# 1. Hedge exits: exchange-enforced reduce semantics or rejection
# --------------------------------------------------------------------------- #


async def test_hedge_exit_without_held_leg_is_rejected_before_transport():
    executor = _futures(legs=[], balance=1000.0)
    _track(executor)
    executor._client.place_order = AsyncMock()

    with pytest.raises(RuntimeError, match="no open LONG position"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
        )

    executor._client.place_order.assert_not_awaited()


async def test_hedge_exit_larger_than_the_held_leg_is_rejected():
    executor = _futures(legs=[("LONG", 0.5)])
    _track(executor)
    executor._client.place_order = AsyncMock()

    with pytest.raises(RuntimeError, match="exceeds held LONG"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
        )

    executor._client.place_order.assert_not_awaited()


async def test_hedge_exit_carries_an_explicit_leg_and_never_a_bare_both_order():
    """reduceOnly is rejected by Binance in hedge mode, so the leg is the guard."""
    executor = _futures(legs=[("LONG", 1.0)])
    _track(executor)
    executor._client.place_order = AsyncMock(side_effect=RuntimeError("transport sentinel"))

    with pytest.raises(RuntimeError, match="transport sentinel"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
        )

    kwargs = executor._client.place_order.await_args.kwargs
    assert kwargs["position_side"] == "LONG"
    assert kwargs["reduce_only"] is False  # -1106 if sent alongside a leg
    # The reduce intent was proven against holdings before submission.
    executor._client.get_position_risk.assert_awaited_with("BTCUSDT")


async def test_hedge_close_that_does_not_shrink_the_leg_is_a_divergence():
    """If the venue did not reduce the leg, nothing may be booked as closed."""
    executor = _futures(legs=[("LONG", 1.0)])
    _track(executor)
    executor._client.get_position_risk = AsyncMock(
        return_value=[SimpleNamespace(position_side="LONG", position_amt=1.0)]
    )
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0))

    with pytest.raises(PositionStateDivergence, match="did not reduce it"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
        )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    executor._risk_manager.register_close_position.assert_not_called()
    executor._portfolio_manager.close_position.assert_not_awaited()
    assert "BTCUSDT" in executor._accounting_gaps


async def test_unknown_position_mode_refuses_to_build_an_order():
    executor = _futures(mode="hedged")
    executor._client.place_order = AsyncMock()

    with pytest.raises(RuntimeError, match="Unsupported futures position mode"):
        await executor.place_futures_order(
            "BTCUSDT", "BUY", 1.0, position_side="LONG", reference_price=100.0
        )

    executor._client.place_order.assert_not_awaited()


# --------------------------------------------------------------------------- #
# 2. Overshoot is an error, never a silent unregister
# --------------------------------------------------------------------------- #


async def test_reduce_fill_overshoot_raises_and_keeps_the_risk_slot():
    executor = _futures(mode="one-way")
    _track(executor, amount=2.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=2.5))

    with pytest.raises(PositionStateDivergence, match="refusing to unregister"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 2.0, position_side="LONG", reduce_only=True
        )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(2.0)
    executor._risk_manager.register_close_position.assert_not_called()
    executor._portfolio_manager.close_position.assert_not_awaited()
    assert "overshoot" in executor._accounting_gaps["BTCUSDT"]


async def test_filled_reduce_without_executed_quantity_is_not_assumed_filled():
    executor = _futures(mode="one-way")
    _track(executor, amount=1.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=0.0))

    with pytest.raises(PostFillAccountingError, match="no executed quantity"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
        )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    executor._risk_manager.register_close_position.assert_not_called()


# --------------------------------------------------------------------------- #
# 3. Unknown fill price must not fabricate PnL
# --------------------------------------------------------------------------- #


async def test_partial_reduce_without_a_price_books_no_pnl():
    """A 0.0 fill price used to book a 100% loss into the ledger and breakers."""
    executor = _futures(mode="one-way")
    _track(executor, amount=2.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=None))
    executor._fetch_close_execution_details = AsyncMock(return_value=(None, None))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    executor._portfolio_manager.reduce_position.assert_not_awaited()
    executor._portfolio_manager.update_quantity.assert_awaited_once_with(
        "BTCUSDT", 1.0, market="futures"
    )
    executor._risk_manager.record_trade.assert_not_called()
    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    assert "PnL deferred" in executor._accounting_gaps["BTCUSDT"]


async def test_partial_reduce_prefers_the_exchange_trade_price_when_the_order_has_none():
    executor = _futures(mode="one-way")
    _track(executor, amount=2.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=None))
    executor._fetch_close_execution_details = AsyncMock(return_value=(110.0, 10.0))
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), 10.0, 1.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    assert executor._portfolio_manager.reduce_position.await_args.kwargs["price"] == 110.0
    executor._risk_manager.record_trade.assert_called_once_with("BTCUSDT", 10.0, 1000.0)


# --------------------------------------------------------------------------- #
# 4. Tracked size must never exceed the exchange-derived remainder
# --------------------------------------------------------------------------- #


async def test_stale_ledger_remainder_cannot_inflate_tracked_size():
    executor = _futures(mode="one-way")
    _track(executor, amount=2.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0))
    # Ledger is stale: it still reports the full remainder after the fill.
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), 0.0, 2.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    executor._risk_manager.adjust_open_position.assert_called_once_with(
        "BTCUSDT", 100.0, market="futures"
    )


async def test_exchange_holdings_bound_the_remainder_not_the_ledger():
    executor = _futures(leg_reads=[[("LONG", 2.0)], [("LONG", 0.5)]])
    _track(executor, amount=2.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.5))
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), 0.0, 2.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.5, position_side="LONG", reduce_only=True
    )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 5. Post-fill errors are not FAILED and not retryable
# --------------------------------------------------------------------------- #


async def test_booking_failure_after_a_fill_is_not_reported_as_failed():
    executor = _futures(mode="one-way")
    _track(executor, amount=1.0)
    executor._metrics = MagicMock()
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0))
    executor._portfolio_manager.close_position = AsyncMock(side_effect=RuntimeError("db down"))

    with pytest.raises(PostFillAccountingError) as excinfo:
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
        )

    assert excinfo.value.retryable is False
    statuses = [
        call.kwargs["status"]
        for call in executor._metrics.record_order_placed.call_args_list
        if "status" in call.kwargs
    ]
    assert statuses == ["FILLED"]
    executor._metrics.record_api_error.assert_not_called()
    executor._metrics.record_accounting_gap.assert_called()


async def test_poll_failure_after_submission_is_not_a_place_failure():
    executor = _futures(mode="one-way")
    _track(executor, amount=1.0)
    executor._metrics = MagicMock()
    executor._client.place_order = AsyncMock(return_value=_order(executed=0.0, status="NEW"))
    executor._client.get_order_status = AsyncMock(side_effect=RuntimeError("poll timeout"))

    order = await executor.place_futures_order("BTCUSDT", "BUY", 1.0, reference_price=100.0)

    assert order.status == "NEW"
    executor._metrics.record_api_error.assert_not_called()
    assert "poll failed" in executor._accounting_gaps["BTCUSDT"]


# --------------------------------------------------------------------------- #
# 6. Risk close uses a live balance or defers with a marker
# --------------------------------------------------------------------------- #


async def test_risk_close_reads_a_live_balance_when_the_cache_is_zero():
    executor = _futures(mode="one-way", balance=5000.0)
    executor._account_balance = 0.0
    _track(executor, amount=1.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0))
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 2.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    executor._client.get_account_info.assert_awaited()
    executor._risk_manager.record_trade.assert_called_once_with("BTCUSDT", 2.0, 5000.0)
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )


async def test_risk_close_without_a_balance_defers_and_replays_the_pnl():
    executor = _futures(mode="one-way", balance=None)
    _track(executor, amount=1.0)
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0))
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 2.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    # Position released (exposure is gone), PnL deferred and announced.
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )
    executor._risk_manager.record_trade.assert_not_called()
    assert executor._deferred_risk_pnl["BTCUSDT"] == pytest.approx(2.0)
    assert "no live balance" in executor._accounting_gaps["BTCUSDT"]

    # A later close with a live balance replays the deferred loss so the daily
    # circuit breaker still sees it.
    executor._client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(total_margin_balance=4000.0, available_balance=4000.0)
    )
    _track(executor, amount=1.0)
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 1.0))
    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    executor._risk_manager.record_trade.assert_called_once_with("BTCUSDT", 3.0, 4000.0)
    assert "BTCUSDT" not in executor._deferred_risk_pnl
    assert "BTCUSDT" not in executor._accounting_gaps


# --------------------------------------------------------------------------- #
# 7. SL/TP + time-stop closes reconcile executed quantity like signal exits
# --------------------------------------------------------------------------- #


def _monitor_executor(mode: str = "one-way", *, held: float = 1.0):
    executor = _futures(mode=mode, legs=[("LONG", held)] if mode == "hedge" else None)
    executor._client.get_position_risk = AsyncMock(
        return_value=[
            SimpleNamespace(
                symbol="BTCUSDT",
                position_side="LONG" if mode == "hedge" else "BOTH",
                position_amt=held,
                entry_price=100.0,
                mark_price=80.0,
                liquidation_price=50.0,
                leverage=1,
                isolated_margin=100.0,
                unrealized_pnl=-20.0,
                notional_value=80.0,
            )
        ]
    )
    executor._portfolio_manager.get_position = MagicMock(return_value=None)
    _track(executor, amount=held)
    executor._sl_tp_prices["BTCUSDT"] = {
        "sl_price": 90.0,
        "tp_price": 120.0,
        "entry_price": 100.0,
        "atr_14": 0.0,
        "high_water_mark": 100.0,
        "opened_at": 0.0,
    }
    return executor


async def test_software_stop_partial_fill_keeps_the_remainder_protected():
    executor = _monitor_executor()
    executor._client.place_order = AsyncMock(return_value=_order(executed=0.4))
    executor._client.get_user_trades = AsyncMock(return_value=[])
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), -4.0, 0.6))

    await executor._monitor_and_update()

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(0.6)
    assert executor._sl_tp_prices["BTCUSDT"]["sl_price"] == 90.0
    executor._risk_manager.register_close_position.assert_not_called()
    executor._portfolio_manager.reduce_position.assert_awaited_once()
    assert executor._portfolio_manager.reduce_position.await_args.kwargs["quantity"] == 0.4


async def test_software_stop_without_a_fill_price_does_not_book_a_mark_price_pnl():
    executor = _monitor_executor()
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=None))
    executor._client.get_user_trades = AsyncMock(return_value=[])

    await executor._monitor_and_update()

    executor._portfolio_manager.close_position.assert_not_awaited()
    executor._portfolio_manager.update_quantity.assert_awaited_once_with(
        "BTCUSDT", 0.0, market="futures"
    )
    executor._risk_manager.record_trade.assert_not_called()
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )
    assert "BTCUSDT" in executor._accounting_gaps


async def test_software_stop_overshoot_keeps_tracking_and_protection():
    executor = _monitor_executor()
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.5))
    executor._client.get_user_trades = AsyncMock(return_value=[])

    await executor._monitor_and_update()

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    assert executor._sl_tp_prices["BTCUSDT"]["sl_price"] == 90.0
    executor._risk_manager.register_close_position.assert_not_called()
    assert "overshoot" in executor._accounting_gaps["BTCUSDT"]


async def test_software_stop_closes_a_short_with_the_short_leg():
    executor = _monitor_executor(mode="hedge", held=-1.0)
    executor._client.get_position_risk = AsyncMock(
        return_value=[
            SimpleNamespace(
                symbol="BTCUSDT",
                position_side="SHORT",
                position_amt=-1.0,
                entry_price=100.0,
                mark_price=120.0,
                liquidation_price=150.0,
                leverage=1,
                isolated_margin=100.0,
                unrealized_pnl=-20.0,
                notional_value=120.0,
            )
        ]
    )
    # Trigger through the take-profit branch: the software comparisons are
    # long-only, but the *close shape* must still follow the position's sign.
    executor._sl_tp_prices["BTCUSDT"].update(
        {"sl_price": 0.0, "tp_price": 110.0, "entry_price": 100.0}
    )
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0))

    await executor._monitor_and_update()

    kwargs = executor._client.place_order.await_args.kwargs
    assert kwargs["side"] == "BUY"
    assert kwargs["position_side"] == "SHORT"
    assert kwargs["quantity"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 7b. Signal exits: post-fill failures are not "signal rejected"
# --------------------------------------------------------------------------- #


def _sell_signal() -> Signal:
    return Signal(
        type=SignalType.SELL,
        symbol="BTCUSDT",
        price=100.0,
        confidence=0.8,
        reason="exit",
        indicators={},
        trading_mode="futures",
    )


def _signal_executor(executor):
    """Wire the exchange/ledger state a SELL signal needs to be approved."""
    executor._client.get_position_risk = AsyncMock(
        return_value=[
            SimpleNamespace(
                symbol="BTCUSDT",
                position_side="LONG",
                position_amt=1.0,
                entry_price=100.0,
                mark_price=100.0,
                liquidation_price=50.0,
                leverage=1,
                isolated_margin=100.0,
                unrealized_pnl=0.0,
                notional_value=100.0,
            )
        ]
    )
    executor._portfolio_manager.get_position = MagicMock(
        return_value=Position(
            symbol="BTCUSDT", quantity=1.0, entry_price=100.0, position_side="LONG"
        )
    )
    _track(executor, amount=1.0)
    return executor


async def test_signal_exit_booking_failure_is_not_reported_as_rejected():
    executor = _signal_executor(_futures(mode="one-way"))
    executor.place_futures_order = AsyncMock(
        side_effect=PostFillAccountingError("BTCUSDT SELL filled but booking failed")
    )

    await executor.on_signal(_sell_signal())

    alerts = [call.args[0] for call in executor._notifier.send_alert.await_args_list]
    assert len(alerts) == 1
    assert "accounting pending" in alerts[0]
    assert "rejected" not in alerts[0].lower()
    assert "do not retry" in alerts[0].lower()


async def test_signal_exit_with_unknown_pnl_says_so_instead_of_an_entry_alert():
    executor = _signal_executor(_futures(mode="one-way"))
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=None))
    executor._fetch_close_execution_details = AsyncMock(return_value=(None, None))

    await executor.on_signal(_sell_signal())

    alerts = [call.args[0] for call in executor._notifier.send_alert.await_args_list]
    assert any("PnL unknown" in alert for alert in alerts)
    executor._notifier.send_trade_alert.assert_not_awaited()
    executor._portfolio_manager.close_position.assert_not_awaited()
    assert "BTCUSDT" in executor._accounting_gaps


# --------------------------------------------------------------------------- #
# 8. Spot entries get the same prepare seam as futures
# --------------------------------------------------------------------------- #


class _PostSession:
    """Session stand-in: records the POST and never reaches the network."""

    def __init__(self, events):
        self._events = events

    def post(self, *_args, **_kwargs):
        self._events.append("post")
        raise RuntimeError("POST sentinel")


def _spot(events: list[str], *, real_post: bool = False):
    executor = TradingExecutor(
        TradingConfig(api_key="k", api_secret="s", enabled=True, symbols=["BTCUSDT"]),
        _risk(),
        MagicMock(),
        MagicMock(),
        entry_control=_control(),
    )
    client = BinancePrivateClient("dummy", "dummy")
    client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(total_margin_balance=1000.0, available_balance=1000.0)
    )
    if not real_post:
        client.place_market_order = AsyncMock(side_effect=RuntimeError("place sentinel"))
    cast(Any, client)._session = _PostSession(events)

    async def sync():
        events.append("sync")

    client._ensure_time_sync = sync
    executor._client = client
    return executor, client


async def test_spot_entry_syncs_before_the_final_admission_read():
    events: list[str] = []

    async def fetch(_ids):
        events.append("admit")
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    executor, _client = _spot(events)
    executor._entry_control = EntryControl(fetch_rows=fetch)

    with pytest.raises(RuntimeError, match="place sentinel"):
        await executor.place_market_order("BTCUSDT", "BUY", quantity=0.01, price=100.0)

    assert events == ["sync", "admit"]


async def test_spot_entry_with_a_stale_prepared_clock_fails_closed():
    events: list[str] = []

    async def fetch(_ids):
        events.append("admit")
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    executor, client = _spot(events)
    executor._entry_control = EntryControl(fetch_rows=fetch)
    client.prepared_request_age_s = lambda: 3.0

    with pytest.raises(RuntimeError, match="timestamp freshness window"):
        await executor.place_market_order("BTCUSDT", "BUY", quantity=0.01, price=100.0)

    assert events == ["sync", "admit", "sync", "admit"]
    executor._client.place_market_order.assert_not_awaited()


async def test_spot_time_sync_runs_before_admission_and_never_after():
    """The signed POST must not be preceded by an awaited clock sync."""
    events: list[str] = []

    async def fetch(_ids):
        events.append("admit")
        return [{"agent_id": "all", "armed": True, "reduce_only": False}]

    executor, client = _spot(events, real_post=True)
    executor._entry_control = EntryControl(fetch_rows=fetch)
    cast(Any, client)._session = _PostSession(events)

    with pytest.raises(RuntimeError, match="POST sentinel"):
        await executor.place_market_order("BTCUSDT", "BUY", quantity=0.01, price=100.0)

    assert events == ["sync", "admit", "post"]


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


async def test_spot_prepared_clock_is_single_use_at_the_client():
    client = BinancePrivateClient("dummy", "dummy")
    cast(Any, client)._session = _GetSession()
    syncs = 0

    async def counting_sync():
        nonlocal syncs
        syncs += 1

    client._ensure_time_sync = counting_sync

    await client.prepare_signed_request()
    assert syncs == 1
    assert client.prepared_request_age_s() is not None

    await client._request("GET", "/api/v3/account", {}, signed=True)
    assert syncs == 1
    assert client.prepared_request_age_s() is None

    await client._request("GET", "/api/v3/account", {}, signed=True)
    assert syncs == 2


async def test_spot_sell_path_is_unaffected_by_the_entry_seam():
    executor, client = _spot([])
    executor._client.place_market_order = AsyncMock(side_effect=RuntimeError("sell sentinel"))

    with pytest.raises(RuntimeError, match="sell sentinel"):
        await executor.place_market_order("BTCUSDT", "SELL", quantity=0.01, price=100.0)

    assert client.prepared_request_age_s() is None


def _staged(executor):
    """Give the executor a staged-order manager that records transitions."""
    manager = MagicMock()
    manager.stage = AsyncMock(return_value=SimpleNamespace(order_id="stage-1"))
    manager.commit = AsyncMock()
    manager.mark_executing = AsyncMock()
    manager.mark_rejected = AsyncMock()
    executor._staged_manager = manager
    return manager


async def test_spot_clock_sync_failure_rejects_the_stage_without_posting():
    events: list[str] = []
    executor, client = _spot(events)
    manager = _staged(executor)

    async def boom():
        raise RuntimeError("time endpoint down")

    client._ensure_time_sync = boom

    with pytest.raises(RuntimeError, match="time endpoint down"):
        await executor.place_market_order("BTCUSDT", "BUY", quantity=0.01, price=100.0)

    client.place_market_order.assert_not_awaited()
    reasons = [call.args for call in manager.mark_rejected.await_args_list]
    assert reasons[0] == ("stage-1", "exchange clock sync failed before submission")
    assert "post" not in events


async def test_spot_admission_cancel_still_rejects_the_staged_order():
    executor, _client = _spot([])

    async def cancelled(_control, _metrics, _symbol):
        raise asyncio.CancelledError

    executor._staged_manager = _staged(executor)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.execution.executor.admit", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await executor.place_market_order("BTCUSDT", "BUY", quantity=0.01, price=100.0)

    executor._staged_manager.mark_rejected.assert_awaited_once_with(
        "stage-1", "cancelled before submission"
    )
