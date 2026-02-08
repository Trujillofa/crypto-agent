"""Tests for ingest/db.py."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv


@pytest.fixture
def metrics() -> IngestMetrics:
    """Create test metrics instance."""
    return IngestMetrics()


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


@pytest.fixture
def sample_ohlcv() -> Ohlcv:
    """Create sample OHLCV data."""
    return Ohlcv(
        symbol="BTCUSDT",
        timeframe="1m",
        open_time=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        close_time=datetime(2024, 1, 1, 0, 0, 59, tzinfo=timezone.utc),
        open_price=45000.0,
        high_price=46000.0,
        low_price=44000.0,
        close_price=45500.0,
        volume=1000.5,
    )


class TestTimescaleWriterInit:
    """Test suite for TimescaleWriter initialization."""

    def test_init_with_config(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test initialization with config."""
        writer = TimescaleWriter(db_config, metrics)
        assert writer._config == db_config
        assert writer._connected is False
        assert writer._conn is None

    def test_init_stores_metrics(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test metrics are stored."""
        writer = TimescaleWriter(db_config, metrics)
        assert writer._metrics is metrics


class TestConnectSQLiteFallback:
    """Test suite for SQLite fallback connection."""

    def test_connect_falls_back_to_sqlite(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test connection falls back to SQLite when PostgreSQL unavailable."""
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = TimescaleWriter(db_config, metrics)

            # Mock pg8000 to fail and sqlite3 to succeed
            with patch(
                "src.ingest.db.pg8000.connect", side_effect=Exception("PG unavailable")
            ):
                with patch("src.ingest.db.sqlite3.connect") as mock_sqlite:
                    mock_conn = MagicMock()
                    mock_sqlite.return_value = mock_conn

                    writer._connect()

            # Should have fallen back to SQLite
            assert writer._connected is True
            assert writer._use_sqlite is True
            mock_sqlite.assert_called_once()


class TestAsyncContextManager:
    """Test suite for async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_connects(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test __aenter__ establishes connection."""
        writer = TimescaleWriter(db_config, metrics)

        with patch.object(writer, "_connect") as mock_connect:
            with patch.object(writer, "_ensure_schema"):
                async with writer:
                    mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_aexit_closes_connection(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test __aexit__ closes connection."""
        writer = TimescaleWriter(db_config, metrics)
        mock_conn = MagicMock()

        with patch.object(writer, "_connect"):
            with patch.object(writer, "_ensure_schema"):
                writer._conn = mock_conn
                writer._connected = True
                async with writer:
                    pass

        assert writer._connected is False


class TestEnsureSchema:
    """Test suite for schema management."""

    def test_ensure_schema_not_connected_raises(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test _ensure_schema raises when not connected."""
        writer = TimescaleWriter(db_config, metrics)
        with pytest.raises(RuntimeError, match="not initialized"):
            writer._ensure_schema()

    def test_ensure_schema_no_connection_raises(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test _ensure_schema raises when no connection object."""
        writer = TimescaleWriter(db_config, metrics)
        writer._connected = True
        writer._conn = None
        with pytest.raises(RuntimeError, match="connection missing"):
            writer._ensure_schema()


class TestInsertRow:
    """Test suite for row insertion."""

    def test_insert_row_not_connected_raises(
        self,
        db_config: dict[str, object],
        metrics: IngestMetrics,
        sample_ohlcv: Ohlcv,
    ) -> None:
        """Test _insert_row raises when not connected."""
        writer = TimescaleWriter(db_config, metrics)
        with pytest.raises(RuntimeError, match="not initialized"):
            writer._insert_row(sample_ohlcv)

    def test_insert_row_no_connection_raises(
        self,
        db_config: dict[str, object],
        metrics: IngestMetrics,
        sample_ohlcv: Ohlcv,
    ) -> None:
        """Test _insert_row raises when no connection object."""
        writer = TimescaleWriter(db_config, metrics)
        writer._connected = True
        writer._conn = None
        with pytest.raises(RuntimeError, match="connection missing"):
            writer._insert_row(sample_ohlcv)

    def test_insert_row_db_error_propagates(
        self,
        db_config: dict[str, object],
        metrics: IngestMetrics,
        sample_ohlcv: Ohlcv,
    ) -> None:
        """Test that database errors are propagated during insert."""
        writer = TimescaleWriter(db_config, metrics)
        writer._connected = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Insert Error")
        mock_conn.cursor.return_value = mock_cursor
        writer._conn = mock_conn
        writer._use_sqlite = True

        with pytest.raises(Exception, match="DB Insert Error"):
            writer._insert_row(sample_ohlcv)


class TestWriteOhlcv:
    """Test suite for async write_ohlcv."""

    @pytest.mark.asyncio
    async def test_write_ohlcv_records_latency(
        self,
        db_config: dict[str, object],
        metrics: IngestMetrics,
        sample_ohlcv: Ohlcv,
    ) -> None:
        """Test write_ohlcv records latency metric."""
        writer = TimescaleWriter(db_config, metrics)
        writer._connected = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit = MagicMock()
        writer._conn = mock_conn
        writer._use_sqlite = True

        await writer.write_ohlcv(sample_ohlcv)

        # Latency should be recorded (value > 0 since some time passed)
        from src.ingest.metrics import MetricKey

        key = MetricKey.from_labels({})
        assert key in metrics.insert_latency_seconds.values


class TestSQLiteIntegration:
    """Integration tests with actual SQLite."""

    @pytest.mark.asyncio
    async def test_full_sqlite_write_cycle(
        self, metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test full write cycle with SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "data" / "ohlcv.sqlite"

            # Create config that will fail PG connection and fall back to SQLite
            config = {
                "host": "nonexistent",
                "port": 5432,
                "name": "testdb",
                "user": "testuser",
                "password": "testpass",
            }
            writer = TimescaleWriter(config, metrics)

            # Patch the SQLite path
            original_connect = writer._connect

            def patched_connect() -> None:
                import sqlite3

                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                writer._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
                writer._use_sqlite = True
                writer._connected = True

            writer._connect = patched_connect

            async with writer:
                await writer.write_ohlcv(sample_ohlcv)

            # Verify data was written
            import sqlite3

            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ohlcv")
            count = cursor.fetchone()[0]
            conn.close()

            assert count == 1

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing(
        self, metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test upsert replaces existing row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "data" / "ohlcv.sqlite"

            config = {
                "host": "nonexistent",
                "port": 5432,
                "name": "testdb",
                "user": "testuser",
                "password": "testpass",
            }
            writer = TimescaleWriter(config, metrics)

            def patched_connect() -> None:
                import sqlite3

                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
                writer._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
                writer._use_sqlite = True
                writer._connected = True

            writer._connect = patched_connect

            async with writer:
                await writer.write_ohlcv(sample_ohlcv)

                # Write same candle again with different close price
                updated_ohlcv = Ohlcv(
                    symbol="BTCUSDT",
                    timeframe="1m",
                    open_time=sample_ohlcv.open_time,
                    close_time=sample_ohlcv.close_time,
                    open_price=45000.0,
                    high_price=46000.0,
                    low_price=44000.0,
                    close_price=46000.0,  # Different close
                    volume=1000.5,
                )
                await writer.write_ohlcv(updated_ohlcv)

            # Verify only 1 row exists with updated close price
            import sqlite3

            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ohlcv")
            count = cursor.fetchone()[0]
            cursor.execute("SELECT close_price FROM ohlcv")
            close_price = cursor.fetchone()[0]
            conn.close()

            assert count == 1
            assert close_price == 46000.0
