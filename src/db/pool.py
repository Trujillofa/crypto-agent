"""Async connection pool for TimescaleDB using asyncpg.

This module provides a singleton connection pool for efficient async database access,
replacing the synchronous pg8000 driver with proper async/await patterns.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager

import asyncpg

from src.utils.logger import get_logger

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def init_pool(config: Mapping[str, object]) -> asyncpg.Pool:
    """Initialize the async connection pool.

    Args:
        config: Database configuration with host, port, name, user, password keys.

    Returns:
        Initialized asyncpg connection pool.
    """
    global _pool

    async with _pool_lock:
        if _pool is not None:
            return _pool

        host = str(config.get("host", "localhost"))
        port = int(config.get("port", 5432))
        database = str(config.get("name", "marketdata"))
        user = str(config.get("user", "trading"))
        password = str(config.get("password", ""))

        logger = get_logger("db.pool")
        logger.info(
            "Initializing async connection pool for %s:%d/%s",
            host,
            port,
            database,
        )

        _pool = await asyncpg.create_pool(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            min_size=2,
            max_size=10,
            command_timeout=30.0,
        )

        logger.info("Async connection pool initialized successfully")
        return _pool


async def close_pool() -> None:
    """Close the connection pool gracefully."""
    global _pool

    async with _pool_lock:
        if _pool is not None:
            logger = get_logger("db.pool")
            logger.info("Closing async connection pool...")
            await _pool.close()
            _pool = None
            logger.info("Async connection pool closed")


async def is_connected() -> bool:
    """Check if the database pool is connected and responding.

    Returns:
        True if connected and query succeeds, False otherwise.
    """
    if _pool is None:
        return False
    try:
        async with _pool.acquire(timeout=2.0) as conn:
            await conn.fetchval("SELECT 1")
            return True
    except Exception:  # noqa: BLE001
        return False


def get_pool() -> asyncpg.Pool:
    """Get the current connection pool.

    Returns:
        The active asyncpg connection pool.

    Raises:
        RuntimeError: If pool hasn't been initialized.
    """
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool(config) first.")
    return _pool


@asynccontextmanager
async def get_connection() -> asyncpg.Connection:
    """Context manager for getting a connection from the pool.

    Usage:
        async with get_connection() as conn:
            await conn.fetchval("SELECT 1")
    """
    pool = get_pool()
    async with pool.acquire() as connection:
        yield connection


class DatabasePool:
    """Wrapper class for asyncpg pool operations.

    Provides helper methods for common database operations with type safety.
    """

    def __init__(self, config: Mapping[str, object]) -> None:
        self._config = config
        self._logger = get_logger(self.__class__.__name__)

    async def __aenter__(self) -> DatabasePool:
        await init_pool(self._config)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await close_pool()

    async def execute(
        self,
        query: str,
        *args: object,
    ) -> str:
        """Execute a single query and return status."""
        async with get_connection() as conn:
            return await conn.execute(query, *args)

    async def fetchval(
        self,
        query: str,
        *args: object,
    ) -> object:
        """Fetch a single value from the database."""
        async with get_connection() as conn:
            return await conn.fetchval(query, *args)

    async def fetchrow(
        self,
        query: str,
        *args: object,
    ) -> asyncpg.Record | None:
        """Fetch a single row from the database."""
        async with get_connection() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch(
        self,
        query: str,
        *args: object,
    ) -> list[asyncpg.Record]:
        """Fetch multiple rows from the database."""
        async with get_connection() as conn:
            return await conn.fetch(query, *args)

    async def fetch_rows(
        self,
        query: str,
        *args: object,
    ) -> list[dict[str, object]]:
        """Fetch rows as dictionaries for easier access."""
        async with get_connection() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(row) for row in rows]
