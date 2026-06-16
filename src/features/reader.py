from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta

from src.db.pool import get_pool
from src.utils.logger import get_logger


class IndicatorReader:
    """Read latest indicators from database for strategy evaluation.

    Uses a shared connection pool for efficient database access.
    """

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._db_lock = asyncio.Lock()

    async def __aenter__(self) -> IndicatorReader:
        # Pool is managed globally
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        pass

    async def fetch_latest(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 2,
    ) -> list[dict[str, float]]:
        """Fetch latest N indicator rows for symbol+timeframe, oldest-first."""
        async with self._db_lock:
            return await self._fetch_rows(symbol, timeframe, limit)

    async def fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start_time: str,
        end_time: str,
    ) -> list[dict[str, float]]:
        """Fetch all indicator rows for symbol+timeframe within a time range."""
        async with self._db_lock:
            return await self._fetch_range_rows(symbol, timeframe, start_time, end_time)

    async def fetch_latest_multi_timeframe(
        self,
        symbol: str,
        entry_timeframe: str,
        regime_timeframe: str,
        limit: int = 2,
    ) -> list[dict[str, float]]:
        """Fetch the latest joined MTF indicator rows, oldest-first.

        This mirrors the backtest MTF path for runtime strategy evaluation.
        Regime indicators are joined onto entry bars using the same strict
        no-lookahead semantics as ``fetch_multi_timeframe``.
        """
        async with self._db_lock:
            entry_rows = await self._fetch_rows(symbol, entry_timeframe, limit)
            if not entry_rows:
                return []

            latest_entry_time = entry_rows[-1]["time"]
            regime_start = entry_rows[0]["time"] - timedelta(hours=24)
            regime_rows = await self._fetch_range_rows(
                symbol,
                regime_timeframe,
                regime_start.isoformat(),
                latest_entry_time.isoformat(),
            )

        return self._join_timeframes(entry_rows, regime_rows, regime_timeframe)

    async def _fetch_range_rows(
        self, symbol: str, timeframe: str, start_time: str, end_time: str
    ) -> list[dict[str, float]]:
        """Fetch rows from database for a specific time range."""
        query = """
            SELECT
                i.time,
                i.ema_12,
                i.ema_26,
                o.close_price,
                i.rsi_14,
                i.rsi_7,
                i.macd,
                i.macd_signal,
                i.macd_hist,
                i.bb_upper_dist,
                i.bb_lower_dist,
                i.atr_14,
                i.atr_pct,
                i.ema_50,
                i.ema_200,
                i.sma_20,
                i.sma_40,
                i.sma_50,
                i.sma_60,
                i.sma_200,
                i.vwap,
                i.stoch_k,
                i.stoch_d,
                i.cci,
                -- Regime Features (NEW)
                i.ema_slope_50,
                i.volatility_percentile,
                i.atr_percentile,
                i.volume_regime,
                i.price_vs_weekly,
                i.price_vs_monthly,
                i.rsi_slope,
                i.trend_consistency,
                fr.funding_rate,
                pbm.basis_bps,
                pbm.premium_index,
                (pbm.basis_bps - pbm2.basis_bps) AS cross_venue_basis_spread_bps,
                (pbm.premium_index - pbm2.premium_index) AS cross_venue_premium_spread,
                o.high_price,
                o.low_price
            FROM indicators i
            INNER JOIN ohlcv o
                ON i.time = o.time
                AND i.symbol = o.symbol
                AND i.timeframe = o.timeframe
            LEFT JOIN perp_basis_metrics pbm
                ON pbm.time = i.time
                AND pbm.symbol = i.symbol
                AND pbm.timeframe = i.timeframe
                AND pbm.exchange = 'binance_usdm'
            LEFT JOIN perp_basis_metrics pbm2
                ON pbm2.time = i.time
                AND pbm2.symbol = i.symbol
                AND pbm2.timeframe = i.timeframe
                AND pbm2.exchange = 'bybit'
            LEFT JOIN LATERAL (
                SELECT funding_rate
                FROM funding_rates
                WHERE symbol = i.symbol
                  AND funding_time <= i.time
                ORDER BY funding_time DESC
                LIMIT 1
            ) fr ON TRUE
            WHERE i.symbol = $1 AND i.timeframe = $2
            AND i.time >= $3 AND i.time <= $4
            ORDER BY i.time ASC
        """

        def _parse_dt(val: str | datetime) -> datetime:
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                query, symbol, timeframe, _parse_dt(start_time), _parse_dt(end_time)
            )

        if not rows:
            return []

        results = []
        for row in rows:
            high_price = row["high_price"] if "high_price" in row else None
            low_price = row["low_price"] if "low_price" in row else None
            results.append(
                {
                    "time": row["time"],
                    "ema_12": (float(row["ema_12"]) if row["ema_12"] is not None else 0.0),
                    "ema_26": (float(row["ema_26"]) if row["ema_26"] is not None else 0.0),
                    "close_price": float(row["close_price"]),
                    "rsi_14": (float(row["rsi_14"]) if row["rsi_14"] is not None else None),
                    "rsi_7": float(row["rsi_7"]) if row["rsi_7"] is not None else None,
                    "macd": float(row["macd"]) if row["macd"] is not None else None,
                    "macd_signal": (
                        float(row["macd_signal"]) if row["macd_signal"] is not None else None
                    ),
                    "macd_hist": (
                        float(row["macd_hist"]) if row["macd_hist"] is not None else None
                    ),
                    "bb_upper_dist": (
                        float(row["bb_upper_dist"]) if row["bb_upper_dist"] is not None else None
                    ),
                    "bb_lower_dist": (
                        float(row["bb_lower_dist"]) if row["bb_lower_dist"] is not None else None
                    ),
                    "atr_14": (float(row["atr_14"]) if row["atr_14"] is not None else None),
                    "atr_pct": (float(row["atr_pct"]) if row["atr_pct"] is not None else None),
                    "ema_50": (float(row["ema_50"]) if row["ema_50"] is not None else None),
                    "ema_200": (float(row["ema_200"]) if row["ema_200"] is not None else None),
                    "sma_20": (float(row["sma_20"]) if row["sma_20"] is not None else None),
                    "sma_40": (float(row["sma_40"]) if row["sma_40"] is not None else None),
                    "sma_50": (float(row["sma_50"]) if row["sma_50"] is not None else None),
                    "sma_60": (float(row["sma_60"]) if row["sma_60"] is not None else None),
                    "sma_200": (float(row["sma_200"]) if row["sma_200"] is not None else None),
                    "vwap": float(row["vwap"]) if row["vwap"] is not None else None,
                    "stoch_k": (float(row["stoch_k"]) if row["stoch_k"] is not None else None),
                    "stoch_d": (float(row["stoch_d"]) if row["stoch_d"] is not None else None),
                    "cci": float(row["cci"]) if row["cci"] is not None else None,
                    # Regime Features (NEW)
                    "ema_slope_50": (
                        float(row["ema_slope_50"]) if row["ema_slope_50"] is not None else None
                    ),
                    "volatility_percentile": (
                        float(row["volatility_percentile"])
                        if row["volatility_percentile"] is not None
                        else None
                    ),
                    "atr_percentile": (
                        float(row["atr_percentile"]) if row["atr_percentile"] is not None else None
                    ),
                    "volume_regime": (
                        float(row["volume_regime"]) if row["volume_regime"] is not None else None
                    ),
                    "price_vs_weekly": (
                        float(row["price_vs_weekly"])
                        if row["price_vs_weekly"] is not None
                        else None
                    ),
                    "price_vs_monthly": (
                        float(row["price_vs_monthly"])
                        if row["price_vs_monthly"] is not None
                        else None
                    ),
                    "rsi_slope": float(row["rsi_slope"]) if row["rsi_slope"] is not None else None,
                    "trend_consistency": (
                        float(row["trend_consistency"])
                        if row["trend_consistency"] is not None
                        else None
                    ),
                    "funding_rate": (
                        float(row["funding_rate"]) if row["funding_rate"] is not None else None
                    ),
                    "basis_bps": (
                        float(row.get("basis_bps")) if row.get("basis_bps") is not None else None
                    ),
                    "premium_index": (
                        float(row.get("premium_index"))
                        if row.get("premium_index") is not None
                        else None
                    ),
                    "cross_venue_basis_spread_bps": (
                        float(row.get("cross_venue_basis_spread_bps"))
                        if row.get("cross_venue_basis_spread_bps") is not None
                        else None
                    ),
                    "cross_venue_premium_spread": (
                        float(row.get("cross_venue_premium_spread"))
                        if row.get("cross_venue_premium_spread") is not None
                        else None
                    ),
                    "high_price": (
                        float(high_price) if high_price is not None else float(row["close_price"])
                    ),
                    "low_price": (
                        float(low_price) if low_price is not None else float(row["close_price"])
                    ),
                }
            )

        return results

    async def _fetch_rows(self, symbol: str, timeframe: str, limit: int) -> list[dict[str, float]]:
        """Fetch rows from database."""
        query = """
            SELECT
                i.time,
                i.ema_12,
                i.ema_26,
                o.close_price,
                i.rsi_14,
                i.rsi_7,
                i.macd,
                i.macd_signal,
                i.macd_hist,
                i.bb_upper_dist,
                i.bb_lower_dist,
                i.atr_14,
                i.atr_pct,
                i.ema_50,
                i.ema_200,
                i.sma_20,
                i.sma_40,
                i.sma_50,
                i.sma_60,
                i.sma_200,
                i.vwap,
                i.stoch_k,
                i.stoch_d,
                i.cci,
                -- Regime Features (NEW)
                i.ema_slope_50,
                i.volatility_percentile,
                i.atr_percentile,
                i.volume_regime,
                i.price_vs_weekly,
                i.price_vs_monthly,
                i.rsi_slope,
                i.trend_consistency,
                fr.funding_rate,
                pbm.basis_bps,
                pbm.premium_index,
                (pbm.basis_bps - pbm2.basis_bps) AS cross_venue_basis_spread_bps,
                (pbm.premium_index - pbm2.premium_index) AS cross_venue_premium_spread,
                o.high_price,
                o.low_price
            FROM indicators i
            INNER JOIN ohlcv o
                ON i.time = o.time
                AND i.symbol = o.symbol
                AND i.timeframe = o.timeframe
            LEFT JOIN perp_basis_metrics pbm
                ON pbm.time = i.time
                AND pbm.symbol = i.symbol
                AND pbm.timeframe = i.timeframe
                AND pbm.exchange = 'binance_usdm'
            LEFT JOIN perp_basis_metrics pbm2
                ON pbm2.time = i.time
                AND pbm2.symbol = i.symbol
                AND pbm2.timeframe = i.timeframe
                AND pbm2.exchange = 'bybit'
            LEFT JOIN LATERAL (
                SELECT funding_rate
                FROM funding_rates
                WHERE symbol = i.symbol
                  AND funding_time <= i.time
                ORDER BY funding_time DESC
                LIMIT 1
            ) fr ON TRUE
            WHERE i.symbol = $1 AND i.timeframe = $2
            ORDER BY i.time DESC
            LIMIT $3
        """

        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, symbol, timeframe, limit)

        if not rows:
            return []

        # Reverse to get oldest-first, then convert to dicts
        rows = list(rows)
        rows.reverse()
        results = []
        for row in rows:
            high_price = row["high_price"] if "high_price" in row else None
            low_price = row["low_price"] if "low_price" in row else None
            results.append(
                {
                    "time": row["time"],
                    "ema_12": (float(row["ema_12"]) if row["ema_12"] is not None else 0.0),
                    "ema_26": (float(row["ema_26"]) if row["ema_26"] is not None else 0.0),
                    "close_price": float(row["close_price"]),
                    "rsi_14": (float(row["rsi_14"]) if row["rsi_14"] is not None else None),
                    "rsi_7": float(row["rsi_7"]) if row["rsi_7"] is not None else None,
                    "macd": float(row["macd"]) if row["macd"] is not None else None,
                    "macd_signal": (
                        float(row["macd_signal"]) if row["macd_signal"] is not None else None
                    ),
                    "macd_hist": (
                        float(row["macd_hist"]) if row["macd_hist"] is not None else None
                    ),
                    "bb_upper_dist": (
                        float(row["bb_upper_dist"]) if row["bb_upper_dist"] is not None else None
                    ),
                    "bb_lower_dist": (
                        float(row["bb_lower_dist"]) if row["bb_lower_dist"] is not None else None
                    ),
                    "atr_14": (float(row["atr_14"]) if row["atr_14"] is not None else None),
                    "atr_pct": (float(row["atr_pct"]) if row["atr_pct"] is not None else None),
                    "ema_50": (float(row["ema_50"]) if row["ema_50"] is not None else None),
                    "ema_200": (float(row["ema_200"]) if row["ema_200"] is not None else None),
                    "sma_20": (float(row["sma_20"]) if row["sma_20"] is not None else None),
                    "sma_40": (float(row["sma_40"]) if row["sma_40"] is not None else None),
                    "sma_50": (float(row["sma_50"]) if row["sma_50"] is not None else None),
                    "sma_60": (float(row["sma_60"]) if row["sma_60"] is not None else None),
                    "sma_200": (float(row["sma_200"]) if row["sma_200"] is not None else None),
                    "vwap": float(row["vwap"]) if row["vwap"] is not None else None,
                    "stoch_k": (float(row["stoch_k"]) if row["stoch_k"] is not None else None),
                    "stoch_d": (float(row["stoch_d"]) if row["stoch_d"] is not None else None),
                    "cci": float(row["cci"]) if row["cci"] is not None else None,
                    # Regime Features (NEW)
                    "ema_slope_50": (
                        float(row["ema_slope_50"]) if row["ema_slope_50"] is not None else None
                    ),
                    "volatility_percentile": (
                        float(row["volatility_percentile"])
                        if row["volatility_percentile"] is not None
                        else None
                    ),
                    "atr_percentile": (
                        float(row["atr_percentile"]) if row["atr_percentile"] is not None else None
                    ),
                    "volume_regime": (
                        float(row["volume_regime"]) if row["volume_regime"] is not None else None
                    ),
                    "price_vs_weekly": (
                        float(row["price_vs_weekly"])
                        if row["price_vs_weekly"] is not None
                        else None
                    ),
                    "price_vs_monthly": (
                        float(row["price_vs_monthly"])
                        if row["price_vs_monthly"] is not None
                        else None
                    ),
                    "rsi_slope": float(row["rsi_slope"]) if row["rsi_slope"] is not None else None,
                    "trend_consistency": (
                        float(row["trend_consistency"])
                        if row["trend_consistency"] is not None
                        else None
                    ),
                    "funding_rate": (
                        float(row["funding_rate"]) if row["funding_rate"] is not None else None
                    ),
                    "basis_bps": (
                        float(row.get("basis_bps")) if row.get("basis_bps") is not None else None
                    ),
                    "premium_index": (
                        float(row.get("premium_index"))
                        if row.get("premium_index") is not None
                        else None
                    ),
                    "cross_venue_basis_spread_bps": (
                        float(row.get("cross_venue_basis_spread_bps"))
                        if row.get("cross_venue_basis_spread_bps") is not None
                        else None
                    ),
                    "cross_venue_premium_spread": (
                        float(row.get("cross_venue_premium_spread"))
                        if row.get("cross_venue_premium_spread") is not None
                        else None
                    ),
                    "high_price": (
                        float(high_price) if high_price is not None else float(row["close_price"])
                    ),
                    "low_price": (
                        float(low_price) if low_price is not None else float(row["close_price"])
                    ),
                }
            )

        return results

    async def fetch_multi_timeframe(
        self,
        symbol: str,
        entry_timeframe: str,
        regime_timeframe: str,
        start_time: datetime | str,
        end_time: datetime | str,
    ) -> list[dict[str, float]]:
        """Fetch and join multi-timeframe data without lookahead.

        CRITICAL: Uses STRICT inequality (regime_time < entry_time) to ensure
        we only use COMPLETED regime bars. Same-timestamp bars are NOT available.

        Args:
            symbol: Trading pair symbol
            entry_timeframe: Entry timeframe (e.g., '1h')
            regime_timeframe: Regime classification timeframe (e.g., '4h')
            start_time: Start of range
            end_time: End of range

        Returns:
            List of joined indicator rows with regime indicators suffixed
        """
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)

        regime_lookback = timedelta(hours=24)
        regime_start = start_time - regime_lookback

        entry_start_str = start_time.isoformat()
        entry_end_str = end_time.isoformat()
        entry_data = await self._fetch_range_rows(
            symbol, entry_timeframe, entry_start_str, entry_end_str
        )

        if not entry_data:
            return []

        regime_start_str = regime_start.isoformat()
        regime_data = await self._fetch_range_rows(
            symbol, regime_timeframe, regime_start_str, entry_end_str
        )

        joined_data = self._join_timeframes(entry_data, regime_data, regime_timeframe)

        return joined_data

    @staticmethod
    def _parse_timeframe_to_delta(tf_str: str) -> timedelta:
        unit = tf_str[-1]
        if len(tf_str) > 1 and tf_str[:-1].isdigit():
            value = int(tf_str[:-1])
        else:
            # Fallback or strict parsing? Assuming standard format like '1h', '15m'
            # If someone passes 'BTCUSDT', it will fail.
            # Let's assume valid input for now or add basic check
            value = int(tf_str[:-1])

        if unit == "m":
            return timedelta(minutes=value)
        elif unit == "h":
            return timedelta(hours=value)
        elif unit == "d":
            return timedelta(days=value)
        elif unit == "w":
            return timedelta(weeks=value)
        raise ValueError(f"Unknown timeframe format: {tf_str}")

    @staticmethod
    def _join_timeframes(
        entry_data: list[dict],
        regime_data: list[dict],
        regime_timeframe: str,
    ) -> list[dict[str, float]]:
        """Join regime indicators onto entry bars without lookahead.

        CRITICAL: Uses STRICT regime_close_time <= entry_open_time logic.
        This ensures we only use regime bars that have fully completed
        before or at the entry bar timestamp.

        Args:
            entry_data: List of entry-timeframe bars, each with 'time' key
            regime_data: List of regime-timeframe bars, each with 'time' key
            regime_timeframe: String representation of regime timeframe (e.g. "4h")

        Returns:
            List of joined dicts with regime indicators suffixed
        """
        if not entry_data:
            return []

        regime_duration = IndicatorReader._parse_timeframe_to_delta(regime_timeframe)
        regime_suffix = f"_{regime_timeframe}"

        joined = []
        regime_idx = 0
        current_regime: dict = {}

        for entry_bar in entry_data:
            entry_time = entry_bar.get("time")

            while regime_idx < len(regime_data):
                regime_bar = regime_data[regime_idx]
                regime_time = regime_bar.get("time")

                if regime_time is None:
                    regime_idx += 1
                    continue

                regime_close_time = regime_time + regime_duration

                # CRITICAL: Regime bar must be CLOSED before or at entry OPEN time.
                # Example: 4h bar opens 08:00, closes 12:00.
                # Entry bar opens 12:00.
                # 12:00 <= 12:00 -> True. (Available)
                # Entry bar opens 11:00.
                # 12:00 <= 11:00 -> False. (Not available)
                if regime_close_time <= entry_time:
                    current_regime = regime_bar
                    regime_idx += 1
                else:
                    break

            joined_bar = dict(entry_bar)
            for key, value in current_regime.items():
                if key != "time" and value is not None:
                    joined_bar[f"{key}{regime_suffix}"] = value

            joined.append(joined_bar)

        return joined
