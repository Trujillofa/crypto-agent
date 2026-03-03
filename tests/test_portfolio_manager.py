from __future__ import annotations

from contextlib import asynccontextmanager
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
            assert closed_position.realized_pnl == pytest.approx(19.58)
            assert pnl == pytest.approx(19.58)
            assert manager.has_position("BTCUSDT") is False


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
