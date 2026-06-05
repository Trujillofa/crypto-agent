#!/usr/bin/env python3
"""Audit perp_basis_metrics coverage vs ohlcv before basis/premium probes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_funding_normalization import build_db_config  # noqa: E402
from src.db import close_pool, get_pool, init_pool  # noqa: E402

TABLE_NAME = "perp_basis_metrics"
MIN_COVERAGE_RATIO = 0.95
MAX_START_LAG = timedelta(days=7)


def bar_duration(timeframe: str) -> timedelta:
    """Bar duration for gap and end-lag checks."""
    mapping = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }
    if timeframe not in mapping:
        raise ValueError(f"Unsupported timeframe for audit: {timeframe}")
    return mapping[timeframe]


def has_valid_overlap(
    ohlcv_min: datetime | None,
    ohlcv_max: datetime | None,
    basis_min: datetime | None,
    basis_max: datetime | None,
) -> bool:
    """True when basis and ohlcv time ranges share at least one instant."""
    if ohlcv_min is None or ohlcv_max is None or basis_min is None or basis_max is None:
        return False
    return max(ohlcv_min, basis_min) <= min(ohlcv_max, basis_max)


def overlap_bounds(
    ohlcv_min: datetime,
    ohlcv_max: datetime,
    basis_min: datetime,
    basis_max: datetime,
) -> tuple[datetime, datetime]:
    """Inclusive overlap window; caller must ensure has_valid_overlap is True."""
    return max(ohlcv_min, basis_min), min(ohlcv_max, basis_max)


@dataclass(frozen=True)
class SymbolAudit:
    symbol: str
    ready: bool
    reasons: tuple[str, ...]
    ohlcv_bars: int
    basis_bars: int
    coverage_ratio: float
    ohlcv_range: str
    basis_range: str
    max_gap_hours: float
    funding_rows: int
    funding_range: str


async def _table_exists(conn) -> bool:
    row = await conn.fetchrow(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = $1
        ) AS ok
        """,
        TABLE_NAME,
    )
    return bool(row and row["ok"])


async def _max_gap_hours(
    conn,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    start,
    end,
) -> float:
    row = await conn.fetchrow(
        f"""
        WITH ordered AS (
            SELECT time,
                   time - LAG(time) OVER (ORDER BY time) AS delta
            FROM {TABLE_NAME}
            WHERE exchange = $1
              AND symbol = $2
              AND timeframe = $3
              AND time >= $4
              AND time <= $5
        )
        SELECT EXTRACT(EPOCH FROM MAX(delta)) / 3600.0 AS max_gap_hours
        FROM ordered
        WHERE delta IS NOT NULL
        """,
        exchange,
        symbol,
        timeframe,
        start,
        end,
    )
    if row is None or row["max_gap_hours"] is None:
        return 0.0
    return float(row["max_gap_hours"])


async def audit_symbol(
    conn,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> SymbolAudit:
    reasons: list[str] = []

    ohlcv = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n,
               MIN(time) AS min_time,
               MAX(time) AS max_time
        FROM ohlcv
        WHERE symbol = $1 AND timeframe = $2
        """,
        symbol,
        timeframe,
    )
    ohlcv_bars = int(ohlcv["n"] or 0)
    ohlcv_min = ohlcv["min_time"]
    ohlcv_max = ohlcv["max_time"]
    ohlcv_range = f"{ohlcv_min} .. {ohlcv_max}"

    basis = await conn.fetchrow(
        f"""
        SELECT COUNT(*) AS n,
               MIN(time) AS min_time,
               MAX(time) AS max_time
        FROM {TABLE_NAME}
        WHERE exchange = $1 AND symbol = $2 AND timeframe = $3
        """,
        exchange,
        symbol,
        timeframe,
    )
    basis_bars = int(basis["n"] or 0)
    basis_min = basis["min_time"]
    basis_max = basis["max_time"]
    basis_range = f"{basis_min} .. {basis_max}"

    funding = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n,
               MIN(funding_time) AS min_time,
               MAX(funding_time) AS max_time
        FROM funding_rates
        WHERE symbol = $1
        """,
        symbol,
    )
    funding_rows = int(funding["n"] or 0)
    funding_range = f"{funding['min_time']} .. {funding['max_time']}"

    if ohlcv_bars == 0:
        reasons.append("no ohlcv bars for symbol/timeframe")
        return SymbolAudit(
            symbol=symbol,
            ready=False,
            reasons=tuple(reasons),
            ohlcv_bars=0,
            basis_bars=basis_bars,
            coverage_ratio=0.0,
            ohlcv_range=ohlcv_range,
            basis_range=basis_range,
            max_gap_hours=0.0,
            funding_rows=funding_rows,
            funding_range=funding_range,
        )

    if basis_bars == 0:
        reasons.append("no perp_basis_metrics rows (backfill required)")

    overlap_ohlcv = 0
    overlap_basis = 0
    max_gap_hours = 0.0

    valid_overlap = has_valid_overlap(ohlcv_min, ohlcv_max, basis_min, basis_max)
    if basis_bars > 0 and not valid_overlap:
        reasons.append("no overlap between basis and ohlcv ranges")
    elif basis_bars > 0 and valid_overlap:
        overlap_start, overlap_end = overlap_bounds(ohlcv_min, ohlcv_max, basis_min, basis_max)
        overlap_ohlcv = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM ohlcv
                WHERE symbol = $1 AND timeframe = $2
                  AND time >= $3 AND time <= $4
                """,
                symbol,
                timeframe,
                overlap_start,
                overlap_end,
            )
            or 0
        )
        overlap_basis = int(
            await conn.fetchval(
                f"""
                SELECT COUNT(*)
                FROM {TABLE_NAME}
                WHERE exchange = $1 AND symbol = $2 AND timeframe = $3
                  AND time >= $4 AND time <= $5
                """,
                exchange,
                symbol,
                timeframe,
                overlap_start,
                overlap_end,
            )
            or 0
        )
        max_gap_hours = await _max_gap_hours(
            conn,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            start=overlap_start,
            end=overlap_end,
        )

        if basis_min and ohlcv_min and basis_min > ohlcv_min + MAX_START_LAG:
            reasons.append(f"basis starts {basis_min} > 7d after ohlcv {ohlcv_min}")

        bar_delta = bar_duration(timeframe)
        bar_hours = bar_delta.total_seconds() / 3600.0
        if max_gap_hours > 2.0 * bar_hours:
            reasons.append(f"max gap {max_gap_hours:.1f}h > {2.0 * bar_hours:.1f}h")

        if basis_max and ohlcv_max:
            min_basis_end = ohlcv_max - 2 * bar_delta
            if basis_max < min_basis_end:
                reasons.append(
                    f"basis ends {basis_max} before ohlcv {ohlcv_max} (need >= {min_basis_end})"
                )

    coverage_ratio = overlap_basis / overlap_ohlcv if overlap_ohlcv else 0.0
    if valid_overlap and overlap_ohlcv == 0:
        reasons.append("no ohlcv bars in overlap window")
    if overlap_ohlcv and coverage_ratio < MIN_COVERAGE_RATIO:
        reasons.append(
            f"coverage {coverage_ratio:.1%} < {MIN_COVERAGE_RATIO:.0%} "
            f"({overlap_basis}/{overlap_ohlcv} bars in overlap)"
        )

    ready = basis_bars > 0 and not reasons
    return SymbolAudit(
        symbol=symbol,
        ready=ready,
        reasons=tuple(reasons),
        ohlcv_bars=ohlcv_bars,
        basis_bars=basis_bars,
        coverage_ratio=coverage_ratio,
        ohlcv_range=ohlcv_range,
        basis_range=basis_range,
        max_gap_hours=max_gap_hours,
        funding_rows=funding_rows,
        funding_range=funding_range,
    )


async def main(
    symbols: list[str],
    *,
    exchange: str,
    timeframe: str,
) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        if not await _table_exists(conn):
            print(f"FAIL: table {TABLE_NAME} missing — run migration 010")
            return 1

        print(f"Basis/premium coverage audit (exchange={exchange}, timeframe={timeframe})")
        print("=" * 72)

        audits: list[SymbolAudit] = []
        for symbol in symbols:
            audit = await audit_symbol(
                conn,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
            )
            audits.append(audit)
            status = "PROBE_READY" if audit.ready else "NOT_READY"
            print(f"\n{symbol}: {status}")
            print(f"  ohlcv:   {audit.ohlcv_bars} bars  {audit.ohlcv_range}")
            print(f"  basis:   {audit.basis_bars} bars  {audit.basis_range}")
            print(f"  overlap: {audit.coverage_ratio:.1%} (max gap {audit.max_gap_hours:.1f}h)")
            print(f"  funding: {audit.funding_rows} rows  {audit.funding_range}")
            if audit.reasons:
                for reason in audit.reasons:
                    print(f"  - {reason}")

        all_ready = all(a.ready for a in audits)
        print()
        if all_ready:
            print("Verdict: PROBE_READY — ok to run probe_basis_premium (Phase 3)")
            return 0
        print("Verdict: NOT_READY — run migration 010 + backfill before any probe")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit perp basis/premium DB coverage.")
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="Comma-separated symbols",
    )
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--exchange", default="binance_usdm")
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    async def _run() -> int:
        await init_pool(build_db_config())
        try:
            return await main(symbols, exchange=args.exchange, timeframe=args.timeframe)
        finally:
            await close_pool()

    raise SystemExit(asyncio.run(_run()))
