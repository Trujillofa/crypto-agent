from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.portfolio.manager import PortfolioManager


@pytest.mark.asyncio
async def test_portfolio_manager_open_close():
    """PortfolioManager opens/closes positions with pooled asyncpg mock."""
    manager = PortfolioManager({})

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    # _fetch_open_positions returns empty list (no existing positions)
    mock_conn.fetch.return_value = []
    # open_position uses fetchval to get the RETURNING id
    mock_conn.fetchval.return_value = 1

    # transaction() must return an async context manager
    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    with patch("src.portfolio.manager.get_pool", return_value=mock_pool):
        async with manager:
            assert manager.has_position("BTCUSDT") is False

            position = await manager.open_position(symbol="BTCUSDT", quantity=2.0, price=100.0)
            assert position.entry_price == 100.0
            assert position.quantity == 2.0
            assert position.market == "spot"
            assert manager.has_position("BTCUSDT") is True

            closed_position, pnl = await manager.close_position(symbol="BTCUSDT", price=110.0)
            assert closed_position.is_closed is True
            assert closed_position.realized_pnl == pytest.approx(19.832)
            assert pnl == pytest.approx(19.832)
            assert manager.has_position("BTCUSDT") is False


@pytest.mark.asyncio
async def test_portfolio_manager_close_uses_pnl_override():
    """close_position should persist supplied PnL override when provided."""
    manager = PortfolioManager({})

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 11

    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    with patch("src.portfolio.manager.get_pool", return_value=mock_pool):
        async with manager:
            await manager.open_position(
                symbol="BTCUSDT",
                quantity=0.1,
                price=50000.0,
                market="futures",
                position_side="SHORT",
            )

            closed_position, pnl = await manager.close_position(
                symbol="BTCUSDT",
                price=49000.0,
                market="futures",
                realized_pnl_override=29.804,
            )

            assert closed_position.is_closed is True
            assert closed_position.realized_pnl == pytest.approx(29.804)
            assert pnl == pytest.approx(29.804)


@pytest.mark.asyncio
async def test_portfolio_manager_scopes_symbols_for_non_default_agent():
    """Non-default agents must write scoped position symbols."""
    manager = PortfolioManager({}, agent_id="agent2")

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 1

    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    with patch("src.portfolio.manager.get_pool", return_value=mock_pool):
        async with manager:
            await manager.open_position(
                symbol="BTCUSDT:spot",
                quantity=1.0,
                price=100.0,
            )

            insert_call = mock_conn.fetchval.call_args
            assert insert_call is not None
            # The first argument is the SQL, arguments start from the second element
            # Actually, fetchval arguments are (query, *args)
            assert insert_call.args[1] == "agent2::BTCUSDT:spot"


@pytest.mark.asyncio
async def test_portfolio_manager_preserves_futures_market():
    """Futures positions should retain market metadata separately from symbol."""
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
                quantity=0.01,
                price=50000.0,
                market="futures",
            )

            assert position.market == "futures"
            insert_call = mock_conn.fetchval.call_args
            assert insert_call is not None
            assert insert_call.args[1] == "BTCUSDT"
            assert insert_call.args[2] == "futures"


@pytest.mark.asyncio
async def test_portfolio_manager_persists_short_futures_side():
    """Short futures positions should store side and close with BUY."""
    manager = PortfolioManager({})

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 9

    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    with patch("src.portfolio.manager.get_pool", return_value=mock_pool):
        async with manager:
            position = await manager.open_position(
                symbol="BTCUSDT",
                quantity=0.01,
                price=50000.0,
                market="futures",
                position_side="SHORT",
            )

            assert position.position_side == "SHORT"

            open_call = mock_conn.fetchval.call_args
            assert open_call is not None
            assert open_call.args[3] == "SHORT"

            await manager.close_position(
                symbol="BTCUSDT",
                price=49000.0,
                market="futures",
            )

            insert_trade_sql = mock_conn.execute.call_args_list[-1]
            assert insert_trade_sql.args[4] == "BUY"


@pytest.mark.asyncio
async def test_portfolio_summary_includes_last_trade_time():
    manager = PortfolioManager({})

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [
        3,
        1,
        2,
        7,
        datetime(2026, 3, 4, 15, 25, tzinfo=UTC),
        -101.03,
        2,
        1,
    ]

    with patch("src.portfolio.manager.get_pool", return_value=mock_pool):
        async with manager:
            summary = await manager.get_portfolio_summary()

    assert summary.total_positions == 3
    assert summary.open_positions == 1
    assert summary.closed_positions == 2
    assert summary.total_trades == 7
    assert summary.last_trade_time == datetime(2026, 3, 4, 15, 25, tzinfo=UTC)
    assert summary.total_realized_pnl == pytest.approx(-101.03)


@pytest.mark.asyncio
async def test_get_daily_stats_uses_full_utc_day_window():
    manager = PortfolioManager({})

    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = [
        {
            "symbol": "BTCUSDT",
            "market": "paper",
            "position_side": "LONG",
            "realized_pnl": 100.0,
            "entry_price": 1.0,
            "exit_price": 2.0,
            "exit_time": datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        },
        {
            "symbol": "ETHUSDT",
            "market": "paper",
            "position_side": "LONG",
            "realized_pnl": 23.45,
            "entry_price": 1.0,
            "exit_price": 2.0,
            "exit_time": datetime(2026, 3, 12, 13, 0, tzinfo=UTC),
        },
        {
            "symbol": "SOLUSDT",
            "market": "paper",
            "position_side": "LONG",
            "realized_pnl": -10.0,
            "entry_price": 1.0,
            "exit_price": 2.0,
            "exit_time": datetime(2026, 3, 12, 14, 0, tzinfo=UTC),
        },
    ]

    with (
        patch("src.portfolio.manager.get_pool", return_value=mock_pool),
        patch.object(manager, "_load_open_positions", new=AsyncMock()),
        patch.object(manager, "_ensure_schema", new=AsyncMock()),
    ):
        async with manager:
            total_pnl, trades_count, win_rate = await manager.get_daily_stats(date(2026, 3, 12))

    assert total_pnl == pytest.approx(113.45)
    assert trades_count == 3
    assert win_rate == pytest.approx(66.6666666667)

    fetch_call = mock_conn.fetch.call_args_list[0]

    expected_start = datetime(2026, 3, 12, 0, 0, tzinfo=UTC)
    expected_end = datetime(2026, 3, 13, 0, 0, tzinfo=UTC)

    assert fetch_call.args[1] == manager._agent_id
    assert fetch_call.args[2] == expected_start
    assert fetch_call.args[3] == expected_end
