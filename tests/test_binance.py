"""Tests for ingest/binance.py."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingest.binance import BinanceIngestor
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv


@pytest.fixture
def metrics() -> IngestMetrics:
    """Create test metrics instance."""
    return IngestMetrics()


@pytest.fixture
def ingestor(metrics: IngestMetrics) -> BinanceIngestor:
    """Create test ingestor instance."""
    return BinanceIngestor(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframe="1m",
        metrics=metrics,
    )


class TestBinanceIngestorInit:
    """Test suite for BinanceIngestor initialization."""

    def test_init_with_symbols(self, metrics: IngestMetrics) -> None:
        """Test initialization with symbols."""
        ingestor = BinanceIngestor(
            symbols=["BTCUSDT", "ETHUSDT"],
            timeframe="1m",
            metrics=metrics,
        )
        assert ingestor._symbols == ["BTCUSDT", "ETHUSDT"]
        assert ingestor._timeframe == "1m"

    def test_init_with_empty_symbols(self, metrics: IngestMetrics) -> None:
        """Test initialization with empty symbols."""
        ingestor = BinanceIngestor(
            symbols=[],
            timeframe="1m",
            metrics=metrics,
        )
        assert ingestor._symbols == []

    def test_base_url(self, ingestor: BinanceIngestor) -> None:
        """Test base URL is Binance Spot."""
        assert ingestor._base_url == "https://api.binance.com"


class TestPollIntervalSeconds:
    """Test suite for poll interval calculation."""

    def test_1m_interval(self, metrics: IngestMetrics) -> None:
        """Test 1m timeframe returns 60 seconds."""
        ingestor = BinanceIngestor(["BTCUSDT"], "1m", metrics)
        assert ingestor._poll_interval_seconds() == 60

    def test_5m_interval(self, metrics: IngestMetrics) -> None:
        """Test 5m timeframe returns 300 seconds."""
        ingestor = BinanceIngestor(["BTCUSDT"], "5m", metrics)
        assert ingestor._poll_interval_seconds() == 300

    def test_15m_interval(self, metrics: IngestMetrics) -> None:
        """Test 15m timeframe returns 900 seconds."""
        ingestor = BinanceIngestor(["BTCUSDT"], "15m", metrics)
        assert ingestor._poll_interval_seconds() == 900

    def test_1h_interval(self, metrics: IngestMetrics) -> None:
        """Test 1h timeframe returns 3600 seconds."""
        ingestor = BinanceIngestor(["BTCUSDT"], "1h", metrics)
        assert ingestor._poll_interval_seconds() == 3600

    def test_4h_interval(self, metrics: IngestMetrics) -> None:
        """Test 4h timeframe returns 14400 seconds."""
        ingestor = BinanceIngestor(["BTCUSDT"], "4h", metrics)
        assert ingestor._poll_interval_seconds() == 14400

    def test_unknown_interval_defaults_to_60(self, metrics: IngestMetrics) -> None:
        """Test unknown timeframe defaults to 60 seconds."""
        ingestor = BinanceIngestor(["BTCUSDT"], "unknown", metrics)
        assert ingestor._poll_interval_seconds() == 60


class TestParseKline:
    """Test suite for kline parsing."""

    def test_parse_kline_valid(self, ingestor: BinanceIngestor) -> None:
        """Test parsing valid kline data."""
        raw = [
            1704067200000,  # open time (ms)
            "45000.00",  # open
            "46000.00",  # high
            "44000.00",  # low
            "45500.00",  # close
            "1000.5",  # volume
            1704067259999,  # close time (ms)
            "45000000.00",  # quote asset volume
            100,  # number of trades
            "500.25",  # taker buy base asset volume
            "22500000.00",  # taker buy quote asset volume
            "0",  # unused
        ]
        ohlcv = ingestor._parse_kline("BTCUSDT", raw)

        assert isinstance(ohlcv, Ohlcv)
        assert ohlcv.symbol == "BTCUSDT"
        assert ohlcv.timeframe == "1m"
        assert ohlcv.open_price == 45000.00
        assert ohlcv.high_price == 46000.00
        assert ohlcv.low_price == 44000.00
        assert ohlcv.close_price == 45500.00
        assert ohlcv.volume == 1000.5

    def test_parse_kline_timestamps(self, ingestor: BinanceIngestor) -> None:
        """Test timestamp parsing."""
        raw = [
            1704067200000,  # open time
            "45000.00",
            "46000.00",
            "44000.00",
            "45500.00",
            "1000.5",
            1704067259999,  # close time
            "0",
            0,
            "0",
            "0",
            "0",
        ]
        ohlcv = ingestor._parse_kline("BTCUSDT", raw)

        assert ohlcv.open_time.tzinfo == timezone.utc
        assert ohlcv.close_time.tzinfo == timezone.utc

    def test_parse_kline_invalid_data_raises(self, ingestor: BinanceIngestor) -> None:
        """Test parsing kline with invalid data types raises error."""
        raw = ["not-a-timestamp", "not-a-float", "high", "low", "close", "vol"]
        with pytest.raises((ValueError, TypeError, IndexError)):
            ingestor._parse_kline("BTCUSDT", raw)

    def test_parse_kline_missing_fields_raises(self, ingestor: BinanceIngestor) -> None:
        """Test parsing kline with missing fields raises error."""
        raw = [1704067200000, "45000.00"]  # Missing high, low, etc.
        with pytest.raises(IndexError):
            ingestor._parse_kline("BTCUSDT", raw)


class TestToDatetime:
    """Test suite for datetime conversion."""

    def test_to_datetime_valid(self) -> None:
        """Test valid millisecond epoch conversion."""
        ms = 1704067200000  # 2024-01-01 00:00:00 UTC
        dt = BinanceIngestor._to_datetime(ms)

        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.tzinfo == timezone.utc

    def test_to_datetime_float(self) -> None:
        """Test float millisecond epoch conversion."""
        ms = 1704067200000.5
        dt = BinanceIngestor._to_datetime(ms)
        assert isinstance(dt, datetime)


class TestToFloat:
    """Test suite for float conversion."""

    def test_to_float_string(self) -> None:
        """Test string to float conversion."""
        assert BinanceIngestor._to_float("45000.50") == 45000.50

    def test_to_float_int(self) -> None:
        """Test int to float conversion."""
        assert BinanceIngestor._to_float(45000) == 45000.0

    def test_to_float_float(self) -> None:
        """Test float passthrough."""
        assert BinanceIngestor._to_float(45000.50) == 45000.50


class TestAsyncContextManager:
    """Test suite for async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_creates_session(self, ingestor: BinanceIngestor) -> None:
        """Test __aenter__ creates aiohttp session."""
        assert ingestor._session is None
        async with ingestor:
            assert ingestor._session is not None

    @pytest.mark.asyncio
    async def test_aexit_closes_session(self, ingestor: BinanceIngestor) -> None:
        """Test __aexit__ closes aiohttp session."""
        async with ingestor:
            session = ingestor._session
            assert session is not None
        assert ingestor._session is None


class TestFetchKlines:
    """Test suite for kline fetching."""

    @pytest.mark.asyncio
    async def test_fetch_klines_no_session_raises(
        self, ingestor: BinanceIngestor
    ) -> None:
        """Test fetch without session raises error."""
        with pytest.raises(RuntimeError, match="Session not initialized"):
            await ingestor._fetch_klines("BTCUSDT")

    @pytest.mark.asyncio
    async def test_fetch_klines_success(self, ingestor: BinanceIngestor) -> None:
        """Test successful kline fetch with mocked response."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(
            return_value=[
                [
                    1704067200000,
                    "45000.00",
                    "46000.00",
                    "44000.00",
                    "45500.00",
                    "1000.5",
                    1704067259999,
                    "0",
                    0,
                    "0",
                    "0",
                    "0",
                ]
            ]
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        async with ingestor:
            with patch.object(ingestor._session, "get", return_value=mock_response):
                result = await ingestor._fetch_klines("BTCUSDT")

        assert len(result) == 1
        assert result[0][1] == "45000.00"


class TestPollLatest:
    """Test suite for poll latest functionality."""

    @pytest.mark.asyncio
    async def test_poll_latest_no_session_raises(
        self, ingestor: BinanceIngestor
    ) -> None:
        """Test poll without session raises error."""
        with pytest.raises(RuntimeError, match="Session not initialized"):
            await ingestor._poll_latest(AsyncMock())

    @pytest.mark.asyncio
    async def test_poll_latest_calls_callback(self, ingestor: BinanceIngestor) -> None:
        """Test poll latest calls callback for each symbol."""
        callback = AsyncMock()
        mock_klines = [
            [
                1704067200000,
                "45000.00",
                "46000.00",
                "44000.00",
                "45500.00",
                "1000.5",
                1704067259999,
                "0",
                0,
                "0",
                "0",
                "0",
            ]
        ]

        async with ingestor:
            with patch.object(
                ingestor, "_fetch_klines", new=AsyncMock(return_value=mock_klines)
            ):
                await ingestor._poll_latest(callback)

        # Should be called twice (BTCUSDT and ETHUSDT)
        assert callback.call_count == 2


class TestRunLoop:
    """Test suite for main run loop."""

    @pytest.mark.asyncio
    async def test_run_empty_symbols_returns(self, metrics: IngestMetrics) -> None:
        """Test run returns immediately with no symbols."""
        ingestor = BinanceIngestor(
            symbols=[],
            timeframe="1m",
            metrics=metrics,
        )
        await ingestor.run(AsyncMock())  # Should return immediately
