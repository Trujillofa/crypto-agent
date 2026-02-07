"""Portfolio manager for tracking positions and calculating PnL."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pg8000

from src.portfolio.models import Position, PositionStatus, Trade, PortfolioSummary
from src.utils.logger import get_logger


class PortfolioManager:
    """Manages positions and trade history with database persistence."""

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._conn: Any | None = None
        self._use_sqlite = False
        self._connected = False
        self._positions: dict[str, Position] = {}  # Cache of open positions by symbol

    async def __aenter__(self) -> "PortfolioManager":
        await asyncio.to_thread(self._connect)
        await asyncio.to_thread(self._ensure_schema)
        await self._load_open_positions()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
        self._connected = False

    def _connect(self) -> None:
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
            self._logger.info("PortfolioManager: Connected to TimescaleDB")
        except Exception:
            self._logger.warning("PortfolioManager: Falling back to SQLite")
            sqlite_path = "data/portfolio.sqlite"
            import os

            os.makedirs("data", exist_ok=True)
            self._conn = sqlite3.connect(sqlite_path)
            self._use_sqlite = True
            self._connected = True
            self._logger.info("PortfolioManager: Connected to SQLite")

    def _ensure_schema(self) -> None:
        if not self._connected or self._conn is None:
            raise RuntimeError("Database not connected")

        cursor = self._conn.cursor()

        if self._use_sqlite:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    exit_time TEXT,
                    exit_price REAL,
                    realized_pnl REAL
                )
            """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    order_id TEXT,
                    pnl REAL,
                    position_id INTEGER,
                    FOREIGN KEY (position_id) REFERENCES positions (id)
                )
            """
            )
        else:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    entry_time TIMESTAMPTZ NOT NULL,
                    entry_price DOUBLE PRECISION NOT NULL,
                    quantity DOUBLE PRECISION NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    exit_time TIMESTAMPTZ,
                    exit_price DOUBLE PRECISION,
                    realized_pnl DOUBLE PRECISION
                )
            """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    time TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity DOUBLE PRECISION NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    order_id TEXT,
                    pnl DOUBLE PRECISION,
                    position_id INTEGER REFERENCES positions(id)
                )
            """
            )

        self._conn.commit()

    async def _load_open_positions(self) -> None:
        """Load open positions from database into cache."""
        rows = await asyncio.to_thread(self._fetch_open_positions)
        for row in rows:
            position = self._row_to_position(row)
            self._positions[position.symbol] = position
        self._logger.info(f"Loaded {len(self._positions)} open positions")

    def _fetch_open_positions(self) -> list[tuple]:
        if self._conn is None:
            return []
        cursor = self._conn.cursor()
        if self._use_sqlite:
            cursor.execute(
                "SELECT * FROM positions WHERE status = ? ORDER BY entry_time DESC",
                ("open",),
            )
        else:
            cursor.execute(
                "SELECT * FROM positions WHERE status = %s ORDER BY entry_time DESC",
                ("open",),
            )
        return cursor.fetchall()

    def _row_to_position(self, row: tuple) -> Position:
        """Convert database row to Position object."""
        if self._use_sqlite:
            return Position(
                id=row[0],
                symbol=row[1],
                entry_time=datetime.fromisoformat(row[2]),
                entry_price=float(row[3]),
                quantity=float(row[4]),
                status=PositionStatus(row[5]),
                exit_time=datetime.fromisoformat(row[6]) if row[6] else None,
                exit_price=float(row[7]) if row[7] else None,
                realized_pnl=float(row[8]) if row[8] else None,
            )
        else:
            return Position(
                id=row[0],
                symbol=row[1],
                entry_time=row[2],
                entry_price=float(row[3]),
                quantity=float(row[4]),
                status=PositionStatus(row[5]),
                exit_time=row[6],
                exit_price=float(row[7]) if row[7] else None,
                realized_pnl=float(row[8]) if row[8] else None,
            )

    async def open_position(
        self, symbol: str, quantity: float, price: float, order_id: str | None = None
    ) -> Position:
        """Open a new position.

        Args:
            symbol: Trading pair symbol
            quantity: Amount to buy (base asset units)
            price: Entry price
            order_id: Associated Binance order ID

        Returns:
            The created Position
        """
        entry_time = datetime.utcnow()

        if self._use_sqlite:
            insert_query = """
                INSERT INTO positions (symbol, entry_time, entry_price, quantity, status)
                VALUES (?, ?, ?, ?, ?)
            """
            trade_query = """
                INSERT INTO trades (time, symbol, side, quantity, price, order_id, position_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
        else:
            insert_query = """
                INSERT INTO positions (symbol, entry_time, entry_price, quantity, status)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """
            trade_query = """
                INSERT INTO trades (time, symbol, side, quantity, price, order_id, position_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """

        def _insert():
            cursor = self._conn.cursor()
            cursor.execute(
                insert_query,
                (
                    symbol,
                    entry_time.isoformat() if self._use_sqlite else entry_time,
                    price,
                    quantity,
                    "open",
                ),
            )

            if self._use_sqlite:
                position_id = cursor.lastrowid
            else:
                position_id = cursor.fetchone()[0]

            cursor.execute(
                trade_query,
                (
                    entry_time.isoformat() if self._use_sqlite else entry_time,
                    symbol,
                    "BUY",
                    quantity,
                    price,
                    order_id,
                    position_id,
                ),
            )
            self._conn.commit()
            return position_id

        position_id = await asyncio.to_thread(_insert)

        position = Position(
            id=position_id,
            symbol=symbol,
            entry_time=entry_time,
            entry_price=price,
            quantity=quantity,
            status=PositionStatus.OPEN,
        )

        self._positions[symbol] = position
        self._logger.info(f"Opened position: {symbol} @ {price} (qty: {quantity})")

        return position

    async def close_position(
        self, symbol: str, price: float, order_id: str | None = None
    ) -> tuple[Position, float]:
        """Close an existing position.

        Args:
            symbol: Trading pair symbol
            price: Exit price
            order_id: Associated Binance order ID

        Returns:
            Tuple of (closed Position, realized PnL)

        Raises:
            ValueError: If no open position exists for symbol
        """
        if symbol not in self._positions:
            raise ValueError(f"No open position for {symbol}")

        position = self._positions[symbol]
        exit_time = datetime.utcnow()
        realized_pnl = position.close(price, exit_time)

        if self._use_sqlite:
            update_query = """
                UPDATE positions
                SET status = ?, exit_time = ?, exit_price = ?, realized_pnl = ?
                WHERE id = ?
            """
            trade_query = """
                INSERT INTO trades (time, symbol, side, quantity, price, order_id, pnl, position_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
        else:
            update_query = """
                UPDATE positions
                SET status = %s, exit_time = %s, exit_price = %s, realized_pnl = %s
                WHERE id = %s
            """
            trade_query = """
                INSERT INTO trades (time, symbol, side, quantity, price, order_id, pnl, position_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """

        def _update():
            cursor = self._conn.cursor()
            cursor.execute(
                update_query,
                (
                    "closed",
                    exit_time.isoformat() if self._use_sqlite else exit_time,
                    price,
                    realized_pnl,
                    position.id,
                ),
            )
            cursor.execute(
                trade_query,
                (
                    exit_time.isoformat() if self._use_sqlite else exit_time,
                    symbol,
                    "SELL",
                    position.quantity,
                    price,
                    order_id,
                    realized_pnl,
                    position.id,
                ),
            )
            self._conn.commit()

        await asyncio.to_thread(_update)
        del self._positions[symbol]

        self._logger.info(
            f"Closed position: {symbol} @ {price} (PnL: {realized_pnl:.2f})"
        )

        return position, realized_pnl

    def get_position(self, symbol: str) -> Position | None:
        """Get current open position for symbol."""
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        """Check if there's an open position for symbol."""
        return symbol in self._positions

    def get_all_positions(self) -> list[Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def calculate_unrealized_pnl(self, symbol: str, current_price: float) -> float:
        """Calculate unrealized PnL for a position."""
        position = self._positions.get(symbol)
        if position is None:
            return 0.0
        return position.calculate_unrealized_pnl(current_price)

    async def get_portfolio_summary(self) -> PortfolioSummary:
        """Get portfolio summary statistics."""

        def _query():
            cursor = self._conn.cursor()

            # Count positions
            cursor.execute("SELECT COUNT(*) FROM positions")
            total_positions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
            open_positions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed'")
            closed_positions = cursor.fetchone()[0]

            # Count trades
            cursor.execute("SELECT COUNT(*) FROM trades")
            total_trades = cursor.fetchone()[0]

            # Sum realized PnL
            cursor.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed'"
            )
            total_realized_pnl = float(cursor.fetchone()[0] or 0)

            # Count wins/losses
            cursor.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'closed' AND realized_pnl > 0"
            )
            win_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'closed' AND realized_pnl < 0"
            )
            loss_count = cursor.fetchone()[0]

            return {
                "total_positions": total_positions,
                "open_positions": open_positions,
                "closed_positions": closed_positions,
                "total_trades": total_trades,
                "total_realized_pnl": total_realized_pnl,
                "win_count": win_count,
                "loss_count": loss_count,
            }

        stats = await asyncio.to_thread(_query)

        # Calculate unrealized PnL for open positions
        total_unrealized = 0.0

        return PortfolioSummary(
            total_positions=stats["total_positions"],
            open_positions=stats["open_positions"],
            closed_positions=stats["closed_positions"],
            total_trades=stats["total_trades"],
            total_realized_pnl=stats["total_realized_pnl"],
            total_unrealized_pnl=total_unrealized,
            win_count=stats["win_count"],
            loss_count=stats["loss_count"],
        )
