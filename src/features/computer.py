from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import datetime, timezone

import pg8000

from src.features.technical import compute_indicators
from src.features.writer import IndicatorWriter, StoredIndicator
from src.features.metrics import IndicatorMetrics
from src.utils.logger import get_logger


class IndicatorComputer:
    """Periodically computes and stores technical indicators from OHLCV data."""

    def __init__(
        self,
        config: Mapping[str, object],
        symbols: list[str],
        timeframe: str,
        writer: IndicatorWriter,
        metrics: IndicatorMetrics,
        compute_interval: int = 60,
    ) -> None:
        self._config = config
        self._symbols = symbols
        self._timeframe = timeframe
        self._writer = writer
        self._metrics = metrics
        self._compute_interval = compute_interval
        self._logger = get_logger(self.__class__.__name__)
        self._conn: pg8000.Connection | None = None
        self._running = False

    async def run(self) -> None:
        """Main computation loop."""
        self._running = True
        self._metrics.start_computation_loop()
        self._logger.info("Starting indicator computation loop...")

        # Initialize connection
        await self._connect()

        try:
            while self._running:
                await self._compute_all_symbols()
                await asyncio.sleep(self._compute_interval)
        except asyncio.CancelledError:
            self._logger.info("Indicator computation loop cancelled")
        finally:
            self._metrics.stop_computation_loop()
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
            self._logger.info("Indicator computation loop stopped")

    async def _connect(self) -> None:
        """Connect to TimescaleDB for reading OHLCV data."""
        host = str(self._config.get("host", "localhost"))
        port = int(self._config.get("port", 5432))
        database = str(self._config.get("name", "marketdata"))
        user = str(self._config.get("user", "trading"))
        password = str(self._config.get("password", ""))

        try:
            self._conn = await asyncio.to_thread(
                pg8000.connect,
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
            )
            self._logger.info("IndicatorComputer: Connected to TimescaleDB")
        except Exception as exc:  # noqa: BLE001
            self._logger.error("Failed to connect to TimescaleDB: %s", exc)
            raise

    async def _compute_all_symbols(self) -> None:
        """Compute indicators for all configured symbols."""
        start_time = time.perf_counter()

        for symbol in self._symbols:
            try:
                await self._compute_symbol(symbol)
            except Exception as exc:  # noqa: BLE001
                self._logger.error(
                    "Failed to compute indicators for %s: %s", symbol, exc
                )
                self._metrics.errors_total.labels(
                    symbol=symbol, error_type=type(exc).__name__
                ).inc()

        elapsed = time.perf_counter() - start_time
        self._logger.info(
            "Computed indicators for %d symbols in %.2f seconds",
            len(self._symbols),
            elapsed,
        )

    async def _compute_symbol(self, symbol: str) -> None:
        """Compute and store indicators for a single symbol."""
        computation_start = time.perf_counter()

        # Read OHLCV data (need at least 200 periods for long-term indicators)
        ohlcv_data = await asyncio.to_thread(
            self._read_ohlcv,
            symbol,
            limit=200,
        )

        if not ohlcv_data:
            self._logger.warning("No OHLCV data found for %s", symbol)
            return

        # Compute indicators
        try:
            indicators = compute_indicators(ohlcv_data)
        except ValueError as exc:
            self._logger.warning(
                "Not enough data to compute indicators for %s: %s", symbol, exc
            )
            self._metrics.errors_total.labels(
                symbol=symbol, error_type="insufficient_data"
            ).inc()
            return

        # Get the latest timestamp from OHLCV data
        time_values = list(ohlcv_data.get("time", []))
        if time_values:
            latest_time_value = time_values[-1]
            if isinstance(latest_time_value, datetime):
                latest_time_str = latest_time_value.isoformat()
            else:
                latest_time_str = str(latest_time_value)
        else:
            latest_time_str = datetime.now(timezone.utc).isoformat()

        # Update Prometheus metrics
        self._metrics.rsi.labels(symbol=symbol, period="14").set(indicators.rsi_14)
        self._metrics.rsi.labels(symbol=symbol, period="7").set(indicators.rsi_7)
        self._metrics.macd.labels(symbol=symbol, component="macd").set(indicators.macd)
        self._metrics.macd.labels(symbol=symbol, component="signal").set(
            indicators.macd_signal
        )
        self._metrics.macd.labels(symbol=symbol, component="hist").set(
            indicators.macd_hist
        )
        self._metrics.atr.labels(symbol=symbol, period="14").set(indicators.atr_14)

        # Store indicators
        stored = StoredIndicator(
            time=latest_time_str,
            symbol=symbol,
            timeframe=self._timeframe,
            rsi_14=indicators.rsi_14,
            rsi_7=indicators.rsi_7,
            macd=indicators.macd,
            macd_signal=indicators.macd_signal,
            macd_hist=indicators.macd_hist,
            bb_upper_dist=indicators.bb_upper_dist,
            bb_lower_dist=indicators.bb_lower_dist,
            atr_14=indicators.atr_14,
            atr_pct=indicators.atr_pct,
            ema_12=indicators.ema_12,
            ema_26=indicators.ema_26,
            ema_50=indicators.ema_50,
            ema_200=indicators.ema_200,
            sma_20=indicators.sma_20,
            sma_50=indicators.sma_50,
            sma_200=indicators.sma_200,
            vwap=indicators.vwap,
            stoch_k=indicators.stoch_k,
            stoch_d=indicators.stoch_d,
            cci=indicators.cci,
        )

        await self._writer.write_indicators(stored)

        # Update metrics
        elapsed = time.perf_counter() - computation_start
        self._metrics.computation_latency.labels(symbol=symbol).observe(elapsed)
        self._metrics.computations_total.labels(symbol=symbol, status="success").inc()
        self._metrics.last_computation_time.labels(symbol=symbol).set(time.time())

    def _read_ohlcv(self, symbol: str, limit: int) -> dict[str, list]:
        """Read OHLCV data from TimescaleDB."""
        if self._conn is None:
            raise RuntimeError("Database connection not initialized")

        cursor = self._conn.cursor()

        query = """
            SELECT time, open_price, high_price, low_price, close_price, volume
            FROM ohlcv
            WHERE symbol = %s AND timeframe = %s
            ORDER BY time DESC
            LIMIT %s
        """

        cursor.execute(query, (symbol, self._timeframe, limit))
        rows = cursor.fetchall()

        if not rows:
            return {}

        # Reverse to get oldest to newest
        rows = list(reversed(rows))

        return {
            "time": [row[0] for row in rows],
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
            "volume": [row[5] for row in rows],
        }

    def stop(self) -> None:
        """Stop the computation loop."""
        self._running = False
