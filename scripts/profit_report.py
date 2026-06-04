#!/usr/bin/env python3
"""Generate a P&L snapshot from the trading database.

Usage:
    python scripts/profit_report.py                     # text output to stdout
    python scripts/profit_report.py --output docs/reports/pnl-$(date +%Y%m%d).txt
    python scripts/profit_report.py --format json        # machine-readable
    python scripts/profit_report.py --quiet              # no connecting message
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import asyncpg


async def _fetch_overall(pool: asyncpg.Pool) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS total_closed,
            COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
            COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses,
            COALESCE(SUM(realized_pnl), 0) AS total_realized_pnl,
            AVG(realized_pnl) FILTER (WHERE realized_pnl > 0) AS avg_win,
            AVG(realized_pnl) FILTER (WHERE realized_pnl < 0) AS avg_loss
        FROM positions
        WHERE status = 'closed'
        """
    )
    if row["total_closed"] == 0:
        return None
    return {
        "total_trades": row["total_closed"],
        "wins": row["wins"],
        "losses": row["losses"],
        "win_rate": round(row["wins"] / row["total_closed"] * 100, 2),
        "total_pnl": round(float(row["total_realized_pnl"]), 2),
        "avg_win": round(float(row["avg_win"]), 2) if row["avg_win"] else 0.0,
        "avg_loss": round(float(row["avg_loss"]), 2) if row["avg_loss"] else 0.0,
        "win_loss_ratio": round(float(row["avg_win"]) / abs(float(row["avg_loss"])), 2)
        if row["avg_win"] and row["avg_loss"]
        else 0.0,
    }


async def _fetch_by_agent(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT
            agent_id,
            COUNT(*) AS trades,
            COALESCE(SUM(realized_pnl), 0) AS pnl,
            COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
            COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses,
            ROUND(AVG(realized_pnl) FILTER (WHERE realized_pnl > 0), 2) AS avg_win,
            ROUND(AVG(realized_pnl) FILTER (WHERE realized_pnl < 0), 2) AS avg_loss,
            ROUND(
                COUNT(*) FILTER (WHERE realized_pnl > 0)::numeric /
                NULLIF(COUNT(*), 0) * 100, 2
            ) AS win_rate_pct
        FROM positions
        WHERE status = 'closed'
        GROUP BY agent_id
        ORDER BY pnl DESC
        """
    )
    return [
        {
            "agent_id": r["agent_id"],
            "trades": r["trades"],
            "pnl": round(float(r["pnl"]), 2),
            "wins": r["wins"],
            "losses": r["losses"],
            "win_rate": float(r["win_rate_pct"]) if r["win_rate_pct"] else 0.0,
            "avg_win": float(r["avg_win"]) if r["avg_win"] else 0.0,
            "avg_loss": float(r["avg_loss"]) if r["avg_loss"] else 0.0,
        }
        for r in rows
    ]


async def _fetch_by_symbol(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT
            symbol,
            COUNT(*) AS trades,
            COALESCE(SUM(realized_pnl), 0) AS pnl,
            COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
            COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses
        FROM positions
        WHERE status = 'closed'
        GROUP BY symbol
        ORDER BY pnl DESC
        """
    )
    return [
        {
            "symbol": r["symbol"],
            "trades": r["trades"],
            "pnl": round(float(r["pnl"]), 2),
            "wins": r["wins"],
            "losses": r["losses"],
        }
        for r in rows
    ]


def _format_text(
    overall: dict | None, by_agent: list[dict], by_symbol: list[dict], quiet: bool
) -> str:
    lines = []
    if not quiet:
        lines.append(f"Generated: {datetime.now().isoformat()}")

    if overall is None:
        lines.append("No closed positions found.")
        return "\n".join(lines)

    lines.append("[OVERALL]")
    lines.append(
        f"  Total trades: {overall['total_trades']} | "
        f"Wins: {overall['wins']} | Losses: {overall['losses']} | "
        f"Win rate: {overall['win_rate']}%"
    )
    lines.append(
        f"  Total PnL: ${overall['total_pnl']:+.2f} | "
        f"Avg win: ${overall['avg_win']:+.2f} | Avg loss: ${overall['avg_loss']:+.2f} | "
        f"W/L ratio: {overall['win_loss_ratio']:.2f}"
    )

    lines.append("\n[BY AGENT]")
    for a in by_agent:
        lines.append(
            f"  {a['agent_id']:<30} | "
            f"trades={a['trades']:>3} | "
            f"pnl=${a['pnl']:>+8.2f} | "
            f"wr={a['win_rate']:>5.1f}% | "
            f"avg_win=${a['avg_win']:>+7.2f} | "
            f"avg_loss=${a['avg_loss']:>+7.2f}"
        )

    lines.append("\n[BY SYMBOL]")
    for s in by_symbol:
        lines.append(
            f"  {s['symbol']:<12} | trades={s['trades']:>3} | "
            f"pnl=${s['pnl']:>+8.2f} | {s['wins']}/{s['losses']}"
        )

    return "\n".join(lines)


async def generate_report(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    output_path: Path | None,
    fmt: str,
    quiet: bool,
) -> None:
    if not quiet:
        print(f"Connecting to {database} on {host}:{port}...", file=sys.stderr)

    pool = await asyncpg.create_pool(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        min_size=1,
        max_size=4,
    )
    try:
        async with pool.acquire() as conn:
            overall = await _fetch_overall(conn)
            by_agent = await _fetch_by_agent(conn)
            by_symbol = await _fetch_by_symbol(conn)
    finally:
        await pool.close()

    if fmt == "json":
        data = {
            "generated_at": datetime.now().isoformat(),
            "overall": overall,
            "by_agent": by_agent,
            "by_symbol": by_symbol,
        }
        output = json.dumps(data, indent=2)
    else:
        output = _format_text(overall, by_agent, by_symbol, quiet)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        if not quiet:
            print(f"Wrote: {output_path}", file=sys.stderr)
    else:
        print(output)


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="P&L snapshot from trading database")
    p.add_argument("--host", default=_env("POSTGRES_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(_env("POSTGRES_PORT", "5432")))
    p.add_argument("--db", default=_env("POSTGRES_DB", "marketdata"))
    p.add_argument("--user", default=_env("POSTGRES_USER", "trading"))
    p.add_argument("--password", default=_env("POSTGRES_PASSWORD", ""))
    p.add_argument("--output", type=Path, default=None, help="Write output to file")
    p.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format (default: text)"
    )
    p.add_argument(
        "--quiet", action="store_true", help="Suppress connecting message and write only data"
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(
            generate_report(
                host=args.host,
                port=args.port,
                database=args.db,
                user=args.user,
                password=args.password,
                output_path=args.output,
                fmt=args.format,
                quiet=args.quiet,
            )
        )
    except KeyboardInterrupt:
        pass
