from __future__ import annotations

import sqlite3

import pytest

from src.portfolio.manager import PortfolioManager


@pytest.mark.asyncio
async def test_portfolio_manager_open_close_sqlite(tmp_path, monkeypatch):
    """PortfolioManager opens/closes positions with SQLite fallback."""
    import src.portfolio.manager as portfolio_manager_module

    def _raise_connect(*_args, **_kwargs):
        raise RuntimeError("Timescale unavailable")

    monkeypatch.setattr(portfolio_manager_module.pg8000, "connect", _raise_connect)

    sqlite_path = tmp_path / "portfolio.sqlite"
    original_connect = sqlite3.connect

    def _sqlite_connect(_path, *args, **kwargs):
        return original_connect(sqlite_path, *args, **kwargs)

    monkeypatch.setattr(portfolio_manager_module.sqlite3, "connect", _sqlite_connect)

    manager = PortfolioManager({})

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
