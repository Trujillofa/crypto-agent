from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

import aiohttp

from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.utils.logger import get_logger
from src.utils.rate_limiter import RateLimiter

WriteCallback = Callable[[Ohlcv], Awaitable[None]]


class BinanceIngestor:
    """Async Binance Spot data ingestor using aiohttp."""

    def __init__(
        self,
        symbols: Iterable[str],
        timeframe: str,
        metrics: IngestMetrics,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._timeframe: str = timeframe
        self._metrics: IngestMetrics = metrics
        self._logger = get_logger(self.__class__.__name__)
        self._base_url: str = "https://api.binance.com"
        self._session: aiohttp.ClientSession | None = None
        self._rate_limiter = rate_limiter or RateLimiter()

    async def __aenter__(self) -> BinanceIngestor:
        """Initialize aiohttp session with connection pooling."""
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(
            limit=100,  # Max concurrent connections
            limit_per_host=10,  # Max connections per host
            ttl_dns_cache=300,  # DNS cache TTL
            use_dns_cache=True,
        )
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"Accept": "application/json"},
        )
        self._logger.info("aiohttp session initialized with connection pooling")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None
            self._logger.info("aiohttp session closed")

    async def run(self, on_candle: WriteCallback) -> None:
        """Main ingest loop."""
        if not self._symbols:
            self._logger.warning("No trading pairs configured; exiting ingest loop")
            return

        # Initialize session if not using context manager
        if self._session is None:
            await self.__aenter__()

        await self._backfill(on_candle)

        while True:
            try:
                await self._poll_latest(on_candle)
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("Ingest loop error: %s", exc)
                self._metrics.errors_total.inc(labels={"error_type": "ingest_loop"})
                await asyncio.sleep(5)
            await asyncio.sleep(self._poll_interval_seconds())

    async def _poll_latest(self, on_candle: WriteCallback) -> None:
        """Fetch latest klines for all symbols concurrently."""
        if not self._session:
            raise RuntimeError("Session not initialized")

        tasks = [self._fetch_klines(symbol) for symbol in self._symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for symbol, result in zip(self._symbols, results, strict=False):
            if isinstance(result, Exception):
                self._logger.error(f"Failed to fetch {symbol}: {result}")
                self._metrics.errors_total.inc(labels={"error_type": "fetch_failed"})
                continue

            if not result:
                continue

            payload = list(result)
            candle = self._parse_kline(symbol, payload[-1])
            await on_candle(candle)
            self._metrics.messages_total.inc(labels={"symbol": symbol, "stream": "klines"})
            self._metrics.last_open_time.set(
                candle.open_time_utc.timestamp(), labels={"symbol": symbol}
            )

    async def _backfill(self, on_candle: WriteCallback) -> None:
        """Backfill recent candles to seed indicator computation."""
        if not self._session:
            raise RuntimeError("Session not initialized")

        backfill_limit = 200
        self._logger.info("Starting backfill (%d candles per symbol)", backfill_limit)

        for symbol in self._symbols:
            try:
                klines = await self._fetch_klines(symbol, limit=backfill_limit)
            except Exception as exc:  # noqa: BLE001
                self._logger.error("Backfill failed for %s: %s", symbol, exc)
                self._metrics.errors_total.inc(labels={"error_type": "backfill_failed"})
                continue

            if not klines:
                self._logger.warning("Backfill returned no data for %s", symbol)
                continue

            for entry in klines:
                candle = self._parse_kline(symbol, entry)
                await on_candle(candle)
                self._metrics.messages_total.inc(labels={"symbol": symbol, "stream": "klines"})
                self._metrics.last_open_time.set(
                    candle.open_time_utc.timestamp(), labels={"symbol": symbol}
                )

            self._logger.info("Backfilled %d candles for %s", len(klines), symbol)

    async def _fetch_klines(self, symbol: str, limit: int = 100) -> list[list[str | int | float]]:
        """Fetch klines from Binance Spot API with rate limiting."""
        if not self._session:
            raise RuntimeError("Session not initialized")

        params = {"symbol": symbol, "interval": self._timeframe, "limit": limit}
        url = f"{self._base_url}/api/v3/klines"

        # Acquire rate limit token (weight based on limit)
        await self._rate_limiter.acquire(weight=self._weight_for_limit(limit))

        try:
            async with self._session.get(url, params=params) as response:
                # Update rate limiter from response headers
                headers = dict(response.headers.items())
                self._rate_limiter.update_from_response(headers)
                used_weight = headers.get("X-MBX-USED-WEIGHT-1M")
                if used_weight is not None:
                    try:
                        used_weight_value = float(used_weight)
                        self._metrics.api_used_weight_1m.set(used_weight_value)
                        self._metrics.api_rate_limit_remaining.set(
                            max(0.0, 2400.0 - used_weight_value)
                        )
                    except ValueError:
                        self._logger.debug("Invalid X-MBX-USED-WEIGHT-1M header: %s", used_weight)

                if response.status == 429:  # Rate limited
                    self._rate_limiter.record_error(429)
                    if await self._rate_limiter.wait_for_retry():
                        return await self._fetch_klines(symbol)  # Retry
                    raise RuntimeError("Rate limit exceeded, max retries reached")

                if response.status == 418:  # IP banned
                    self._rate_limiter.record_error(418)
                    raise RuntimeError("IP banned by Binance API")

                response.raise_for_status()
                payload = await response.json()

                if isinstance(payload, list):
                    return payload
                raise ValueError(f"Unexpected Binance response: {payload}")

        except aiohttp.ClientError as exc:
            self._rate_limiter.record_error(500)
            raise RuntimeError(f"Binance request failed: {exc}") from exc

    def _parse_kline(self, symbol: str, raw: Sequence[str | int | float]) -> Ohlcv:
        """Parse a kline response into Ohlcv model."""
        open_time = self._to_datetime(self._to_float(raw[0]))
        close_time = self._to_datetime(self._to_float(raw[6]))
        return Ohlcv(
            symbol=symbol,
            timeframe=self._timeframe,
            open_time=open_time,
            close_time=close_time,
            open_price=self._to_float(raw[1]),
            high_price=self._to_float(raw[2]),
            low_price=self._to_float(raw[3]),
            close_price=self._to_float(raw[4]),
            volume=self._to_float(raw[5]),
        )

    @staticmethod
    def _to_datetime(ms_since_epoch: int | float) -> datetime:
        """Convert milliseconds since epoch to datetime."""
        return datetime.fromtimestamp(float(ms_since_epoch) / 1000, tz=UTC)

    @staticmethod
    def _to_float(value: str | int | float) -> float:
        """Convert value to float."""
        return float(value)

    def _poll_interval_seconds(self) -> int:
        """Get polling interval based on timeframe."""
        match self._timeframe:
            case "1m":
                return 60
            case "5m":
                return 300
            case "15m":
                return 900
            case "1h":
                return 3600
            case "4h":
                return 14400
            case _:
                return 60

    @staticmethod
    def _weight_for_limit(limit: int) -> int:
        """Estimate Binance request weight based on kline limit."""
        if limit <= 100:
            return 1
        if limit <= 500:
            return 2
        return 5
