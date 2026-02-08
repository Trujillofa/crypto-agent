"""Tests for src/features/reader.py."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestConnectSQLiteFallback:
    """Test suite for SQLite fallback connection."""

    def test_connect_falls_back_to_sqlite(self, db_config: dict[str, object]) -> None:
        """Test connection falls back to SQLite when PostgreSQL unavailable."""
        reader = IndicatorReader(db_config)

        # Mock pg8000 to fail and sqlite3 to succeed
        with patch(
            "src.features.reader.pg8000.connect",
            side_effect=Exception("PG unavailable"),
        ):
            with patch("src.features.reader.sqlite3.connect") as mock_sqlite:
                mock_conn = MagicMock()
                mock_sqlite.return_value = mock_conn

                reader._connect()

        # Should have fallen back to SQLite
        assert reader._connected is True
        assert reader._use_sqlite is True
        mock_sqlite.assert_called_once()


class TestAsyncContextManager:
    """Test suite for async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_connects(self, db_config: dict[str, object]) -> None:
        """Test __aenter__ establishes connection."""
        reader = IndicatorReader(db_config)

        with patch.object(reader, "_connect") as mock_connect:
            async with reader:
                mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_aexit_closes_connection(self, db_config: dict[str, object]) -> None:
        """Test __aexit__ closes connection."""
        reader = IndicatorReader(db_config)
        mock_conn = MagicMock()

        with patch.object(reader, "_connect"):
            reader._conn = mock_conn
            reader._connected = True
            async with reader:
                pass

        assert reader._connected is False


class TestFetchLatest:
    """Test suite for fetch_latest method."""

    @pytest.mark.asyncio
    async def test_fetch_latest_two_rows(self, db_config: dict[str, object]) -> None:
        """Test fetching latest two rows returns oldest-first with dict format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "test_indicators.sqlite"
            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()

            # Create tables
            cursor.execute(
                """
                CREATE TABLE ohlcv (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    PRIMARY KEY (time, symbol, timeframe)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE indicators (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ema_12 REAL,
                    ema_26 REAL,
                    rsi_14 REAL,
                    rsi_7 REAL,
                    macd REAL,
                    macd_signal REAL,
                    macd_hist REAL,
                    bb_upper_dist REAL,
                    bb_lower_dist REAL,
                    atr_14 REAL,
                    atr_pct REAL,
                    ema_50 REAL,
                    ema_200 REAL,
                    sma_20 REAL,
                    sma_50 REAL,
                    sma_200 REAL,
                    vwap REAL,
                    stoch_k REAL,
                    stoch_d REAL,
                    cci REAL,
                    PRIMARY KEY (time, symbol, timeframe)
                )
                """
            )

            # Insert sample data (two rows)
            cursor.execute(
                "INSERT INTO ohlcv (time, symbol, timeframe, close_price) VALUES (?, ?, ?, ?)",
                ("2024-01-01T00:00:00Z", "BTCUSDT", "1m", 45000.0),
            )
            cursor.execute(
                """
                INSERT INTO indicators (
                    time, symbol, timeframe, ema_12, ema_26, rsi_14, macd
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2024-01-01T00:00:00Z",
                    "BTCUSDT",
                    "1m",
                    45100.0,
                    45200.0,
                    30.0,
                    100.0,
                ),
            )
            cursor.execute(
                "INSERT INTO ohlcv (time, symbol, timeframe, close_price) VALUES (?, ?, ?, ?)",
                ("2024-01-01T00:01:00Z", "BTCUSDT", "1m", 45100.0),
            )
            cursor.execute(
                """
                INSERT INTO indicators (
                    time, symbol, timeframe, ema_12, ema_26, rsi_14, macd
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "2024-01-01T00:01:00Z",
                    "BTCUSDT",
                    "1m",
                    45150.0,
                    45250.0,
                    35.0,
                    110.0,
                ),
            )
            conn.commit()
            conn.close()

            # Create reader with patched connection
            reader = IndicatorReader(db_config)

            def patched_connect() -> None:
                reader._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
                reader._use_sqlite = True
                reader._connected = True

            reader._connect = patched_connect

            async with reader:
                rows = await reader.fetch_latest("BTCUSDT", "1m", limit=2)

            assert len(rows) == 2
            # Should be oldest-first (after DESC + reverse)
            assert rows[0]["close_price"] == 45000.0
            assert rows[0]["ema_12"] == 45100.0
            assert rows[0]["ema_26"] == 45200.0
            assert rows[0]["rsi_14"] == 30.0
            assert rows[0]["macd"] == 100.0

            assert rows[1]["close_price"] == 45100.0
            assert rows[1]["ema_12"] == 45150.0
            assert rows[1]["ema_26"] == 45250.0
            assert rows[1]["rsi_14"] == 35.0
            assert rows[1]["macd"] == 110.0

    @pytest.mark.asyncio
    async def test_fetch_empty_table(self, db_config: dict[str, object]) -> None:
        """Test fetching from empty table returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "test_empty.sqlite"
            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()

            # Create empty tables
            cursor.execute(
                """
                CREATE TABLE ohlcv (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    PRIMARY KEY (time, symbol, timeframe)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE indicators (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ema_12 REAL,
                    ema_26 REAL,
                    rsi_14 REAL,
                    rsi_7 REAL,
                    macd REAL,
                    macd_signal REAL,
                    macd_hist REAL,
                    bb_upper_dist REAL,
                    bb_lower_dist REAL,
                    atr_14 REAL,
                    atr_pct REAL,
                    ema_50 REAL,
                    ema_200 REAL,
                    sma_20 REAL,
                    sma_50 REAL,
                    sma_200 REAL,
                    vwap REAL,
                    stoch_k REAL,
                    stoch_d REAL,
                    cci REAL,
                    PRIMARY KEY (time, symbol, timeframe)
                )
                """
            )
            conn.commit()
            conn.close()

            reader = IndicatorReader(db_config)

            def patched_connect() -> None:
                reader._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
                reader._use_sqlite = True
                reader._connected = True

            reader._connect = patched_connect

            async with reader:
                rows = await reader.fetch_latest("BTCUSDT", "1m", limit=2)

            assert rows == []

    @pytest.mark.asyncio
    async def test_fetch_single_row(self, db_config: dict[str, object]) -> None:
        """Test fetching single row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "test_single.sqlite"
            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()

            # Create tables
            cursor.execute(
                """
                CREATE TABLE ohlcv (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    PRIMARY KEY (time, symbol, timeframe)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE indicators (
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ema_12 REAL,
                    ema_26 REAL,
                    rsi_14 REAL,
                    rsi_7 REAL,
                    macd REAL,
                    macd_signal REAL,
                    macd_hist REAL,
                    bb_upper_dist REAL,
                    bb_lower_dist REAL,
                    atr_14 REAL,
                    atr_pct REAL,
                    ema_50 REAL,
                    ema_200 REAL,
                    sma_20 REAL,
                    sma_50 REAL,
                    sma_200 REAL,
                    vwap REAL,
                    stoch_k REAL,
                    stoch_d REAL,
                    cci REAL,
                    PRIMARY KEY (time, symbol, timeframe)
                )
                """
            )

            # Insert single row
            cursor.execute(
                "INSERT INTO ohlcv (time, symbol, timeframe, close_price) VALUES (?, ?, ?, ?)",
                ("2024-01-01T00:00:00Z", "BTCUSDT", "1m", 45000.0),
            )
            cursor.execute(
                """
                INSERT INTO indicators (
                    time, symbol, timeframe, ema_12, ema_26, rsi_14
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("2024-01-01T00:00:00Z", "BTCUSDT", "1m", 45100.0, 45200.0, 50.0),
            )
            conn.commit()
            conn.close()

            reader = IndicatorReader(db_config)

            def patched_connect() -> None:
                reader._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
                reader._use_sqlite = True
                reader._connected = True

            reader._connect = patched_connect

            async with reader:
                rows = await reader.fetch_latest("BTCUSDT", "1m", limit=2)

            assert len(rows) == 1
            assert rows[0]["close_price"] == 45000.0
            assert rows[0]["ema_12"] == 45100.0
            assert rows[0]["ema_26"] == 45200.0

    @pytest.mark.asyncio
    async def test_db_uses_asyncio_to_thread(
        self, db_config: dict[str, object]
    ) -> None:
        """Test that database operations use asyncio.to_thread."""
        reader = IndicatorReader(db_config)

        with patch("src.features.reader.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = []
            reader._connected = True

            await reader.fetch_latest("BTCUSDT", "1m", limit=2)

            # Verify asyncio.to_thread was called with _fetch_rows
            mock_to_thread.assert_called_once()
            assert mock_to_thread.call_args[0][0] == reader._fetch_rows
