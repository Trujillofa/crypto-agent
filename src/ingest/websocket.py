from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import aiohttp

from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.utils.logger import get_logger

WriteCallback = Callable[[Ohlcv], Awaitable[None]]


class BinanceWebSocketIngestor:
    """Async Binance Spot data ingestor using WebSockets."""

    def __init__(
        self,
        symbols: Iterable[str],
        timeframe: str,
        metrics: IngestMetrics,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._timeframe: str = timeframe
        self._metrics: IngestMetrics = metrics
        self._logger = get_logger(self.__class__.__name__)
        self._base_url: str = "wss://stream.binance.com:9443/ws"
        self._session: aiohttp.ClientSession | None = None
        self._running: bool = False

    async def __aenter__(self) -> "BinanceWebSocketIngestor":
        """Initialize aiohttp session."""
        self._session = aiohttp.ClientSession()
        self._logger.info("aiohttp session initialized for WebSocket")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close aiohttp session."""
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
            self._logger.info("aiohttp session closed")

    async def run(self, on_candle: WriteCallback) -> None:
        """Main ingest loop connecting to WebSocket streams."""
        if not self._symbols:
            self._logger.warning("No trading pairs configured; exiting ingest loop")
            return

        if self._session is None:
            await self.__aenter__()

        self._running = True

        # Construct combined stream URL
        # Format: <symbol>@kline_<interval>/<symbol>@kline_<interval>...
        streams = [f"{s.lower()}@kline_{self._timeframe}" for s in self._symbols]
        stream_path = "/".join(streams)
        url = f"{self._base_url}/{stream_path}"

        self._logger.info(f"Connecting to WebSocket stream: {url}")

        while self._running:
            try:
                async with self._session.ws_connect(url) as ws:
                    self._logger.info("WebSocket connected")

                    async for msg in ws:
                        if not self._running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(msg.data, on_candle)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self._logger.error(
                                f"WebSocket connection closed with error: {ws.exception()}"
                            )
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            self._logger.warning("WebSocket connection closed")
                            break

            except Exception as exc:
                self._logger.error(f"WebSocket connection failed: {exc}")
                self._metrics.errors_total.inc(labels={"error_type": "websocket_error"})
                if self._running:
                    await asyncio.sleep(5)  # Reconnect delay

    async def _handle_message(self, raw_data: str, on_candle: WriteCallback) -> None:
        """Process incoming WebSocket message."""
        try:
            data = json.loads(raw_data)

            # Check for kline event
            if data.get("e") != "kline":
                return

            kline = data.get("k", {})
            symbol = data.get("s")

            # Only process closed candles to ensure final data
            # "x": true means the candle is closed
            is_closed = kline.get("x", False)

            if is_closed:
                candle = self._parse_kline(symbol, kline)
                await on_candle(candle)

                self._metrics.messages_total.inc(
                    labels={"symbol": symbol, "stream": "kline_ws"}
                )
                self._metrics.last_open_time.set(
                    candle.open_time_utc.timestamp(), labels={"symbol": symbol}
                )

        except json.JSONDecodeError:
            self._logger.error("Failed to decode WebSocket message")
        except Exception as exc:
            self._logger.error(f"Error handling message: {exc}")
            self._metrics.errors_total.inc(labels={"error_type": "message_processing"})

    def _parse_kline(self, symbol: str, k: dict[str, Any]) -> Ohlcv:
        """Parse WebSocket kline data into Ohlcv model."""
        # WebSocket data uses specific keys (t, T, o, c, h, l, v, etc.)
        open_time = self._to_datetime(k["t"])
        close_time = self._to_datetime(k["T"])

        return Ohlcv(
            symbol=symbol,
            timeframe=self._timeframe,
            open_time=open_time,
            close_time=close_time,
            open_price=float(k["o"]),
            high_price=float(k["h"]),
            low_price=float(k["l"]),
            close_price=float(k["c"]),
            volume=float(k["v"]),
        )

    @staticmethod
    def _to_datetime(ms_since_epoch: int) -> datetime:
        """Convert milliseconds since epoch to datetime."""
        return datetime.fromtimestamp(ms_since_epoch / 1000, tz=timezone.utc)
