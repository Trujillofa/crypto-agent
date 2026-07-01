from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import aiohttp

from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.utils.logger import get_logger

WriteCallback = Callable[[Ohlcv], Awaitable[None]]


class BinanceWebSocketIngestor:
    """Async Binance Spot data ingestor using WebSockets."""

    SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
    FUTURES_WS_URL = "wss://fstream.binance.com/ws"
    FUTURES_DEMO_WS_URL = "wss://fstream.binancefuture.com/ws"

    def __init__(
        self,
        symbols: Iterable[str],
        timeframe: str,
        metrics: IngestMetrics,
        base_url: str | None = None,
        stream_type: str = "kline",
        stale_timeout_seconds: float = 120.0,
        reconnect_base_delay_seconds: float = 1.0,
        reconnect_max_delay_seconds: float = 30.0,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._timeframe: str = timeframe
        self._metrics: IngestMetrics = metrics
        self._logger = get_logger(self.__class__.__name__)
        self._base_url: str = base_url or self.SPOT_WS_URL
        self._stream_type: str = stream_type
        self._stale_timeout_seconds: float = max(stale_timeout_seconds, 1.0)
        self._reconnect_base_delay_seconds: float = max(reconnect_base_delay_seconds, 0.5)
        self._reconnect_max_delay_seconds: float = max(
            reconnect_max_delay_seconds,
            self._reconnect_base_delay_seconds,
        )
        self._session: aiohttp.ClientSession | None = None
        self._running: bool = False
        self._mark_price_callback: Callable[[str, float], Awaitable[None]] | None = None

    async def __aenter__(self) -> BinanceWebSocketIngestor:
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

        # Construct combined stream URL based on stream type
        if self._stream_type == "kline":
            # Format: <symbol>@kline_<interval>/<symbol>@kline_<interval>...
            streams = [f"{s.lower()}@kline_{self._timeframe}" for s in self._symbols]
        elif self._stream_type == "mark_price":
            # Format: <symbol>@markPrice or !markPrice@arr for all
            if len(self._symbols) > 1:
                streams = ["!markPrice@arr"]  # All symbols at once
            else:
                streams = [f"{s.lower()}@markPrice" for s in self._symbols]
        else:
            raise ValueError(f"Unsupported stream type: {self._stream_type}")

        stream_path = "/".join(streams)
        url = f"{self._base_url}/{stream_path}"

        self._logger.info(f"Connecting to WebSocket stream: {url}")

        reconnect_attempt = 0
        while self._running:
            try:
                async with self._session.ws_connect(url, heartbeat=30) as ws:
                    self._logger.info("WebSocket connected")
                    self._metrics.websocket_last_message_age_seconds.set(
                        0.0, labels={"stream": self._stream_type}
                    )
                    reconnect_attempt = 0
                    last_message_at = time.monotonic()

                    while self._running:
                        try:
                            msg = await ws.receive(timeout=self._stale_timeout_seconds)
                        except TimeoutError:
                            idle_seconds = time.monotonic() - last_message_at
                            self._logger.warning(
                                "WebSocket stale for %.1fs (stream=%s), reconnecting",
                                idle_seconds,
                                self._stream_type,
                            )
                            self._metrics.errors_total.inc(labels={"error_type": "websocket_stale"})
                            self._metrics.websocket_last_message_age_seconds.set(
                                idle_seconds, labels={"stream": self._stream_type}
                            )
                            await ws.close()
                            break

                        last_message_at = time.monotonic()
                        self._metrics.websocket_last_message_age_seconds.set(
                            0.0, labels={"stream": self._stream_type}
                        )
                        if not self._running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_message(msg.data, on_candle)
                        elif msg.type == aiohttp.WSMsgType.PING:
                            await ws.pong(msg.data)
                        elif msg.type == aiohttp.WSMsgType.PONG:
                            continue
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
                reconnect_attempt += 1
                self._metrics.websocket_reconnects_total.inc(labels={"stream": self._stream_type})
                delay = self._compute_reconnect_delay(reconnect_attempt)
                self._logger.info(
                    "WebSocket reconnect attempt %d in %.2fs (stream=%s)",
                    reconnect_attempt,
                    delay,
                    self._stream_type,
                )
                await asyncio.sleep(delay)

    async def _handle_message(self, raw_data: str, on_candle: WriteCallback) -> None:
        """Process incoming WebSocket message."""
        try:
            data = json.loads(raw_data)
            received_at_ms = datetime.now(UTC).timestamp() * 1000.0

            # Handle all-markets array (for futures mark price)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("e") == "markPriceUpdate":
                        await self._handle_mark_price(item, received_at_ms)
                return

            # Handle single mark price update (for futures)
            if data.get("e") == "markPriceUpdate":
                await self._handle_mark_price(data, received_at_ms)
                return

            # Check for kline event
            if data.get("e") != "kline":
                return

            kline = data.get("k", {})
            symbol = data.get("s")
            if symbol:
                self._record_websocket_latency(
                    symbol=symbol,
                    stream="kline_ws",
                    event_time_ms=data.get("E"),
                    received_at_ms=received_at_ms,
                )

            # Only process closed candles to ensure final data
            # "x": true means the candle is closed
            is_closed = kline.get("x", False)

            if is_closed:
                candle = self._parse_kline(symbol, kline)
                await on_candle(candle)

                self._metrics.messages_total.inc(labels={"symbol": symbol, "stream": "kline_ws"})
                self._metrics.last_open_time.set(
                    candle.open_time_utc.timestamp(), labels={"symbol": symbol}
                )

        except json.JSONDecodeError:
            self._logger.error("Failed to decode WebSocket message")
        except Exception as exc:
            self._logger.error(f"Error handling message: {exc}")
            self._metrics.errors_total.inc(labels={"error_type": "message_processing"})

    async def _handle_mark_price(self, data: dict[str, Any], received_at_ms: float) -> None:
        """Handle mark price update for futures positions.

        Args:
            data: Mark price update event data
        """
        symbol = data.get("s")
        if symbol:
            self._record_websocket_latency(
                symbol=symbol,
                stream="mark_price",
                event_time_ms=data.get("E"),
                received_at_ms=received_at_ms,
            )
        mark_price = float(data.get("p", 0))
        funding_rate = float(data.get("r", 0))

        self._metrics.messages_total.inc(labels={"symbol": symbol, "stream": "mark_price"})

        # Callback for liquidation monitoring
        if self._mark_price_callback:
            await self._mark_price_callback(symbol, mark_price)

        self._logger.debug(
            "Mark price update: %s @ %.2f (funding: %.4f%%)",
            symbol,
            mark_price,
            funding_rate * 100,
        )

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
        return datetime.fromtimestamp(ms_since_epoch / 1000, tz=UTC)

    def _record_websocket_latency(
        self,
        symbol: str,
        stream: str,
        event_time_ms: float | int | None,
        received_at_ms: float,
    ) -> None:
        if not event_time_ms:
            return
        try:
            latency_ms = max(0.0, received_at_ms - float(event_time_ms))
        except (TypeError, ValueError):
            return
        self._metrics.websocket_latency_ms.set(
            latency_ms,
            labels={"symbol": symbol, "stream": stream},
        )

    def _compute_reconnect_delay(self, attempt: int) -> float:
        base_delay = min(
            self._reconnect_base_delay_seconds * (2 ** max(attempt - 1, 0)),
            self._reconnect_max_delay_seconds,
        )
        jitter = random.uniform(0.0, base_delay * 0.25)
        return min(base_delay + jitter, self._reconnect_max_delay_seconds)
