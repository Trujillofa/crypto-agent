from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.portfolio.manager import PortfolioManager


@pytest.mark.asyncio
async def test_portfolio_manager_open_close():
    """PortfolioManager opens/closes positions with asyncpg mock."""
    manager = PortfolioManager({})

    mock_conn = AsyncMock()
    # _fetch_open_positions returns empty list (no existing positions)
    mock_conn.fetch = AsyncMock(return_value=[])
    # open_position uses fetchval to get the RETURNING id
    mock_conn.fetchval = AsyncMock(return_value=1)

    # transaction() must return an async context manager (not a coroutine)
    @asynccontextmanager
    async def _mock_transaction():
        yield

    mock_conn.transaction = _mock_transaction

    with patch("src.portfolio.manager.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
        async with manager:
            assert manager.has_position("BTCUSDT") is False

            position = await manager.open_position(
                symbol="BTCUSDT", quantity=2.0, price=100.0
            )
            assert position.entry_price == 100.0
            assert position.quantity == 2.0
            assert manager.has_position("BTCUSDT") is True

            closed_position, pnl = await manager.close_position(
                symbol="BTCUSDT", price=110.0
            )
            assert closed_position.is_closed is True
            assert closed_position.realized_pnl == pytest.approx(20.0)
            assert pnl == pytest.approx(20.0)
            assert manager.has_position("BTCUSDT") is False
