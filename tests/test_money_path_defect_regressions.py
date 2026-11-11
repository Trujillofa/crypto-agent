"""Regressions for the ten entry-control defects proven against cd3be5e.

Each test pins one way the money path used to fail *unsafe* rather than closed:

* D2  protection was cancelled before the exit was proven, so a refused or
      failed exit left a leveraged position with nothing on it,
* D3  a tracker/risk divergence had no repair, so a phantom slot blocked every
      later entry forever,
* D10 the same divergence silently swallowed the next signal,
* D1  a partial reduce with no tracker entry was booked as a full close,
* D6  an entry fill with no price wrote a 0.0 entry price into the ledger,
* D7  software exits closed ``positions[0]``, which in hedge mode can be
      another agent's leg,
* D9  a partial SL/TP close never reached the risk manager,
* D4  the balance gate was missing on the partial-reduce path,
* D5  deferred PnL was stranded per symbol and lost on restart,
* D7b a hedge close released the risk slot without a venue-flat confirmation.

Nothing here touches the network: every exchange seam is an ``AsyncMock``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.execution.futures_client import FuturesOrderInfo
from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor
from src.portfolio.models import Position
from src.risk.entry_control import EntryControl
from src.risk.manager import RiskManager
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


def _futures(mode: str = "one-way", *, legs=None, balance: float | None = 1000.0):
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
            return_value=SimpleNamespace(
                total_margin_balance=balance, available_balance=balance
            )
        )
    client.get_position_risk = AsyncMock(
        return_value=[
            SimpleNamespace(
                symbol="BTCUSDT",
                position_side=side,
                position_amt=amount,
                entry_price=100.0,
                mark_price=80.0,
                liquidation_price=50.0,
                leverage=1,
                unrealized_pnl=-20.0,
                notional_value=80.0,
            )
            for side, amount in (legs or [])
        ]
    )
    client.get_user_trades = AsyncMock(return_value=[])
    client.place_order = AsyncMock(return_value=_order())
    executor._client = client
    executor._notifier = AsyncMock()
    executor._portfolio_manager = MagicMock()
    executor._portfolio_manager.get_position = MagicMock(return_value=None)
    executor._portfolio_manager.reduce_position = AsyncMock(
        return_value=(MagicMock(), 0.0, 0.0)
    )
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 0.0))
    executor._portfolio_manager.update_quantity = AsyncMock()
    executor._portfolio_manager.open_position = AsyncMock(return_value=MagicMock())
    return executor


def _order(executed: float = 1.0, price: float | None = 100.0, side: str = "SELL"):
    return FuturesOrderInfo(
        order_id="42",
        symbol="BTCUSDT",
        side=side,
        position_side="LONG",
        order_type="MARKET",
        quantity=executed,
        price=price,
        status="FILLED",
        executed_quantity=executed,
        create_time=1,
        reduce_only=side == "SELL",
    )


def _track(executor, amount: float = 1.0, entry: float = 100.0, symbol: str = "BTCUSDT"):
    executor._positions[symbol] = {
        "amount": amount,
        "side": "LONG" if amount >= 0 else "SHORT",
        "entry_price": entry,
        "leverage": 1,
    }


def _bracket(executor, *, sl: float = 90.0, tp: float = 120.0, symbol: str = "BTCUSDT"):
    executor._sl_tp_prices[symbol] = {
        "sl_price": sl,
        "tp_price": tp,
        "entry_price": 100.0,
        "atr_14": 0.0,
        "high_water_mark": 100.0,
        "opened_at": 0.0,
    }


def _monitor(executor, legs, mark: float = 80.0):
    """Give the monitor a venue book plus the tracked/bracketed long it owns."""
    reads = legs if legs and isinstance(legs[0], list) else [legs]
    responses = [
        [
            SimpleNamespace(
                symbol="BTCUSDT",
                position_side=side,
                position_amt=amount,
                entry_price=100.0,
                mark_price=mark,
                liquidation_price=50.0,
                leverage=1,
                unrealized_pnl=-20.0,
                notional_value=80.0,
            )
            for side, amount in read
        ]
        for read in reads
    ]

    def _next(*_args, **_kwargs):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    executor._client.get_position_risk = AsyncMock(side_effect=_next)
    _track(executor, amount=1.0)
    _bracket(executor)
    return executor


def _sell_signal(symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        type=SignalType.SELL,
        symbol=symbol,
        price=100.0,
        confidence=0.8,
        reason="exit",
        indicators={},
        trading_mode="futures",
    )


def _signal_ready(executor):
    """Wire the ledger + hedge leg a SELL signal needs to be approved."""
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
    _bracket(executor)
    executor._sl_tp_orders["BTCUSDT"] = {"sl_order_id": "sl_1", "tp_order_id": "tp_1"}
    return executor


# --------------------------------------------------------------------------- #
# D2 - refuse before cancel, and re-arm if the exit dies after the cancel
# --------------------------------------------------------------------------- #


async def test_refused_hedge_exit_never_cancels_the_bracket():
    executor = _signal_ready(_futures(mode="hedge"))
    # The venue no longer holds the LONG leg: the exit cannot be proven.
    executor._client.get_position_risk = AsyncMock(return_value=[])

    await executor.on_signal(_sell_signal())

    executor._client.cancel_order.assert_not_called()
    executor._client.place_order.assert_not_awaited()
    assert executor._sl_tp_prices["BTCUSDT"]["sl_price"] == 90.0
    assert "BTCUSDT" in executor._sl_tp_orders


async def test_exit_that_fails_after_the_cancel_re_arms_protection():
    executor = _signal_ready(_futures(mode="hedge"))
    executor._client.place_order = AsyncMock(side_effect=RuntimeError("transport sentinel"))
    executor._client.place_algo_order = AsyncMock(
        side_effect=[SimpleNamespace(algo_id="rearm_sl"), SimpleNamespace(algo_id="rearm_tp")]
    )

    await executor.on_signal(_sell_signal())

    # The cancelled software bracket is back, so the position is not naked.
    assert executor._sl_tp_prices["BTCUSDT"]["sl_price"] == 90.0
    assert executor._sl_tp_prices["BTCUSDT"]["tp_price"] == 120.0
    # ... and so are the venue conditionals, at their original triggers.
    assert executor._sl_tp_orders["BTCUSDT"]["sl_order_id"] == "rearm_sl"
    assert executor._sl_tp_orders["BTCUSDT"]["tp_order_id"] == "rearm_tp"
    assert executor._client.place_algo_order.await_count == 2


# --------------------------------------------------------------------------- #
# D3 / D10 - a venue-flat symbol repairs the phantom tracker and risk slot
# --------------------------------------------------------------------------- #


async def test_venue_flat_releases_the_phantom_tracker_and_risk_slot():
    executor = _futures(mode="one-way", legs=[])
    _track(executor, amount=1.0)

    await executor._monitor_and_update()

    assert "BTCUSDT" not in executor._positions
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )

    # Bounded: a second pass must not release the slot again.
    await executor._monitor_and_update()
    assert executor._risk_manager.register_close_position.call_count == 1


async def test_flat_symbol_with_only_the_other_hedge_leg_still_releases_ours():
    """Our LONG is gone while another agent still holds a SHORT leg."""
    executor = _futures(mode="hedge", legs=[("SHORT", -0.5)])
    _track(executor, amount=1.0)

    await executor._monitor_and_update()

    assert "BTCUSDT" not in executor._positions
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )


# --------------------------------------------------------------------------- #
# D1 - a partial reduce without a tracker entry is not a full close
# --------------------------------------------------------------------------- #


async def test_partial_reduce_without_a_tracker_entry_is_not_a_full_close():
    executor = _futures(mode="one-way", legs=[])
    executor._portfolio_manager.get_position = MagicMock(
        return_value=Position(
            symbol="BTCUSDT", quantity=1.0, entry_price=100.0, position_side="LONG"
        )
    )
    executor._portfolio_manager.reduce_position = AsyncMock(
        return_value=(MagicMock(), 4.0, 0.6)
    )
    executor._client.place_order = AsyncMock(return_value=_order(executed=0.4, price=110.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 0.4, position_side="LONG", reduce_only=True
    )

    executor._portfolio_manager.close_position.assert_not_awaited()
    executor._portfolio_manager.reduce_position.assert_awaited_once()
    assert executor._portfolio_manager.reduce_position.await_args.kwargs["quantity"] == 0.4
    executor._risk_manager.register_close_position.assert_not_called()


# --------------------------------------------------------------------------- #
# D6 - an unknown entry price is a gap, never a 0.0 ledger price
# --------------------------------------------------------------------------- #


async def test_entry_fill_without_a_price_uses_the_reference_and_flags_it():
    executor = _futures(mode="one-way")

    await executor._register_open_fill(
        "BTCUSDT", "BUY", _order(executed=1.0, price=None, side="BUY"), 1.0, "LONG", 100.0
    )

    kwargs = executor._portfolio_manager.open_position.await_args.kwargs
    assert kwargs["price"] == 100.0
    executor._risk_manager.register_open_position.assert_called_once_with(
        "BTCUSDT", 100.0, 100.0, market="futures"
    )
    assert "BTCUSDT" in executor._accounting_gaps


async def test_entry_fill_with_no_price_anywhere_books_no_zero_price_row():
    executor = _futures(mode="one-way")

    await executor._register_open_fill(
        "BTCUSDT", "BUY", _order(executed=1.0, price=None, side="BUY"), 1.0, "LONG", None
    )

    executor._portfolio_manager.open_position.assert_not_awaited()
    executor._risk_manager.register_open_position.assert_not_called()
    assert executor._positions["BTCUSDT"]["amount"] == 1.0
    assert "BTCUSDT" in executor._accounting_gaps


# --------------------------------------------------------------------------- #
# D7 - the leg under the close comes from the owned position, not positions[0]
# --------------------------------------------------------------------------- #


async def test_software_stop_closes_the_owned_long_and_not_the_foreign_short():
    executor = _monitor(_futures(mode="hedge"), [("SHORT", -0.5), ("LONG", 1.0)])

    await executor._monitor_and_update()

    kwargs = executor._client.place_order.await_args.kwargs
    assert kwargs["side"] == "SELL"
    assert kwargs["position_side"] == "LONG"
    assert kwargs["quantity"] == 1.0


# --------------------------------------------------------------------------- #
# D9 - partial SL/TP closes book their PnL to the risk manager
# --------------------------------------------------------------------------- #


async def test_partial_software_close_books_the_pnl_to_the_risk_manager():
    executor = _monitor(_futures(mode="one-way"), [("BOTH", 1.0)])
    executor._client.place_order = AsyncMock(return_value=_order(executed=0.4, price=85.0))
    executor._portfolio_manager.reduce_position = AsyncMock(
        return_value=(MagicMock(), -6.0, 0.6)
    )

    await executor._monitor_and_update()

    executor._risk_manager.record_trade.assert_called_once()
    args = executor._risk_manager.record_trade.call_args.args
    assert args[0] == "BTCUSDT"
    assert args[1] == -6.0


# --------------------------------------------------------------------------- #
# D4 - the balance gate applies on the partial-reduce path too
# --------------------------------------------------------------------------- #


async def test_partial_reduce_still_trips_the_breakers_without_a_balance(tmp_path):
    executor = _futures(mode="one-way", legs=[], balance=None)
    executor._risk_manager = RiskManager(state_path=tmp_path / "risk.json")
    _track(executor, amount=2.0)
    executor._portfolio_manager.get_position = MagicMock(
        return_value=Position(
            symbol="BTCUSDT", quantity=2.0, entry_price=100.0, position_side="LONG"
        )
    )
    executor._portfolio_manager.reduce_position = AsyncMock(
        return_value=(MagicMock(), -99.0, 1.0)
    )
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=1.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    summary = executor._risk_manager.get_risk_summary()
    assert summary["daily_pnl"] == -99.0
    assert summary["circuit_breakers"]["max_single_loss"] is True
    assert "BTCUSDT" in executor._accounting_gaps


# --------------------------------------------------------------------------- #
# D5 - deferred PnL is account-wide, persisted, and replayed after a restart
# --------------------------------------------------------------------------- #


async def test_deferred_pnl_is_persisted_and_replayed_on_another_symbol(tmp_path):
    state_path = tmp_path / "risk.json"
    executor = _futures(mode="one-way", legs=[], balance=None)
    executor._risk_manager = RiskManager(state_path=state_path)
    _track(executor, amount=1.0, entry=100.0)
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), -40.0))
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=60.0))

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    # No balance: the loss is deferred, not dropped, and it is on disk.
    assert executor._deferred_risk_pnl["BTCUSDT"] == -40.0
    assert executor._risk_manager.deferred_realized_pnl()["BTCUSDT"] == -40.0

    # A restart replays the durable copy instead of forgetting the loss.
    restarted = _futures(mode="one-way", legs=[], balance=5000.0)
    restarted._risk_manager = RiskManager(state_path=state_path)
    restarted._hydrate_deferred_pnl()
    assert restarted._deferred_risk_pnl["BTCUSDT"] == -40.0

    # Any later close with a live balance settles it, whatever the symbol.
    restarted._account_balance = 5000.0
    _track(restarted, amount=1.0, entry=50.0, symbol="ETHUSDT")
    restarted._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 10.0))
    restarted._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=60.0))

    await restarted.place_futures_order(
        "ETHUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    assert restarted._risk_manager.get_risk_summary()["daily_pnl"] == -30.0
    assert not restarted._deferred_risk_pnl


# --------------------------------------------------------------------------- #
# D7b - a hedge close needs the venue to confirm the leg is gone
# --------------------------------------------------------------------------- #


async def test_hedge_software_close_keeps_the_slot_until_the_venue_is_flat():
    executor = _monitor(_futures(mode="hedge"), [("LONG", 1.0)])
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=80.0))

    await executor._monitor_and_update()

    # The venue still reports the LONG leg, so nothing may be released.
    executor._risk_manager.register_close_position.assert_not_called()
    assert executor._positions["BTCUSDT"]["amount"] == 1.0
    assert executor._sl_tp_prices["BTCUSDT"]["sl_price"] == 90.0


async def test_hedge_software_close_releases_once_the_venue_is_flat():
    # Loop read, pre-submit holdings proof, then the post-fill flat confirmation.
    executor = _monitor(
        _futures(mode="hedge"), [[("LONG", 1.0)], [("LONG", 1.0)], [("LONG", 0.0)]]
    )
    executor._client.place_order = AsyncMock(return_value=_order(executed=1.0, price=80.0))

    await executor._monitor_and_update()

    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )
    assert "BTCUSDT" not in executor._positions


# --------------------------------------------------------------------------- #
# A live venue conditional wins the trigger the software stop covers
# --------------------------------------------------------------------------- #


async def test_software_stop_stands_down_for_a_live_exchange_conditional():
    executor = _monitor(_futures(mode="one-way"), [("BOTH", 1.0)])
    executor._sl_tp_orders["BTCUSDT"] = {
        "sl_order_id": "algo_sl",
        "sl_is_algo": True,
        "tp_order_id": "algo_tp",
        "tp_is_algo": True,
    }

    await executor._monitor_and_update()

    # The venue STOP_MARKET owns this trigger: no cancel, no duplicate close.
    executor._client.place_order.assert_not_awaited()
    executor._client.cancel_order.assert_not_called()
    assert executor._sl_tp_prices["BTCUSDT"]["sl_price"] == 90.0
