"""Portfolio manager for tracking positions and calculating PnL."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

from src.db.pool import get_pool
from src.portfolio.models import PortfolioSummary, Position, PositionStatus
from src.utils.logger import get_logger


class PortfolioManager:
    """Manages positions and trade history with database persistence.

    Uses a shared connection pool for efficient database access.
    """

    def __init__(self, config: Mapping[str, object], agent_id: str = "default") -> None:
        self._config = config
        self._agent_id = self._normalize_agent_id(agent_id)
        # Keep legacy (unscoped) symbols for default single-agent mode.
        self._symbol_prefix = "" if self._agent_id == "default" else f"{self._agent_id}::"
        self._logger = get_logger(self.__class__.__name__)
        self._positions: dict[tuple[str, str], Position] = {}
        self._db_lock = asyncio.Lock()

    async def __aenter__(self) -> PortfolioManager:
        await self._ensure_schema()
        await self._load_open_positions()
        return self

    @staticmethod
    def _normalize_agent_id(agent_id: str) -> str:
        normalized = "".join(
            ch if (ch.isalnum() or ch in {"-", "_"}) else "_"
            for ch in (agent_id or "default").strip()
        ).strip("_")
        return normalized or "default"

    def _scope_symbol(self, symbol: str) -> str:
        if not self._symbol_prefix:
            return symbol
        if symbol.startswith(self._symbol_prefix):
            return symbol
        return f"{self._symbol_prefix}{symbol}"

    def _descope_symbol(self, symbol: str) -> str | None:
        if not self._symbol_prefix:
            return symbol if "::" not in symbol else None
        if symbol.startswith(self._symbol_prefix):
            return symbol[len(self._symbol_prefix) :]
        return None

    def _position_key(self, symbol: str, market: str) -> tuple[str, str]:
        return (market, self._scope_symbol(symbol))

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        pass

    async def _ensure_schema(self) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
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
            await conn.execute("""
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
            await conn.execute(
                "ALTER TABLE positions ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'spot'"
            )
            await conn.execute(
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'spot'"
            )

    async def _load_open_positions(self) -> None:
        """Load open positions from database into cache."""
        await self._normalize_open_positions()
        rows = await self._fetch_open_positions()
        for row in rows:
            position = self._row_to_position(row)
            if position is None:
                continue
            market = str(row.get("market") or "spot")
            self._positions[self._position_key(position.symbol, market)] = position
        self._logger.info(
            "Loaded %d open positions for agent '%s'",
            len(self._positions),
            self._agent_id,
        )

    async def _normalize_open_positions(self) -> None:
        pool = get_pool()
        async with pool.acquire() as conn:
            if self._symbol_prefix:
                duplicates = await conn.fetch(
                    """
                    SELECT market, symbol, ARRAY_AGG(id ORDER BY entry_time DESC, id DESC) AS ids
                    FROM positions
                    WHERE status = 'open' AND symbol LIKE $1
                    GROUP BY market, symbol
                    HAVING COUNT(*) > 1
                    """,
                    f"{self._symbol_prefix}%",
                )
            else:
                duplicates = await conn.fetch(
                    """
                    SELECT market, symbol, ARRAY_AGG(id ORDER BY entry_time DESC, id DESC) AS ids
                    FROM positions
                    WHERE status = 'open' AND symbol NOT LIKE $1
                    GROUP BY market, symbol
                    HAVING COUNT(*) > 1
                    """,
                    "%::%",
                )

            for row in duplicates:
                ids = list(row["ids"])
                stale_ids = [int(position_id) for position_id in ids[1:]]
                if not stale_ids:
                    continue

                await conn.execute(
                    """
                    UPDATE positions
                    SET status = 'closed',
                        exit_time = COALESCE(exit_time, entry_time),
                        exit_price = COALESCE(exit_price, entry_price),
                        realized_pnl = COALESCE(realized_pnl, 0)
                    WHERE id = ANY($1::int[]) AND agent_id = $2
                    """,
                    stale_ids,
                    self._agent_id,
                )
                self._logger.warning(
                    "Normalized duplicate open positions for %s/%s; closed stale IDs: %s",
                    row["market"],
                    row["symbol"],
                    stale_ids,
                )

    async def _fetch_open_positions(self) -> list[Mapping[str, object]]:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM positions WHERE status = $1 AND agent_id = $2 ORDER BY entry_time DESC",
                "open",
                self._agent_id,
            )
            return [dict(row) for row in rows]

    def _row_to_position(self, row: Mapping[str, object]) -> Position | None:
        """Convert database row to Position object."""
        scoped_symbol = str(row["symbol"])
        symbol = self._descope_symbol(scoped_symbol)
        if symbol is None:
            return None

        return Position(
            id=row["id"],
            symbol=symbol,
            entry_time=row["entry_time"],
            entry_price=float(row["entry_price"]),
            quantity=float(row["quantity"]),
            status=PositionStatus(row["status"]),
            exit_time=row.get("exit_time"),
            exit_price=float(row["exit_price"]) if row.get("exit_price") else None,
            realized_pnl=float(row["realized_pnl"]) if row.get("realized_pnl") else None,
        )

    async def open_position(
        self,
        symbol: str,
        quantity: float,
        price: float,
        order_id: str | None = None,
        market: str = "spot",
    ) -> Position:
        """Open a new position."""
        async with self._db_lock:
            entry_time = datetime.now(UTC)
            scoped_symbol = self._scope_symbol(symbol)
            pool = get_pool()

            async with pool.acquire() as conn:
                async with conn.transaction():
                    position_id = await conn.fetchval(
                        """
                        INSERT INTO positions (symbol, market, entry_time, entry_price, quantity, status, agent_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        RETURNING id
                        """,
                        scoped_symbol,
                        market,
                        entry_time,
                        price,
                        quantity,
                        "open",
                        self._agent_id,
                    )

                    await conn.execute(
                        """
                        INSERT INTO trades (time, symbol, market, side, quantity, price, order_id, position_id, agent_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        entry_time,
                        scoped_symbol,
                        market,
                        "BUY",
                        quantity,
                        price,
                        order_id,
                        position_id,
                        self._agent_id,
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
        """Close an existing position."""
        async with self._db_lock:
            key = self._position_key(symbol, market)
            if key not in self._positions:
                raise ValueError(f"No open position for {symbol}")

            position = self._positions[key]
            exit_time = datetime.now(UTC)
            realized_pnl = position.close(price, exit_time)
            pool = get_pool()

            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE positions
                        SET status = $1, exit_time = $2, exit_price = $3, realized_pnl = $4
                        WHERE id = $5 AND agent_id = $6
                        """,
                        "closed",
                        exit_time,
                        price,
                        realized_pnl,
                        position.id,
                        self._agent_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO trades (time, symbol, market, side, quantity, price, order_id, pnl, position_id, agent_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        """,
                        exit_time,
                        self._scope_symbol(symbol),
                        market,
                        "SELL",
                        position.quantity,
                        price,
                        order_id,
                        realized_pnl,
                        position.id,
                        self._agent_id,
                    )

            del self._positions[key]

            self._logger.info(f"Closed position: {symbol} @ {price} (PnL: {realized_pnl:.2f})")

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
        async with self._db_lock:
            pool = get_pool()
            async with pool.acquire() as conn:
                # Filter by agent_id instead of symbol pattern
                agent_filter = "agent_id = $1"
                agent_param = self._agent_id

                total_positions = await conn.fetchval(
                    f"SELECT COUNT(*) FROM positions WHERE {agent_filter}",
                    agent_param,
                )
                open_positions = await conn.fetchval(
                    f"SELECT COUNT(*) FROM positions WHERE status = 'open' AND {agent_filter}",
                    agent_param,
                )
                closed_positions = await conn.fetchval(
                    f"SELECT COUNT(*) FROM positions WHERE status = 'closed' AND {agent_filter}",
                    agent_param,
                )
                total_trades = await conn.fetchval(
                    f"SELECT COUNT(*) FROM trades WHERE {agent_filter}",
                    agent_param,
                )
                total_realized_pnl = await conn.fetchval(
                    f"SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE status = 'closed' AND {agent_filter}",
                    agent_param,
                )
                win_count = await conn.fetchval(
                    f"SELECT COUNT(*) FROM positions WHERE status = 'closed' AND realized_pnl > 0 AND {agent_filter}",
                    agent_param,
                )
                loss_count = await conn.fetchval(
                    f"SELECT COUNT(*) FROM positions WHERE status = 'closed' AND realized_pnl < 0 AND {agent_filter}",
                    agent_param,
                )

                return PortfolioSummary(
                    total_positions=int(total_positions or 0),
                    open_positions=int(open_positions or 0),
                    closed_positions=int(closed_positions or 0),
                    total_trades=int(total_trades or 0),
                    total_realized_pnl=float(total_realized_pnl or 0),
                    total_unrealized_pnl=0.0,
                    win_count=int(win_count or 0),
                    loss_count=int(loss_count or 0),
                )
