from __future__ import annotations

from collections.abc import Mapping

import asyncpg

from src.utils.logger import get_logger


class IndicatorReader:
    """Read latest indicators from database for strategy evaluation.

    Uses asyncpg for async database access.
    """

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._connected = False
        self._conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> "IndicatorReader":
        await self._connect()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await self._conn.close()
        self._connected = False

    async def fetch_latest(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 2,
    ) -> list[dict[str, float]]:
        """Fetch latest N indicator rows for symbol+timeframe, oldest-first.

        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe (e.g., "1m", "5m", "1h")
            limit: Maximum number of rows to fetch (default: 2)

        Returns:
            List of dicts with keys: ema_12, ema_26, close_price.
            Sorted oldest to newest (time ASC).
            Empty list if no data.
        """
        if not self._connected:
            raise RuntimeError("IndicatorReader connection not initialized")
        return await self._fetch_rows(symbol, timeframe, limit)

    async def fetch_range(
        self,
        symbol: str,
        timeframe: str,
        start_time: str,
        end_time: str,
    ) -> list[dict[str, float]]:
        """Fetch all indicator rows for symbol+timeframe within a time range.

        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe (e.g., "1m", "5m")
            start_time: ISO 8601 start timestamp
            end_time: ISO 8601 end timestamp

        Returns:
            List of dicts with indicator keys, sorted oldest to newest (time ASC).
        """
        if not self._connected:
            raise RuntimeError("IndicatorReader connection not initialized")
        return await self._fetch_range_rows(symbol, timeframe, start_time, end_time)

    async def _connect(self) -> None:
        """Connect to TimescaleDB via asyncpg."""
        host = str(self._config.get("host", "localhost"))
        port = int(self._config.get("port", 5432))
        database = str(self._config.get("name", "marketdata"))
        user = str(self._config.get("user", "trading"))
        password = str(self._config.get("password", ""))

        self._conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        self._connected = True
        self._logger.info("IndicatorReader: Connected to TimescaleDB via asyncpg")

    async def _fetch_range_rows(
        self, symbol: str, timeframe: str, start_time: str, end_time: str
    ) -> list[dict[str, float]]:
        """Fetch rows from database for a specific time range."""
        if self._conn is None:
            raise RuntimeError("Database connection missing")

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
                i.sma_50,
                i.sma_200,
                i.vwap,
                i.stoch_k,
                i.stoch_d,
                i.cci,
                o.high_price,
                o.low_price
            FROM indicators i
            INNER JOIN ohlcv o
                ON i.time = o.time
                AND i.symbol = o.symbol
                AND i.timeframe = o.timeframe
            WHERE i.symbol = $1 AND i.timeframe = $2
            AND i.time >= $3 AND i.time <= $4
            ORDER BY i.time ASC
        """

        rows = await self._conn.fetch(query, symbol, timeframe, start_time, end_time)

        if not rows:
            return []

        results = []
        for row in rows:
            results.append(
                {
                    "time": row["time"],  # Include time for backtesting
                    "ema_12": float(row["ema_12"])
                    if row["ema_12"] is not None
                    else 0.0,
                    "ema_26": float(row["ema_26"])
                    if row["ema_26"] is not None
                    else 0.0,
                    "close_price": float(row["close_price"]),
                    "rsi_14": float(row["rsi_14"])
                    if row["rsi_14"] is not None
                    else None,
                    "rsi_7": float(row["rsi_7"]) if row["rsi_7"] is not None else None,
                    "macd": float(row["macd"]) if row["macd"] is not None else None,
                    "macd_signal": float(row["macd_signal"])
                    if row["macd_signal"] is not None
                    else None,
                    "macd_hist": float(row["macd_hist"])
                    if row["macd_hist"] is not None
                    else None,
                    "bb_upper_dist": float(row["bb_upper_dist"])
                    if row["bb_upper_dist"] is not None
                    else None,
                    "bb_lower_dist": float(row["bb_lower_dist"])
                    if row["bb_lower_dist"] is not None
                    else None,
                    "atr_14": float(row["atr_14"])
                    if row["atr_14"] is not None
                    else None,
                    "atr_pct": float(row["atr_pct"])
                    if row["atr_pct"] is not None
                    else None,
                    "ema_50": float(row["ema_50"])
                    if row["ema_50"] is not None
                    else None,
                    "ema_200": float(row["ema_200"])
                    if row["ema_200"] is not None
                    else None,
                    "sma_20": float(row["sma_20"])
                    if row["sma_20"] is not None
                    else None,
                    "sma_50": float(row["sma_50"])
                    if row["sma_50"] is not None
                    else None,
                    "sma_200": float(row["sma_200"])
                    if row["sma_200"] is not None
                    else None,
                    "vwap": float(row["vwap"]) if row["vwap"] is not None else None,
                    "stoch_k": float(row["stoch_k"])
                    if row["stoch_k"] is not None
                    else None,
                    "stoch_d": float(row["stoch_d"])
                    if row["stoch_d"] is not None
                    else None,
                    "cci": float(row["cci"]) if row["cci"] is not None else None,
                    "high_price": float(row["high_price"])
                    if row["high_price"] is not None
                    else float(row["close_price"]),
                    "low_price": float(row["low_price"])
                    if row["low_price"] is not None
                    else float(row["close_price"]),
                }
            )

        return results

    async def _fetch_rows(
        self, symbol: str, timeframe: str, limit: int
    ) -> list[dict[str, float]]:
        """Fetch rows from database."""
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        # JOIN with ohlcv table to get close_price
        # Use DESC ordering and reverse to get oldest-first
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
                i.sma_50,
                i.sma_200,
                i.vwap,
                i.stoch_k,
                i.stoch_d,
                i.cci
            FROM indicators i
            INNER JOIN ohlcv o
                ON i.time = o.time
                AND i.symbol = o.symbol
                AND i.timeframe = o.timeframe
            WHERE i.symbol = $1 AND i.timeframe = $2
            ORDER BY i.time DESC
            LIMIT $3
        """

        rows = await self._conn.fetch(query, symbol, timeframe, limit)

        if not rows:
            return []

        # Reverse to get oldest-first, then convert to dicts
        rows = list(rows)
        rows.reverse()
        results = []
        for row in rows:
            results.append(
                {
                    "ema_12": float(row["ema_12"])
                    if row["ema_12"] is not None
                    else 0.0,
                    "ema_26": float(row["ema_26"])
                    if row["ema_26"] is not None
                    else 0.0,
                    "close_price": float(row["close_price"]),
                    "rsi_14": float(row["rsi_14"])
                    if row["rsi_14"] is not None
                    else None,
                    "rsi_7": float(row["rsi_7"]) if row["rsi_7"] is not None else None,
                    "macd": float(row["macd"]) if row["macd"] is not None else None,
                    "macd_signal": float(row["macd_signal"])
                    if row["macd_signal"] is not None
                    else None,
                    "macd_hist": float(row["macd_hist"])
                    if row["macd_hist"] is not None
                    else None,
                    "bb_upper_dist": float(row["bb_upper_dist"])
                    if row["bb_upper_dist"] is not None
                    else None,
                    "bb_lower_dist": float(row["bb_lower_dist"])
                    if row["bb_lower_dist"] is not None
                    else None,
                    "atr_14": float(row["atr_14"])
                    if row["atr_14"] is not None
                    else None,
                    "atr_pct": float(row["atr_pct"])
                    if row["atr_pct"] is not None
                    else None,
                    "ema_50": float(row["ema_50"])
                    if row["ema_50"] is not None
                    else None,
                    "ema_200": float(row["ema_200"])
                    if row["ema_200"] is not None
                    else None,
                    "sma_20": float(row["sma_20"])
                    if row["sma_20"] is not None
                    else None,
                    "sma_50": float(row["sma_50"])
                    if row["sma_50"] is not None
                    else None,
                    "sma_200": float(row["sma_200"])
                    if row["sma_200"] is not None
                    else None,
                    "vwap": float(row["vwap"]) if row["vwap"] is not None else None,
                    "stoch_k": float(row["stoch_k"])
                    if row["stoch_k"] is not None
                    else None,
                    "stoch_d": float(row["stoch_d"])
                    if row["stoch_d"] is not None
                    else None,
                    "cci": float(row["cci"]) if row["cci"] is not None else None,
                }
            )

        return results
