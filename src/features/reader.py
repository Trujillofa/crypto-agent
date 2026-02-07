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
            self._conn = sqlite3.connect(sqlite_path)
            self._use_sqlite = True
            self._connected = True
            self._logger.info(
                "IndicatorReader: Connected to SQLite for local persistence"
            )

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
                o.close_price
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
        rows.reverse()
        results = []
        for row in rows:
            results.append(
                {
                    "ema_12": float(row[1]) if row[1] is not None else 0.0,
                    "ema_26": float(row[2]) if row[2] is not None else 0.0,
                    "close_price": float(row[3]),
                }
            )

        return results
