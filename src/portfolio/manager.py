"""Portfolio manager for tracking positions and calculating PnL."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

import asyncpg

from src.portfolio.models import Position, PositionStatus, PortfolioSummary
from src.utils.logger import get_logger


class PortfolioManager:
    """Manages positions and trade history with database persistence."""

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)
        self._conn: asyncpg.Connection | None = None
        self._connected = False
        self._positions: dict[tuple[str, str], Position] = {}

    async def __aenter__(self) -> "PortfolioManager":
        await self._connect()
        await self._ensure_schema()
        await self._load_open_positions()
        return self

    @staticmethod
    def _position_key(symbol: str, market: str) -> tuple[str, str]:
        return (market, symbol)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._conn is not None:
            await self._conn.close()
        self._connected = False

    async def _connect(self) -> None:
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
        self._logger.info("PortfolioManager: Connected to TimescaleDB via asyncpg")

    async def _ensure_schema(self) -> None:
        if not self._connected or self._conn is None:
            raise RuntimeError("Database not connected")

        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id SERIAL PRIMARY KEY,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'spot',
                entry_time TIMESTAMPTZ NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                exit_time TIMESTAMPTZ,
                exit_price DOUBLE PRECISION,
                realized_pnl DOUBLE PRECISION
            )
            """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                time TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT 'spot',
                side TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                order_id TEXT,
                pnl DOUBLE PRECISION,
                position_id INTEGER REFERENCES positions(id)
            )
            """)
        await self._conn.execute(
            "ALTER TABLE positions ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'spot'"
        )
        await self._conn.execute(
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'spot'"
        )

    async def _load_open_positions(self) -> None:
        """Load open positions from database into cache."""
        await self._normalize_open_positions()
        rows = await self._fetch_open_positions()
        for row in rows:
            position = self._row_to_position(row)
            market = str(row.get("market") or "spot")
            self._positions[self._position_key(position.symbol, market)] = position
        self._logger.info(f"Loaded {len(self._positions)} open positions")

    async def _normalize_open_positions(self) -> None:
        if self._conn is None:
            return

        duplicates = await self._conn.fetch(
            """
            SELECT market, symbol, ARRAY_AGG(id ORDER BY entry_time DESC, id DESC) AS ids
            FROM positions
            WHERE status = 'open'
            GROUP BY market, symbol
            HAVING COUNT(*) > 1
            """
        )

        for row in duplicates:
            ids = list(row["ids"])
            stale_ids = [int(position_id) for position_id in ids[1:]]
            if not stale_ids:
                continue

            await self._conn.execute(
                """
                UPDATE positions
                SET status = 'closed',
                    exit_time = COALESCE(exit_time, entry_time),
                    exit_price = COALESCE(exit_price, entry_price),
                    realized_pnl = COALESCE(realized_pnl, 0)
                WHERE id = ANY($1::int[])
                """,
                stale_ids,
            )
            self._logger.warning(
                "Normalized duplicate open positions for %s/%s; closed stale IDs: %s",
                row["market"],
                row["symbol"],
                stale_ids,
            )

    async def _fetch_open_positions(self) -> list[asyncpg.Record]:
        if self._conn is None:
            return []
        return await self._conn.fetch(
            "SELECT * FROM positions WHERE status = $1 ORDER BY entry_time DESC",
            "open",
        )

    def _row_to_position(self, row: asyncpg.Record) -> Position:
        """Convert database row to Position object."""
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            entry_time=row["entry_time"],
            entry_price=float(row["entry_price"]),
            quantity=float(row["quantity"]),
            status=PositionStatus(row["status"]),
            exit_time=row["exit_time"],
            exit_price=float(row["exit_price"]) if row["exit_price"] else None,
            realized_pnl=float(row["realized_pnl"]) if row["realized_pnl"] else None,
        )

    async def open_position(
        self,
        symbol: str,
        quantity: float,
        price: float,
        order_id: str | None = None,
        market: str = "spot",
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
        entry_time = datetime.now(timezone.utc)
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        async with self._conn.transaction():
            position_id = await self._conn.fetchval(
                """
                INSERT INTO positions (symbol, market, entry_time, entry_price, quantity, status)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                symbol,
                market,
                entry_time,
                price,
                quantity,
                "open",
            )

            await self._conn.execute(
                """
                INSERT INTO trades (time, symbol, market, side, quantity, price, order_id, position_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                entry_time,
                symbol,
                market,
                "BUY",
                quantity,
                price,
                order_id,
                position_id,
            )

        position = Position(
            id=position_id,
            symbol=symbol,
            entry_time=entry_time,
            entry_price=price,
            quantity=quantity,
            status=PositionStatus.OPEN,
        )

        self._positions[self._position_key(symbol, market)] = position
        self._logger.info(f"Opened position: {symbol} @ {price} (qty: {quantity})")

        return position

    async def close_position(
        self,
        symbol: str,
        price: float,
        order_id: str | None = None,
        market: str = "spot",
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
        key = self._position_key(symbol, market)
        if key not in self._positions:
            raise ValueError(f"No open position for {symbol}")

        position = self._positions[key]
        exit_time = datetime.now(timezone.utc)
        realized_pnl = position.close(price, exit_time)
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        async with self._conn.transaction():
            await self._conn.execute(
                """
                UPDATE positions
                SET status = $1, exit_time = $2, exit_price = $3, realized_pnl = $4
                WHERE id = $5
                """,
                "closed",
                exit_time,
                price,
                realized_pnl,
                position.id,
            )
            await self._conn.execute(
                """
                INSERT INTO trades (time, symbol, market, side, quantity, price, order_id, pnl, position_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                exit_time,
                symbol,
                market,
                "SELL",
                position.quantity,
                price,
                order_id,
                realized_pnl,
                position.id,
            )
        del self._positions[key]

        self._logger.info(
            f"Closed position: {symbol} @ {price} (PnL: {realized_pnl:.2f})"
        )

        return position, realized_pnl

    def get_position(self, symbol: str, market: str = "spot") -> Position | None:
        """Get current open position for symbol."""
        return self._positions.get(self._position_key(symbol, market))

    def has_position(self, symbol: str, market: str = "spot") -> bool:
        """Check if there's an open position for symbol."""
        return self._position_key(symbol, market) in self._positions

    def get_all_positions(self) -> list[Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def calculate_unrealized_pnl(
        self, symbol: str, current_price: float, market: str = "spot"
    ) -> float:
        """Calculate unrealized PnL for a position."""
        position = self._positions.get(self._position_key(symbol, market))
        if position is None:
            return 0.0
        return position.calculate_unrealized_pnl(current_price)

    async def get_portfolio_summary(self) -> PortfolioSummary:
        """Get portfolio summary statistics."""
        if self._conn is None:
            raise RuntimeError("Database connection missing")

        total_positions = await self._conn.fetchval("SELECT COUNT(*) FROM positions")
        open_positions = await self._conn.fetchval(
            "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        )
        closed_positions = await self._conn.fetchval(
            "SELECT COUNT(*) FROM positions WHERE status = 'closed'"
        )
        total_trades = await self._conn.fetchval("SELECT COUNT(*) FROM trades")
        total_realized_pnl = await self._conn.fetchval(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed'"
        )
        win_count = await self._conn.fetchval(
            "SELECT COUNT(*) FROM positions WHERE status = 'closed' AND realized_pnl > 0"
        )
        loss_count = await self._conn.fetchval(
            "SELECT COUNT(*) FROM positions WHERE status = 'closed' AND realized_pnl < 0"
        )

        # Calculate unrealized PnL for open positions
        total_unrealized = 0.0

        return PortfolioSummary(
            total_positions=int(total_positions or 0),
            open_positions=int(open_positions or 0),
            closed_positions=int(closed_positions or 0),
            total_trades=int(total_trades or 0),
            total_realized_pnl=float(total_realized_pnl or 0),
            total_unrealized_pnl=total_unrealized,
            win_count=int(win_count or 0),
            loss_count=int(loss_count or 0),
        )
