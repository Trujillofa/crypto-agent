"""Tests for ingest/models.py."""

from __future__ import annotations

from datetime import UTC, datetime

from src.ingest.models import Ohlcv


class TestOhlcv:
    """Test suite for Ohlcv model."""

    def test_ohlcv_creation(self) -> None:
        """Test basic OHLCV creation."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=now,
            close_time=now,
            open_price=50000.0,
            high_price=51000.0,
            low_price=49000.0,
            close_price=50500.0,
            volume=100.0,
        )

        assert candle.symbol == "BTCUSDT"
        assert candle.timeframe == "1m"
        assert candle.open_price == 50000.0
        assert candle.high_price == 51000.0
        assert candle.low_price == 49000.0
        assert candle.close_price == 50500.0
        assert candle.volume == 100.0

    def test_ohlcv_price_relationships(self) -> None:
        """Test that high >= open/close/low and low <= open/close/high."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=now,
            close_time=now,
            open_price=50000.0,
            high_price=51000.0,
            low_price=49000.0,
            close_price=50500.0,
            volume=100.0,
        )

        assert candle.high_price >= candle.open_price
        assert candle.high_price >= candle.close_price
        assert candle.high_price >= candle.low_price
        assert candle.low_price <= candle.open_price
        assert candle.low_price <= candle.close_price
        assert candle.low_price <= candle.high_price

    def test_ohlcv_zero_volume(self) -> None:
        """Test OHLCV with zero volume."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="ETHUSDT",
            timeframe="1h",
            open_time=now,
            close_time=now,
            open_price=3000.0,
            high_price=3000.0,
            low_price=3000.0,
            close_price=3000.0,
            volume=0.0,
        )

        assert candle.volume == 0.0

    def test_ohlcv_different_timeframes(self) -> None:
        """Test OHLCV with various timeframes."""
        now = datetime.now(UTC)
        timeframes = ["1m", "5m", "15m", "1h", "4h", "1d"]

        for tf in timeframes:
            candle = Ohlcv(
                symbol="BTCUSDT",
                timeframe=tf,
                open_time=now,
                close_time=now,
                open_price=50000.0,
                high_price=51000.0,
                low_price=49000.0,
                close_price=50500.0,
                volume=100.0,
            )
            assert candle.timeframe == tf

    def test_ohlcv_different_symbols(self) -> None:
        """Test OHLCV with various trading pairs."""
        now = datetime.now(UTC)
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

        for symbol in symbols:
            candle = Ohlcv(
                symbol=symbol,
                timeframe="1m",
                open_time=now,
                close_time=now,
                open_price=50000.0,
                high_price=51000.0,
                low_price=49000.0,
                close_price=50500.0,
                volume=100.0,
            )
            assert candle.symbol == symbol

    def test_ohlcv_negative_values(self) -> None:
        """Test OHLCV with negative price movement (loss)."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=now,
            close_time=now,
            open_price=51000.0,
            high_price=51500.0,
            low_price=49000.0,
            close_price=50000.0,
            volume=200.0,
        )

        assert candle.close_price < candle.open_price
        assert candle.change_percent < 0

    def test_ohlcv_positive_values(self) -> None:
        """Test OHLCV with positive price movement (gain)."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=now,
            close_time=now,
            open_price=50000.0,
            high_price=52000.0,
            low_price=49500.0,
            close_price=51000.0,
            volume=200.0,
        )

        assert candle.close_price > candle.open_price
        assert candle.change_percent > 0

    def test_ohlcv_body_size(self) -> None:
        """Test OHLCV body size calculation."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=now,
            close_time=now,
            open_price=50000.0,
            high_price=52000.0,
            low_price=49000.0,
            close_price=51000.0,
            volume=200.0,
        )

        expected_body = abs(candle.close_price - candle.open_price)
        assert candle.body_size == expected_body

    def test_ohlcv_wick_size(self) -> None:
        """Test OHLCV wick calculations."""
        now = datetime.now(UTC)
        candle = Ohlcv(
            symbol="BTCUSDT",
            timeframe="1m",
            open_time=now,
            close_time=now,
            open_price=50000.0,
            high_price=52000.0,
            low_price=49000.0,
            close_price=51000.0,
            volume=200.0,
        )

        upper_wick = candle.high_price - max(candle.open_price, candle.close_price)
        lower_wick = min(candle.open_price, candle.close_price) - candle.low_price

        assert candle.upper_wick == upper_wick
        assert candle.lower_wick == lower_wick
