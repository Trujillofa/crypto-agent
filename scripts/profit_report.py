#!/usr/bin/env python3
"""Generate a comprehensive profit report from the trading database."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import asyncpg

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

async def generate_report() -> None:
    """Connect to database and generate report."""
    
    # Database config
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    database = os.getenv("POSTGRES_DB", "marketdata")
    user = os.getenv("POSTGRES_USER", "trading")
    password = os.getenv("POSTGRES_PASSWORD", "")

    print(f"Connecting to {database} on {host}:{port}...")

    conn = None
    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        return

    try:
        # Comprehensive Profit Report Query
        query = """
        WITH closed_stats AS (
          SELECT 
            COUNT(*) as total_closed,
            COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
            COUNT(*) FILTER (WHERE realized_pnl < 0) as losses,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            AVG(realized_pnl) FILTER (WHERE realized_pnl > 0) as avg_win,
            AVG(realized_pnl) FILTER (WHERE realized_pnl < 0) as avg_loss
          FROM positions 
          WHERE status = 'closed'
        ),
        symbol_stats AS (
          SELECT 
            symbol,
            COUNT(*) as trades,
            COALESCE(SUM(realized_pnl), 0) as pnl
          FROM positions 
          WHERE status = 'closed'
          GROUP BY symbol
          ORDER BY pnl DESC
        )
        SELECT 
          cs.total_closed,
          cs.wins,
          cs.losses,
          ROUND(cs.wins::numeric / NULLIF(cs.total_closed, 0) * 100, 2) as win_rate_pct,
          ROUND(cs.total_realized_pnl::numeric, 2) as total_pnl,
          ROUND(cs.avg_win::numeric, 2) as avg_win,
          ROUND(ABS(cs.avg_loss)::numeric, 2) as avg_loss,
          ROUND((COALESCE(cs.avg_win, 0) / NULLIF(ABS(cs.avg_loss), 0))::numeric, 2) as win_loss_ratio
        FROM closed_stats cs;
        """

        row = await conn.fetchrow(query)

        if not row:
            print("No closed positions found.")
        else:
            print("\n💰 TRADING PERFORMANCE REPORT")
            print("=" * 40)
            print(f"{'Metric':<25} | {'Value':>12}")
            print("-" * 40)
            
            print(f"{'Total Closed Trades':<25} | {row['total_closed']:>12}")
            print(f"{'Wins':<25} | {row['wins']:>12}")
            print(f"{'Losses':<25} | {row['losses']:>12}")
            print(f"{'Win Rate':<25} | {row['win_rate_pct'] or 0:>11}%")
            print(f"{'Total Realized PnL':<25} | ${row['total_pnl'] or 0:>11.2f}")
            print(f"{'Avg Win':<25} | ${row['avg_win'] or 0:>11.2f}")
            print(f"{'Avg Loss':<25} | ${row['avg_loss'] or 0:>11.2f}")
            print(f"{'Win/Loss Ratio':<25} | {row['win_loss_ratio'] or 0:>12}")
            print("=" * 40)

        # Symbol Breakdown
        print("\n📊 PERFORMANCE BY SYMBOL")
        symbol_query = """
        SELECT 
            symbol,
            COUNT(*) as trades,
            COALESCE(SUM(realized_pnl), 0) as pnl,
            COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
            COUNT(*) FILTER (WHERE realized_pnl < 0) as losses
        FROM positions 
        WHERE status = 'closed'
        GROUP BY symbol
        ORDER BY pnl DESC
        """
        
        symbol_rows = await conn.fetch(symbol_query)
        
        if not symbol_rows:
             print("No symbol data available.")
        else:
            print("-" * 60)
            print(f"{'Symbol':<15} | {'Trades':>8} | {'PnL':>12} | {'Win/Loss':>10}")
            print("-" * 60)
            for r in symbol_rows:
                print(f"{r['symbol']:<15} | {r['trades']:>8} | ${r['pnl']:>11.2f} | {r['wins']:>4}/{r['losses']:<4}")
            print("-" * 60)

    finally:
        if conn:
            await conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(generate_report())
    except KeyboardInterrupt:
        pass
