from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from src.db.pool import get_pool
from src.features.metrics import IndicatorMetrics
from src.features.technical import compute_indicators
from src.features.writer import IndicatorWriter, StoredIndicator
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
        self._running = False
        self._db_lock = asyncio.Lock()

    async def run(self) -> None:
        """Main computation loop."""
        self._running = True
        self._metrics.start_computation_loop()
        self._logger.info("Starting indicator computation loop...")

        try:
            while self._running:
                await self._compute_all_symbols()
                await asyncio.sleep(self._compute_interval)
        except asyncio.CancelledError:
            self._logger.info("Indicator computation loop cancelled")
        finally:
            self._metrics.stop_computation_loop()
            self._logger.info("Indicator computation loop stopped")

    async def _compute_all_symbols(self) -> None:
        """Compute indicators for all configured symbols."""
        start_time = time.perf_counter()

        for symbol in self._symbols:
            try:
                await self._compute_symbol(symbol)
            except Exception as exc:  # noqa: BLE001
                self._logger.error(
                    "Failed to compute indicators for %s: %s", symbol, exc, exc_info=True
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
        async with self._db_lock:
            computation_start = time.perf_counter()

            # Read OHLCV data (need at least 200 periods for long-term indicators)
            ohlcv_data = await self._read_ohlcv(symbol, limit=200)

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
                    latest_time = latest_time_value
                else:
                    latest_time = datetime.now(UTC)
            else:
                latest_time = datetime.now(UTC)

            # Update Prometheus metrics
            self._metrics.rsi.labels(symbol=symbol, period="14").set(indicators.rsi_14)
            self._metrics.rsi.labels(symbol=symbol, period="7").set(indicators.rsi_7)
            self._metrics.macd.labels(symbol=symbol, component="macd").set(indicators.macd)
            self._metrics.macd.labels(symbol=symbol, component="signal").set(indicators.macd_signal)
            self._metrics.macd.labels(symbol=symbol, component="hist").set(indicators.macd_hist)
            self._metrics.atr.labels(symbol=symbol, period="14").set(indicators.atr_14)

            # Store indicators
            stored = StoredIndicator(
                time=latest_time,
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
                ema_8=indicators.ema_8,
                ema_10=indicators.ema_10,
                ema_12=indicators.ema_12,
                ema_14=indicators.ema_14,
                ema_21=indicators.ema_21,
                ema_24=indicators.ema_24,
                ema_26=indicators.ema_26,
                ema_30=indicators.ema_30,
                ema_50=indicators.ema_50,
                ema_200=indicators.ema_200,
                sma_20=indicators.sma_20,
                sma_40=indicators.sma_40,
                sma_50=indicators.sma_50,
                sma_60=indicators.sma_60,
                sma_200=indicators.sma_200,
                vwap=indicators.vwap,
                stoch_k=indicators.stoch_k,
                stoch_d=indicators.stoch_d,
                cci=indicators.cci,
                # Regime Features (NEW)
                ema_slope_50=indicators.ema_slope_50,
                volatility_percentile=indicators.volatility_percentile,
                atr_percentile=indicators.atr_percentile,
                volume_regime=indicators.volume_regime,
                price_vs_weekly=indicators.price_vs_weekly,
                price_vs_monthly=indicators.price_vs_monthly,
                rsi_slope=indicators.rsi_slope,
                trend_consistency=indicators.trend_consistency,
            )

            write_start = time.perf_counter()
            await self._writer.write_indicators(stored)
            write_elapsed = time.perf_counter() - write_start
            self._metrics.write_latency.labels(symbol=symbol).observe(write_elapsed)
            self._metrics.writes_total.labels(symbol=symbol, status="success").inc()

            # Update metrics
            elapsed = time.perf_counter() - computation_start
            self._metrics.computation_latency.labels(symbol=symbol).observe(elapsed)
            self._metrics.computations_total.labels(symbol=symbol, status="success").inc()
            self._metrics.last_computation_time.labels(symbol=symbol).set(time.time())

    async def _read_ohlcv(self, symbol: str, limit: int) -> dict[str, list]:
        """Read OHLCV data from TimescaleDB."""
        query = """
            SELECT time, open_price, high_price, low_price, close_price, volume
            FROM ohlcv
            WHERE symbol = $1 AND timeframe = $2
            ORDER BY time DESC
            LIMIT $3
        """

        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, self._timeframe, limit)

        if not rows:
            return {}

        # Reverse to get oldest to newest
        rows = list(reversed(rows))

        return {
            "time": [row["time"] for row in rows],
            "open": [row["open_price"] for row in rows],
            "high": [row["high_price"] for row in rows],
            "low": [row["low_price"] for row in rows],
            "close": [row["close_price"] for row in rows],
            "volume": [row["volume"] for row in rows],
        }

    def stop(self) -> None:
        """Stop the computation loop."""
        self._running = False
