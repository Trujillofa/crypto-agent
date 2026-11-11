"""Exits ignore open-risk gates, and partial closes keep the remainder.

Two money-path invariants:

1. A reduce-only order must never be blocked by limits that describe *opening*
   risk (account read, leverage, margin, kill switch). Only the close gate and
   a reconciliation block may refuse it.
2. A filled reduce-only order that closes less than the tracked position leaves
   live exposure. Local, portfolio and risk state must keep that remainder
   instead of unregistering the whole position.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.execution.futures_client import FuturesOrderInfo
from src.execution.futures_executor import FuturesTradingConfig, FuturesTradingExecutor
from src.portfolio.manager import PortfolioManager
from src.portfolio.models import Position
from src.risk.entry_control import EntryControl
from src.risk.manager import RiskManager


def _risk():
    risk = MagicMock()
    for name in (
        "is_close_allowed",
        "is_trading_allowed",
        "check_max_leverage",
        "check_position_limit",
        "check_margin_usage",
    ):
        getattr(risk, name).return_value = (True, "")
    return risk


def _executor(control: EntryControl, risk: MagicMock | None = None):
    executor = FuturesTradingExecutor(
        FuturesTradingConfig(api_key="", api_secret="", enabled=True, position_mode="hedge"),
        risk or _risk(),
        MagicMock(),
        entry_control=control,
    )
    executor._client = MagicMock()
    executor._client.get_account_info = AsyncMock(
        return_value=SimpleNamespace(total_margin_balance=1000, available_balance=1000)
    )
    return executor


def _filled(order_id="1", side="SELL", price=100.0, executed=1.0):
    return FuturesOrderInfo(
        order_id=order_id,
        symbol="BTCUSDT",
        side=side,
        position_side="LONG",
        order_type="MARKET",
        quantity=executed,
        price=price,
        status="FILLED",
        executed_quantity=executed,
        create_time=1,
        reduce_only=True,
    )


def _no_reads():
    async def fetch(_ids):
        raise AssertionError("exit must not consult the entry brake")

    return EntryControl(fetch_rows=fetch)


async def test_exit_skips_account_and_leverage_checks_and_reaches_transport():
    risk = _risk()
    risk.check_max_leverage.return_value = (False, "leverage blocked")
    risk.is_trading_allowed.return_value = (False, "kill switch active")
    executor = _executor(_no_reads(), risk)
    executor._client.place_order = AsyncMock(side_effect=RuntimeError("transport sentinel"))

    with pytest.raises(RuntimeError, match="transport sentinel"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1, position_side="LONG", reduce_only=True
        )

    risk.is_close_allowed.assert_called_once_with()
    risk.is_trading_allowed.assert_not_called()
    risk.check_max_leverage.assert_not_called()
    risk.check_position_limit.assert_not_called()
    risk.check_margin_usage.assert_not_called()
    executor._client.get_account_info.assert_not_awaited()
    executor._client.place_order.assert_awaited_once()


async def test_reconciliation_block_still_refuses_an_exit():
    risk = _risk()
    risk.is_close_allowed.return_value = (False, "Reconciliation block: divergence")
    executor = _executor(_no_reads(), risk)
    executor._client.place_order = AsyncMock(side_effect=RuntimeError("transport sentinel"))

    with pytest.raises(RuntimeError, match="Reconciliation block"):
        await executor.place_futures_order(
            "BTCUSDT", "SELL", 1, position_side="LONG", reduce_only=True
        )

    executor._client.place_order.assert_not_awaited()


async def test_partial_hedge_close_keeps_remaining_volume_and_risk_registered():
    executor = _executor(_no_reads())
    executor._positions["BTCUSDT"] = {
        "amount": 2.0,
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 1,
    }
    executor._portfolio_manager = MagicMock()
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), 0.0, 1.0))
    executor._portfolio_manager.close_position = AsyncMock()
    executor._client.place_order = AsyncMock(return_value=_filled())

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    risk = cast(MagicMock, executor._risk_manager)
    risk.register_close_position.assert_not_called()
    risk.adjust_open_position.assert_called_once_with("BTCUSDT", 100.0, market="futures")
    risk.register_open_position.assert_not_called()
    executor._portfolio_manager.reduce_position.assert_awaited_once()
    assert executor._portfolio_manager.reduce_position.await_args.kwargs["quantity"] == 1.0
    executor._portfolio_manager.close_position.assert_not_awaited()


async def test_full_hedge_close_unregisters_only_when_flat():
    executor = _executor(_no_reads())
    executor._positions["BTCUSDT"] = {
        "amount": 1.0,
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 1,
    }
    executor._portfolio_manager = MagicMock()
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 2.0))
    executor._portfolio_manager.reduce_position = AsyncMock()
    executor._client.place_order = AsyncMock(return_value=_filled())

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )

    assert "BTCUSDT" not in executor._positions
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )
    executor._portfolio_manager.close_position.assert_awaited_once()
    executor._portfolio_manager.reduce_position.assert_not_awaited()


async def test_second_partial_fill_releases_the_position_only_when_flat():
    executor = _executor(_no_reads())
    executor._positions["BTCUSDT"] = {
        "amount": 2.0,
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 1,
    }
    executor._portfolio_manager = MagicMock()
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), 0.0, 1.0))
    executor._portfolio_manager.close_position = AsyncMock(return_value=(MagicMock(), 2.0))
    executor._client.place_order = AsyncMock(
        side_effect=[_filled(executed=1.0), _filled(executed=1.0)]
    )

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )
    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(1.0)
    executor._risk_manager.register_close_position.assert_not_called()

    await executor.place_futures_order(
        "BTCUSDT", "SELL", 1.0, position_side="LONG", reduce_only=True
    )
    assert "BTCUSDT" not in executor._positions
    executor._risk_manager.register_close_position.assert_called_once_with(
        "BTCUSDT", market="futures"
    )


async def test_signal_partial_exit_restores_software_stop_for_the_remainder():
    from src.execution.futures_client import FuturesPositionInfo
    from src.strategy.signals import Signal, SignalType

    risk = _risk()
    executor = FuturesTradingExecutor(
        FuturesTradingConfig(
            api_key="",
            api_secret="",
            enabled=True,
            position_mode="hedge",
            symbols=["BTCUSDT"],
        ),
        risk,
        MagicMock(),
        entry_control=_no_reads(),
    )
    executor._client = MagicMock()
    executor._client.get_position_risk = AsyncMock(
        return_value=[
            FuturesPositionInfo(
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
    executor._client.place_order = AsyncMock(return_value=_filled(executed=0.4))
    executor._notifier = AsyncMock()
    executor._portfolio_manager = MagicMock()
    executor._portfolio_manager.get_position = MagicMock(
        return_value=Position(
            symbol="BTCUSDT", quantity=1.0, entry_price=100.0, position_side="LONG"
        )
    )
    executor._portfolio_manager.reduce_position = AsyncMock(return_value=(MagicMock(), -0.024, 0.6))
    executor._positions["BTCUSDT"] = {
        "amount": 1.0,
        "side": "LONG",
        "entry_price": 100.0,
        "leverage": 1,
    }
    stopped = {
        "sl_price": 90.0,
        "tp_price": 120.0,
        "entry_price": 100.0,
        "atr_14": 5.0,
        "high_water_mark": 105.0,
        "opened_at": 1.0,
    }
    executor._sl_tp_prices["BTCUSDT"] = dict(stopped)

    await executor.on_signal(
        Signal(SignalType.SELL, "BTCUSDT", 100.0, 1.0, "test", {}, trading_mode="futures")
    )

    assert executor._positions["BTCUSDT"]["amount"] == pytest.approx(0.6)
    assert executor._sl_tp_prices["BTCUSDT"] == stopped
    risk.register_close_position.assert_not_called()


@pytest.mark.asyncio
async def test_portfolio_reduce_position_keeps_remainder_open_and_books_partial_pnl():
    manager = PortfolioManager({})

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 7

    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    with patch("src.portfolio.manager.get_pool", return_value=mock_pool):
        async with manager:
            position = await manager.open_position(
                symbol="BTCUSDT",
                quantity=2.0,
                price=100.0,
                market="futures",
                position_side="LONG",
            )

            reduced, pnl, remaining = await manager.reduce_position(
                symbol="BTCUSDT",
                quantity=0.5,
                price=110.0,
                order_id="fill-1",
                market="futures",
                closing_side="SELL",
            )

    assert reduced.quantity == pytest.approx(1.5)
    assert remaining == pytest.approx(1.5)
    assert position.is_open
    # (110 - 100) * 0.5 - (100 + 110) * 0.5 * 0.0004
    assert pnl == pytest.approx(4.958)
    tracked_pos = manager.get_position("BTCUSDT", market="futures")
    assert tracked_pos is not None
    assert tracked_pos.quantity == pytest.approx(1.5)

    update_call = mock_conn.execute.await_args_list[-2]
    assert "UPDATE positions" in update_call.args[0]
    assert update_call.args[1] == pytest.approx(1.5)

    trade_call = mock_conn.execute.await_args_list[-1]
    assert "INSERT INTO trades" in trade_call.args[0]
    assert trade_call.args[5] == pytest.approx(0.5)


def test_adjust_open_position_preserves_entry_time_and_price(tmp_path):
    manager = RiskManager(state_path=tmp_path / "risk.json")
    manager.register_open_position("BTCUSDT", 200.0, 100.0, market="futures")
    key = manager._canonical_position_key("BTCUSDT", "futures")
    entry_time = manager._positions[key]["entry_time"]

    assert manager.adjust_open_position("BTCUSDT", 100.0, market="futures") is True

    tracked = manager._positions[key]
    assert tracked["quantity_usdt"] == pytest.approx(100.0)
    assert tracked["entry_price"] == pytest.approx(100.0)
    assert tracked["entry_time"] == entry_time
    assert manager.get_position_notional("BTCUSDT", market="futures") == pytest.approx(100.0)
    assert manager.adjust_open_position("ETHUSDT", 10.0, market="futures") is False


@pytest.mark.asyncio
async def test_portfolio_reduce_position_refuses_to_silently_close_everything():
    manager = PortfolioManager({})
    manager._positions[("futures", "BTCUSDT")] = Position(
        id=1,
        symbol="BTCUSDT",
        market="futures",
        entry_price=100.0,
        quantity=2.0,
        position_side="LONG",
    )

    with pytest.raises(ValueError, match="use close_position"):
        await manager.reduce_position(symbol="BTCUSDT", quantity=2.0, price=110.0, market="futures")

    still_open = manager.get_position("BTCUSDT", market="futures")
    assert still_open is not None
    assert still_open.quantity == pytest.approx(2.0)
