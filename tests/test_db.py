"""Tests for ingest/db.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
        open_time=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        close_time=datetime(2024, 1, 1, 0, 0, 59, tzinfo=UTC),
        open_price=45000.0,
        high_price=46000.0,
        low_price=44000.0,
        close_price=45500.0,
        volume=1000.5,
    )


@pytest.mark.asyncio
class TestTimescaleWriter:
    """Test suite for TimescaleWriter with pooled asyncpg."""

    async def test_init_with_config(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test initialization."""
        writer = TimescaleWriter(db_config, metrics)
        assert writer._config == db_config

    async def test_write_ohlcv(
        self, db_config: dict[str, object], metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test writing OHLCV data."""
        writer = TimescaleWriter(db_config, metrics)
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("src.ingest.db.get_pool", return_value=mock_pool):
            await writer.write_ohlcv(sample_ohlcv)

        mock_conn.execute.assert_called_once()
        # Latency metric should be recorded
        key = MetricKey.from_labels({})
        assert key in metrics.insert_latency_seconds.values

    async def test_write_ohlcv_db_error(
        self, db_config: dict[str, object], metrics: IngestMetrics, sample_ohlcv: Ohlcv
    ) -> None:
        """Test db error propagates from write_ohlcv."""
        writer = TimescaleWriter(db_config, metrics)
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = Exception("DB Error")
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("src.ingest.db.get_pool", return_value=mock_pool):
            with pytest.raises(Exception, match="DB Error"):
                await writer.write_ohlcv(sample_ohlcv)

    async def test_count_rows(self, db_config: dict[str, object], metrics: IngestMetrics) -> None:
        """Test counting rows."""
        writer = TimescaleWriter(db_config, metrics)
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 42
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("src.ingest.db.get_pool", return_value=mock_pool):
            count = await writer.count_rows("ohlcv")
        assert count == 42

    async def test_count_rows_invalid_table(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test count_rows rejects invalid table names."""
        writer = TimescaleWriter(db_config, metrics)

        with pytest.raises(ValueError, match="Invalid table name"):
            await writer.count_rows("malicious_table")

    async def test_ensure_schema(
        self, db_config: dict[str, object], metrics: IngestMetrics
    ) -> None:
        """Test _ensure_schema creates tables and hypertables."""
        writer = TimescaleWriter(db_config, metrics)
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("src.ingest.db.get_pool", return_value=mock_pool):
            await writer._ensure_schema()

        # Should call execute for table and hypertable
        assert mock_conn.execute.call_count >= 2
