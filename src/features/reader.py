from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from typing import Any

import pg8000

from src.utils.logger import get_logger


class IndicatorReader:
    """Read latest indicators from database for strategy evaluation.

    Follows the same pg8000 + asyncio.to_thread + SQLite fallback pattern
    as IndicatorWriter for consistency.
    """

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._connected = False
        self._conn: Any | None = None
        self._use_sqlite = False

    async def __aenter__(self) -> "IndicatorReader":
        await asyncio.to_thread(self._connect)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
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
        return await asyncio.to_thread(self._fetch_rows, symbol, timeframe, limit)

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
        return await asyncio.to_thread(
            self._fetch_range_rows, symbol, timeframe, start_time, end_time
        )

    def _connect(self) -> None:
        """Connect to TimescaleDB via pg8000, fallback to SQLite."""
        host = str(self._config.get("host", "localhost"))
        port = int(self._config.get("port", 5432))
        database = str(self._config.get("name", "marketdata"))
        user = str(self._config.get("user", "trading"))
        password = str(self._config.get("password", ""))

        try:
            self._conn = pg8000.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
            )
            self._use_sqlite = False
            self._connected = True
            self._logger.info("IndicatorReader: Connected to TimescaleDB via pg8000")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("IndicatorReader: Falling back to SQLite: %s", exc)
            sqlite_path = "/tmp/indicators.sqlite"
            self._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
            self._use_sqlite = True
            self._connected = True
            self._logger.info(
                "IndicatorReader: Connected to SQLite for local persistence"
            )

    def _fetch_range_rows(
        self, symbol: str, timeframe: str, start_time: str, end_time: str
    ) -> list[dict[str, float]]:
        """Fetch rows from database for a specific time range (blocking I/O)."""
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        cursor = self._conn.cursor()

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
            WHERE i.symbol = %s AND i.timeframe = %s
            AND i.time >= %s AND i.time <= %s
            ORDER BY i.time ASC
        """

        if self._use_sqlite:
            query = query.replace("%s", "?")

        cursor.execute(query, (symbol, timeframe, start_time, end_time))
        rows = cursor.fetchall()

        if not rows:
            return []

        results = []
        for row in rows:
            results.append(
                {
                    "time": row[0],  # Include time for backtesting
                    "ema_12": float(row[1]) if row[1] is not None else 0.0,
                    "ema_26": float(row[2]) if row[2] is not None else 0.0,
                    "close_price": float(row[3]),
                    "rsi_14": float(row[4]) if row[4] is not None else None,
                    "rsi_7": float(row[5]) if row[5] is not None else None,
                    "macd": float(row[6]) if row[6] is not None else None,
                    "macd_signal": float(row[7]) if row[7] is not None else None,
                    "macd_hist": float(row[8]) if row[8] is not None else None,
                    "bb_upper_dist": float(row[9]) if row[9] is not None else None,
                    "bb_lower_dist": float(row[10]) if row[10] is not None else None,
                    "atr_14": float(row[11]) if row[11] is not None else None,
                    "atr_pct": float(row[12]) if row[12] is not None else None,
                    "ema_50": float(row[13]) if row[13] is not None else None,
                    "ema_200": float(row[14]) if row[14] is not None else None,
                    "sma_20": float(row[15]) if row[15] is not None else None,
                    "sma_50": float(row[16]) if row[16] is not None else None,
                    "sma_200": float(row[17]) if row[17] is not None else None,
                    "vwap": float(row[18]) if row[18] is not None else None,
                    "stoch_k": float(row[19]) if row[19] is not None else None,
                    "stoch_d": float(row[20]) if row[20] is not None else None,
                    "cci": float(row[21]) if row[21] is not None else None,
                }
            )

        return results

    def _fetch_rows(
        self, symbol: str, timeframe: str, limit: int
    ) -> list[dict[str, float]]:
        """Fetch rows from database (blocking I/O)."""
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        cursor = self._conn.cursor()

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
            WHERE i.symbol = %s AND i.timeframe = %s
            ORDER BY i.time DESC
            LIMIT %s
        """

        if self._use_sqlite:
            # SQLite uses ? placeholders
            query = query.replace("%s", "?")

        cursor.execute(query, (symbol, timeframe, limit))
        rows = cursor.fetchall()

        if not rows:
            return []

        # Reverse to get oldest-first, then convert to dicts
        rows = list(rows)
        rows.reverse()
        results = []
        for row in rows:
            results.append(
                {
                    "ema_12": float(row[1]) if row[1] is not None else 0.0,
                    "ema_26": float(row[2]) if row[2] is not None else 0.0,
                    "close_price": float(row[3]),
                    "rsi_14": float(row[4]) if row[4] is not None else None,
                    "rsi_7": float(row[5]) if row[5] is not None else None,
                    "macd": float(row[6]) if row[6] is not None else None,
                    "macd_signal": float(row[7]) if row[7] is not None else None,
                    "macd_hist": float(row[8]) if row[8] is not None else None,
                    "bb_upper_dist": float(row[9]) if row[9] is not None else None,
                    "bb_lower_dist": float(row[10]) if row[10] is not None else None,
                    "atr_14": float(row[11]) if row[11] is not None else None,
                    "atr_pct": float(row[12]) if row[12] is not None else None,
                    "ema_50": float(row[13]) if row[13] is not None else None,
                    "ema_200": float(row[14]) if row[14] is not None else None,
                    "sma_20": float(row[15]) if row[15] is not None else None,
                    "sma_50": float(row[16]) if row[16] is not None else None,
                    "sma_200": float(row[17]) if row[17] is not None else None,
                    "vwap": float(row[18]) if row[18] is not None else None,
                    "stoch_k": float(row[19]) if row[19] is not None else None,
                    "stoch_d": float(row[20]) if row[20] is not None else None,
                    "cci": float(row[21]) if row[21] is not None else None,
                }
            )

        return results
