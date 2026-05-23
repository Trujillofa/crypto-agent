#!/usr/bin/env python3
"""Read-only audit for futures close accounting against Binance fills."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.execution.futures_client import BinanceFuturesClient, FuturesUserTrade

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_AGENT_ID = "sentiment-macro-bot"


@dataclass(frozen=True)
class DbCloseRow:
    position_id: int
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    realized_pnl: float
    exit_time: datetime
    order_id: str | None


@dataclass(frozen=True)
class CloseAuditResult:
    db_close: DbCloseRow
    binance_trade: FuturesUserTrade | None
    price_diff: float | None
    pnl_diff: float | None
    status: str


def normalize_symbol(symbol: str) -> str:
    return symbol.rsplit("::", maxsplit=1)[-1]


def parse_utc_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def trade_datetime(trade: FuturesUserTrade) -> datetime:
    return datetime.fromtimestamp(trade.time / 1000, tz=UTC)


def find_matching_trade(
    close: DbCloseRow,
    trades: list[FuturesUserTrade],
    max_time_delta: timedelta,
) -> FuturesUserTrade | None:
    close_symbol = normalize_symbol(close.symbol)
    close_order_id = str(close.order_id or "")
    close_candidates = [
        trade
        for trade in trades
        if trade.symbol == close_symbol and trade.side == "SELL" and abs(trade.realized_pnl) > 0
    ]
    if close_order_id:
        for trade in close_candidates:
            if trade.order_id == close_order_id:
                return trade

    windowed = [
        trade
        for trade in close_candidates
        if abs(trade_datetime(trade) - close.exit_time) <= max_time_delta
    ]
    if not windowed:
        return None
    return min(windowed, key=lambda trade: abs(trade_datetime(trade) - close.exit_time))


def audit_close(
    close: DbCloseRow,
    trades: list[FuturesUserTrade],
    max_time_delta: timedelta,
    price_tolerance: float,
    pnl_tolerance: float,
) -> CloseAuditResult:
    trade = find_matching_trade(close, trades, max_time_delta)
    if trade is None:
        return CloseAuditResult(close, None, None, None, "missing_binance_match")

    price_diff = close.exit_price - trade.price
    pnl_diff = close.realized_pnl - trade.realized_pnl
    status = (
        "ok" if abs(price_diff) <= price_tolerance and abs(pnl_diff) <= pnl_tolerance else "drift"
    )
    return CloseAuditResult(close, trade, price_diff, pnl_diff, status)


async def fetch_db_closes(
    conn: asyncpg.Connection,
    agent_id: str,
    since: datetime,
    symbols: tuple[str, ...],
    limit: int,
) -> list[DbCloseRow]:
    rows = await conn.fetch(
        """
        SELECT
            p.id AS position_id,
            p.symbol,
            p.quantity,
            p.entry_price,
            p.exit_price,
            p.realized_pnl,
            p.exit_time,
            t.order_id
        FROM positions p
        LEFT JOIN trades t
            ON t.position_id = p.id
            AND t.agent_id = p.agent_id
            AND t.market = p.market
            AND t.side = 'SELL'
        WHERE p.agent_id = $1
            AND p.market = 'futures'
            AND p.status = 'closed'
            AND p.exit_time >= $2
            AND split_part(p.symbol, '::', 2) = ANY($3::text[])
        ORDER BY p.exit_time DESC
        LIMIT $4
        """,
        agent_id,
        since,
        list(symbols),
        limit,
    )
    return [
        DbCloseRow(
            position_id=int(row["position_id"]),
            symbol=str(row["symbol"]),
            quantity=float(row["quantity"]),
            entry_price=float(row["entry_price"]),
            exit_price=float(row["exit_price"]),
            realized_pnl=float(row["realized_pnl"]),
            exit_time=row["exit_time"].astimezone(UTC),
            order_id=str(row["order_id"]) if row["order_id"] else None,
        )
        for row in rows
    ]


async def fetch_binance_trades(
    client: BinanceFuturesClient,
    symbols: tuple[str, ...],
    limit: int,
) -> list[FuturesUserTrade]:
    trades: list[FuturesUserTrade] = []
    for symbol in symbols:
        trades.extend(await client.get_user_trades(symbol, limit=limit))
    return trades


def print_report(results: list[CloseAuditResult]) -> None:
    print("=" * 100)
    print("FUTURES CLOSE AUDIT — read-only")
    print("=" * 100)
    print(f"Rows checked: {len(results)}")
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    for status, count in sorted(status_counts.items()):
        print(f"{status}: {count}")

    print("\nDetails")
    print("-" * 100)
    for result in results:
        close = result.db_close
        trade = result.binance_trade
        if trade is None:
            print(
                f"{result.status} position={close.position_id} {close.symbol} "
                f"db_exit={close.exit_price:.8f} db_pnl={close.realized_pnl:+.8f} "
                f"exit_time={close.exit_time.isoformat()}"
            )
            continue
        print(
            f"{result.status} position={close.position_id} {normalize_symbol(close.symbol)} "
            f"order={trade.order_id} "
            f"db_exit={close.exit_price:.8f} binance_exit={trade.price:.8f} "
            f"price_diff={result.price_diff:+.8f} "
            f"db_pnl={close.realized_pnl:+.8f} binance_pnl={trade.realized_pnl:+.8f} "
            f"pnl_diff={result.pnl_diff:+.8f} "
            f"db_time={close.exit_time.isoformat()} binance_time={trade_datetime(trade).isoformat()}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--since", default="2026-05-01T00:00:00Z")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--binance-limit", type=int, default=1000)
    parser.add_argument("--max-time-delta-minutes", type=float, default=10.0)
    parser.add_argument("--price-tolerance", type=float, default=0.01)
    parser.add_argument("--pnl-tolerance", type=float, default=0.01)
    parser.add_argument("--fail-on-drift", action="store_true")
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    symbols = tuple(str(symbol).upper() for symbol in args.symbols)
    since = parse_utc_date(args.since)

    db_host = os.getenv("POSTGRES_HOST", "localhost")
    db_port = int(os.getenv("POSTGRES_PORT", "5432"))
    db_name = os.getenv("POSTGRES_DB", "marketdata")
    db_user = os.getenv("POSTGRES_USER", "trading")
    db_password = os.getenv("POSTGRES_PASSWORD", "")
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")

    conn = await asyncpg.connect(
        host=db_host,
        port=db_port,
        database=db_name,
        user=db_user,
        password=db_password,
    )
    try:
        closes = await fetch_db_closes(conn, args.agent_id, since, symbols, args.limit)
    finally:
        await conn.close()

    async with BinanceFuturesClient(api_key, api_secret, test_mode=False) as client:
        trades = await fetch_binance_trades(client, symbols, args.binance_limit)

    max_time_delta = timedelta(minutes=args.max_time_delta_minutes)
    results = [
        audit_close(
            close,
            trades,
            max_time_delta,
            args.price_tolerance,
            args.pnl_tolerance,
        )
        for close in closes
    ]
    results.reverse()
    print_report(results)

    has_drift = any(result.status != "ok" for result in results)
    return 1 if args.fail_on_drift and has_drift else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
