"""Tests for src/features/reader.py."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestAsyncContextManager:
    """Test suite for async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager(self, db_config: dict[str, object]) -> None:
        """Test async context manager works."""
        reader = IndicatorReader(db_config)
        async with reader as r:
            assert r is reader


class TestFetchLatest:
    """Test suite for fetch_latest method."""

    @pytest.mark.asyncio
    async def test_fetch_latest_returns_rows(self, db_config: dict[str, object]) -> None:
        """Test fetching latest rows returns oldest-first with dict format."""
        reader = IndicatorReader(db_config)
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

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
                "ema_slope_50": None,
                "volatility_percentile": None,
                "atr_percentile": None,
                "volume_regime": None,
                "price_vs_weekly": None,
                "price_vs_monthly": None,
                "rsi_slope": None,
                "trend_consistency": None,
            },
            {
                "time": "2024-01-01T00:00:00Z",
                "ema_12": 45100.0,
                "ema_slope_50": None,
                "volatility_percentile": None,
                "atr_percentile": None,
                "volume_regime": None,
                "price_vs_weekly": None,
                "price_vs_monthly": None,
                "rsi_slope": None,
                "trend_consistency": None,
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
        mock_conn.fetch.return_value = mock_rows

        with patch("src.features.reader.get_pool", return_value=mock_pool):
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
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_conn.fetch.return_value = []

        with patch("src.features.reader.get_pool", return_value=mock_pool):
            rows = await reader.fetch_latest("BTCUSDT", "1m", limit=2)
        assert rows == []


class TestFetchLatestMultiTimeframe:
    """Test suite for fetch_latest_multi_timeframe."""

    @pytest.mark.asyncio
    async def test_fetch_latest_multi_timeframe_returns_joined_rows(
        self,
        db_config: dict[str, object],
    ) -> None:
        """Latest joined rows include suffixed regime indicators."""
        reader = IndicatorReader(db_config)

        async def mock_fetch_rows(symbol: str, timeframe: str, limit: int):
            assert symbol == "BTCUSDT"
            assert timeframe == "1h"
            assert limit == 2
            return [
                {
                    "time": datetime.fromisoformat("2024-01-01T09:00:00"),
                    "close_price": 101.0,
                    "ema_12": 100.0,
                    "ema_26": 99.0,
                    "ema_50": 98.0,
                    "ema_200": 95.0,
                    "vwap": 100.5,
                },
                {
                    "time": datetime.fromisoformat("2024-01-01T10:00:00"),
                    "close_price": 102.0,
                    "ema_12": 101.0,
                    "ema_26": 100.0,
                    "ema_50": 99.0,
                    "ema_200": 95.0,
                    "vwap": 101.0,
                },
            ]

        async def mock_fetch_range_rows(
            symbol: str, timeframe: str, start_time: str, end_time: str
        ):
            assert symbol == "BTCUSDT"
            assert timeframe == "4h"
            assert end_time == "2024-01-01T10:00:00"
            return [
                {
                    "time": datetime.fromisoformat("2024-01-01T04:00:00"),
                    "ema_slope_50": 0.01,
                    "trend_consistency": 75.0,
                    "volatility_percentile": 65.0,
                }
            ]

        reader._fetch_rows = mock_fetch_rows
        reader._fetch_range_rows = mock_fetch_range_rows

        rows = await reader.fetch_latest_multi_timeframe("BTCUSDT", "1h", "4h", limit=2)

        assert len(rows) == 2
        assert rows[-1]["close_price"] == 102.0
        assert rows[-1]["ema_slope_50_4h"] == 0.01
