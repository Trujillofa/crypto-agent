"""Tests for ingest/db.py."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics, MetricKey
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


@pytest.mark.asyncio
class TestTimescaleWriter:
    """Test suite for TimescaleWriter with asyncpg."""

    async def test_init_with_config(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test initialization."""
        writer = TimescaleWriter(db_config, metrics)
        assert writer._config == db_config
        assert writer._conn is None
        assert writer._connected is False

    async def test_connect_success(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test successful connection."""
        writer = TimescaleWriter(db_config, metrics)

        mock_conn = AsyncMock()
        with patch(
            "src.ingest.db.asyncpg.connect",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ):
            await writer._connect()

            assert writer._conn is mock_conn
            assert writer._connected is True

    async def test_connect_failure(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test connection failure raises exception."""
        writer = TimescaleWriter(db_config, metrics)

        with patch(
            "src.ingest.db.asyncpg.connect", side_effect=Exception("Connection failed")
        ):
            with pytest.raises(Exception, match="Connection failed"):
                await writer._connect()

    async def test_context_manager(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test async context manager connects and closes."""
        writer = TimescaleWriter(db_config, metrics)

        mock_conn = AsyncMock()
        with patch(
            "src.ingest.db.asyncpg.connect",
            new_callable=AsyncMock,
            return_value=mock_conn,
        ):
            with patch.object(writer, "_ensure_schema", new_callable=AsyncMock):
                async with writer as w:
                    assert w is writer
                    assert w._conn is mock_conn

            mock_conn.close.assert_called_once()
            assert writer._connected is False

    async def test_write_ohlcv(
        self, db_config: dict[str, object], metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test writing OHLCV data."""
        writer = TimescaleWriter(db_config, metrics)
        mock_conn = AsyncMock()
        writer._conn = mock_conn
        writer._connected = True

        await writer.write_ohlcv(sample_ohlcv)

        mock_conn.execute.assert_called_once()
        # Latency metric should be recorded
        key = MetricKey.from_labels({})
        assert key in metrics.insert_latency_seconds.values

    async def test_write_ohlcv_not_connected(
        self, db_config: dict[str, object], metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test writing when not connected raises RuntimeError."""
        writer = TimescaleWriter(db_config, metrics)

        with pytest.raises(RuntimeError, match="not initialized"):
            await writer.write_ohlcv(sample_ohlcv)

    async def test_write_ohlcv_no_conn_object(
        self, db_config: dict[str, object], metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test writing when conn is None raises RuntimeError."""
        writer = TimescaleWriter(db_config, metrics)
        writer._connected = True
        writer._conn = None

        with pytest.raises(RuntimeError, match="connection missing"):
            await writer.write_ohlcv(sample_ohlcv)

    async def test_write_ohlcv_db_error(
        self, db_config: dict[str, object], metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test db error propagates from write_ohlcv."""
        writer = TimescaleWriter(db_config, metrics)
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("DB Error")
        writer._conn = mock_conn
        writer._connected = True

        with pytest.raises(Exception, match="DB Error"):
            await writer.write_ohlcv(sample_ohlcv)

    async def test_count_rows(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test counting rows."""
        writer = TimescaleWriter(db_config, metrics)
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 42
        writer._conn = mock_conn

        count = await writer.count_rows("ohlcv")
        assert count == 42

    async def test_count_rows_invalid_table(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test count_rows rejects invalid table names."""
        writer = TimescaleWriter(db_config, metrics)
        mock_conn = AsyncMock()
        writer._conn = mock_conn

        with pytest.raises(ValueError, match="Invalid table name"):
            await writer.count_rows("malicious_table")

    async def test_count_rows_no_connection(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test count_rows returns 0 when no connection."""
        writer = TimescaleWriter(db_config, metrics)
        assert await writer.count_rows("ohlcv") == 0

    async def test_ensure_schema_not_connected(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test _ensure_schema raises when not connected."""
        writer = TimescaleWriter(db_config, metrics)

        with pytest.raises(RuntimeError, match="not initialized"):
            await writer._ensure_schema()

    async def test_ensure_schema_no_conn(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test _ensure_schema raises when conn is None."""
        writer = TimescaleWriter(db_config, metrics)
        writer._connected = True
        writer._conn = None

        with pytest.raises(RuntimeError, match="connection missing"):
            await writer._ensure_schema()
