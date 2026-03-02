"""Tests for src/features/reader.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.features.reader import IndicatorReader


@pytest.fixture
def db_config() -> dict[str, object]:
    """Create test database config."""
    return {
        "host": "localhost",
        "port": 5432,
        "name": "testdb",
        "user": "testuser",
        "password": "testpass",
    }


class TestIndicatorReaderInit:
    """Test suite for IndicatorReader initialization."""

    def test_init_with_config(self, db_config: dict[str, object]) -> None:
        """Test initialization with config."""
        reader = IndicatorReader(db_config)
        assert reader._config == db_config
        assert reader._connected is False
        assert reader._conn is None


class TestAsyncContextManager:
    """Test suite for async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_connects(self, db_config: dict[str, object]) -> None:
        """Test __aenter__ establishes connection."""
        reader = IndicatorReader(db_config)

        mock_conn = AsyncMock()
        with patch(
            "src.features.reader.asyncpg.connect",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ):
            async with reader:
                assert reader._connected is True
                assert reader._conn is mock_conn

    @pytest.mark.asyncio
    async def test_aexit_closes_connection(self, db_config: dict[str, object]) -> None:
        """Test __aexit__ closes connection."""
        reader = IndicatorReader(db_config)
        mock_conn = AsyncMock()

        with patch(
            "src.features.reader.asyncpg.connect",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ):
            async with reader:
                pass

        mock_conn.close.assert_called_once()
        assert reader._connected is False


class TestConnectAsyncpg:
    """Test suite for asyncpg connection."""

    @pytest.mark.asyncio
    async def test_connect_success(self, db_config: dict[str, object]) -> None:
        """Test successful asyncpg connection."""
        reader = IndicatorReader(db_config)
        mock_conn = AsyncMock()

        with patch(
            "src.features.reader.asyncpg.connect",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ):
            await reader._connect()

        assert reader._connected is True
        assert reader._conn is mock_conn

    @pytest.mark.asyncio
    async def test_connect_failure(self, db_config: dict[str, object]) -> None:
        """Test connection failure raises exception."""
        reader = IndicatorReader(db_config)

        with patch(
            "src.features.reader.asyncpg.connect",
            side_effect=Exception("PG unavailable"),
        ):
            with pytest.raises(Exception, match="PG unavailable"):
                await reader._connect()


class TestFetchLatest:
    """Test suite for fetch_latest method."""

    @pytest.mark.asyncio
    async def test_fetch_latest_returns_rows(self, db_config: dict[str, object]) -> None:
        """Test fetching latest rows returns oldest-first with dict format."""
        reader = IndicatorReader(db_config)
        mock_conn = AsyncMock()

        # Simulate two rows returned from DB (DESC order, will be reversed)
        mock_rows = [
            {
                "time": "2024-01-01T00:01:00Z",
                "ema_12": 45150.0,
                "ema_26": 45250.0,
                "close_price": 45100.0,
                "rsi_14": 35.0,
                "rsi_7": None,
                "macd": 110.0,
                "macd_signal": None,
                "macd_hist": None,
                "bb_upper_dist": None,
                "bb_lower_dist": None,
                "atr_14": None,
                "atr_pct": None,
                "ema_50": None,
                "ema_200": None,
                "sma_20": None,
                "sma_50": None,
                "sma_200": None,
                "vwap": None,
                "stoch_k": None,
                "stoch_d": None,
                "cci": None,
            },
            {
                "time": "2024-01-01T00:00:00Z",
                "ema_12": 45100.0,
                "ema_26": 45200.0,
                "close_price": 45000.0,
                "rsi_14": 30.0,
                "rsi_7": None,
                "macd": 100.0,
                "macd_signal": None,
                "macd_hist": None,
                "bb_upper_dist": None,
                "bb_lower_dist": None,
                "atr_14": None,
                "atr_pct": None,
                "ema_50": None,
                "ema_200": None,
                "sma_20": None,
                "sma_50": None,
                "sma_200": None,
                "vwap": None,
                "stoch_k": None,
                "stoch_d": None,
                "cci": None,
            },
        ]
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        reader._conn = mock_conn
        reader._connected = True

        rows = await reader.fetch_latest("BTCUSDT", "1m", limit=2)

        assert len(rows) == 2
        # Should be oldest-first (reversed from DESC)
        assert rows[0]["close_price"] == 45000.0
        assert rows[0]["ema_12"] == 45100.0
        assert rows[1]["close_price"] == 45100.0
        assert rows[1]["ema_12"] == 45150.0

    @pytest.mark.asyncio
    async def test_fetch_empty_table(self, db_config: dict[str, object]) -> None:
        """Test fetching from empty table returns empty list."""
        reader = IndicatorReader(db_config)
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        reader._conn = mock_conn
        reader._connected = True

        rows = await reader.fetch_latest("BTCUSDT", "1m", limit=2)
        assert rows == []

    @pytest.mark.asyncio
    async def test_fetch_not_connected_raises(self, db_config: dict[str, object]) -> None:
        """Test fetching when not connected raises RuntimeError."""
        reader = IndicatorReader(db_config)

        with pytest.raises(RuntimeError, match="not initialized"):
            await reader.fetch_latest("BTCUSDT", "1m", limit=2)
