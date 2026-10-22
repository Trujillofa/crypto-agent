from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime

from src.db.pool import get_pool
from src.features.metrics import IndicatorMetrics
from src.features.technical import INDICATOR_WINDOW_BARS, compute_indicators
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

            # Read OHLCV data (need at least 200 periods for long-term indicators).
            # Read one extra bar so that dropping a still-forming newest candle
            # still leaves a full canonical window of closed candles.
            ohlcv_data = await self._read_ohlcv(symbol, limit=INDICATOR_WINDOW_BARS + 1)

            if not ohlcv_data:
                self._logger.warning("No OHLCV data found for %s", symbol)
                return

            # The newest bar may still be forming. Publishing an indicator row
            # for it would freeze that row at a partial value forever: the
            # live path only ever rewrites the newest bar, and by the time the
            # candle closes a newer bar exists, so the closed bar's row is
            # never revisited. That is how the 4h rows ended up 2-3 hours
            # stale relative to the final candle.
            #
            # So: compute and publish the newest CLOSED bar instead, and
            # leave the forming bar to the next cycle. The row we publish is
            # then final and stays correct.
            closed_index = self._newest_closed_index(ohlcv_data)
            if closed_index is None:
                self._logger.info(
                    "No closed candle for %s yet (newest bar is still forming)", symbol
                )
                return

            # Trim to the newest closed bar, then keep exactly the canonical
            # trailing `INDICATOR_WINDOW_BARS` closed candles, so the window
            # matches the backfill definition bar for bar.
            closed_data: dict[str, list[float]] = {
                key: values[: closed_index + 1]
                for key, values in ohlcv_data.items()
                if key not in ("close_time", "time")
            }
            closed_times = list(ohlcv_data["time"])[: closed_index + 1]
            if len(closed_times) < INDICATOR_WINDOW_BARS:
                self._logger.info(
                    "Not enough closed candles for %s: have %d, need %d",
                    symbol,
                    len(closed_times),
                    INDICATOR_WINDOW_BARS,
                )
                return
            for key, values in closed_data.items():
                closed_data[key] = values[-INDICATOR_WINDOW_BARS:]
            closed_times = closed_times[-INDICATOR_WINDOW_BARS:]

            # Compute indicators
            try:
                indicators = compute_indicators(closed_data)
            except ValueError as exc:
                self._logger.warning(
                    "Not enough data to compute indicators for %s: %s", symbol, exc
                )
                self._metrics.errors_total.labels(
                    symbol=symbol, error_type="insufficient_data"
                ).inc()
                return

            # The published bar is the newest CLOSED candle.
            latest_time_value = closed_times[-1] if closed_times else None
            if isinstance(latest_time_value, datetime):
                latest_time = latest_time_value
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

    @staticmethod
    def _newest_closed_index(ohlcv_data: Mapping[str, list]) -> int | None:
        """Index of the newest candle whose close time has already passed.

        Returns ``None`` when every candle is still forming.

        A candle is closed once ``close_time <= now``. The REST poller writes
        Binance's in-progress kline, so the newest row in ``ohlcv`` is
        normally still forming and must not be published as an indicator.
        """
        close_times = ohlcv_data.get("close_time")
        times = ohlcv_data.get("time")
        if not close_times or not times:
            return None

        now = datetime.now(UTC)
        for index in range(len(close_times) - 1, -1, -1):
            close_time = close_times[index]
            if close_time is None:
                # No close time recorded: treat the bar as closed, since it
                # came from a source that does not track forming state.
                return index
            if not isinstance(close_time, datetime):
                continue
            if close_time.tzinfo is None:
                close_time = close_time.replace(tzinfo=UTC)
            if close_time <= now:
                return index
        return None

    async def _read_ohlcv(self, symbol: str, limit: int) -> dict[str, list]:
        """Read OHLCV data from TimescaleDB.

        The newest row may still be a *forming* candle: the REST poller
        writes Binance's in-progress kline and revises it on close. This
        method therefore also returns ``close_time`` for each row so the
        caller can tell a closed bar from a forming one.
        """
        query = """
            SELECT time, close_time, open_price, high_price, low_price, close_price, volume
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
            "close_time": [row["close_time"] for row in rows],
            "open": [row["open_price"] for row in rows],
            "high": [row["high_price"] for row in rows],
            "low": [row["low_price"] for row in rows],
            "close": [row["close_price"] for row in rows],
            "volume": [row["volume"] for row in rows],
        }

    def stop(self) -> None:
        """Stop the computation loop."""
        self._running = False
