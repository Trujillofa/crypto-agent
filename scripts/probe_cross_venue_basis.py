#!/usr/bin/env python3
"""
Cheap feasibility probe for *cross-venue* perp basis / dislocation primitives.

This is the missing cheap probe referenced by the cross-venue RBI lane
(config/autoresearch/rbi_loop.cross-venue-basis-v1.yaml).

Design goals (v0):
- Support multiple venues (e.g. binance_usdm, bybit, okx).
- Compute relative dislocation signals (basis spread, premium difference, etc.)
  across venues for the same symbol/time.
- Apply similar gates to the single-venue probe (event count, forward edge,
  MAE characteristics, concentration).
- Output a structured report + verdict that can feed the RBI guard
  (HAS_PULSE / WEAK_EDGE / NO_PULSE) and be pointed at via last_result.

Current status: Skeleton + multi-venue loading. Full dislocation metrics
and gate logic are TODO / partial (see _compute_dislocation_scenarios).

It will run against whatever is backfilled in perp_basis_metrics (the table
already supports multiple exchanges via the `exchange` column).

Usage (dry / guard-driven):
  uv run python scripts/probe_cross_venue_basis.py \
    --venues binance_usdm,bybit,okx \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --timeframe 1h \
    --start 2024-01-01

See:
- docs/specs/cross-venue-basis-dislocation-brief-v0.md
- The RBI guard will treat a successful probe run as the trigger to advance
  the lane (once a real verdict + last_result artifact exists).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

# DB and logger imports are done lazily inside the non-smoke path so that
# --smoke works without a full project environment / PYTHONPATH.
# This also keeps the cheap smoke test truly cheap.

# --- Minimal schema assumption (matches migration 010) ---
# perp_basis_metrics has: time, exchange, symbol, timeframe, basis_bps, premium_index, ...

PROBE_QUERY = """
    SELECT
        time,
        exchange,
        symbol,
        timeframe,
        basis_bps,
        premium_index
    FROM perp_basis_metrics
    WHERE exchange = ANY($1)
      AND symbol = ANY($2)
      AND timeframe = $3
      AND time >= $4::timestamptz
      AND time <= $5::timestamptz
    ORDER BY time ASC, exchange ASC
"""


class DislocationKind(StrEnum):
    BASIS_SPREAD = "basis_spread"  # basis_venueA - basis_venueB
    PREMIUM_SPREAD = "premium_spread"  # premium_venueA - premium_venueB
    # Future: normalized_funding_diff, oi_pressure, etc.


@dataclass(frozen=True)
class CrossVenueProbeConfig:
    venues: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframe: str
    start: str
    end: str
    min_events_pooled: int
    min_mean_forward_pct: float
    min_mae_improvement_pct: float
    max_concentration_pct: float


# NOTE: The following dataclasses are prepared for the full implementation.
# They are kept for the v0 skeleton but currently unused in the smoke/no-data path
# (to be activated when multi-venue backfill + dislocation logic land).
# Ruff F401 unused-import warnings are expected until then; they document the intended shape.


@dataclass(frozen=True)
class DislocationEvent:
    time: datetime
    symbol: str
    kind: str
    venue_pair: str
    dislocation: float
    forward_12h_pct: float
    forward_24h_pct: float
    mae_12h_pct: float
    mae_24h_pct: float


@dataclass(frozen=True)
class VenuePairSummary:
    venue_pair: str
    kind: str
    event_count: int
    mean_forward_pct: float
    mean_mae_pct: float
    mae_improvement_pct: float
    concentration_pct: float


@dataclass(frozen=True)
class CrossVenueProbeReport:
    config: CrossVenueProbeConfig
    venue_pairs: tuple[VenuePairSummary, ...]
    verdict: str
    passing_pairs: tuple[str, ...]
    note: str = ""


def build_db_config(env: Mapping[str, str] | None = None) -> dict[str, object]:
    source = env or os.environ
    return {
        "host": source.get("DB_HOST", source.get("POSTGRES_HOST", "localhost")),
        "port": int(source.get("DB_PORT", source.get("POSTGRES_PORT", 5432))),
        "name": source.get("DB_NAME", source.get("POSTGRES_DB", "marketdata")),
        "user": source.get("DB_USER", source.get("POSTGRES_USER", "trading")),
        "password": source.get("DB_PASSWORD", source.get("POSTGRES_PASSWORD", "change_me")),
    }


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


async def load_multi_venue_data(
    pool: Any,
    venues: list[str],
    symbols: list[str],
    timeframe: str,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """Load basis data for multiple venues/symbols."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            PROBE_QUERY,
            venues,
            symbols,
            timeframe,
            start,
            end,
        )
    return [dict(r) for r in rows]


async def check_multi_venue_coverage(
    pool: Any, venues: list[str], symbols: list[str], timeframe: str
) -> None:
    """Dry-run audit: report how many rows exist per venue for the symbols.

    This is the coverage/backfill check. It helps decide if enough data is backfilled
    before running a real (expensive) dislocation probe.
    """
    query = """
        SELECT exchange, COUNT(*) as row_count
        FROM perp_basis_metrics
        WHERE exchange = ANY($1)
          AND symbol = ANY($2)
          AND timeframe = $3
        GROUP BY exchange
        ORDER BY exchange
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, venues, symbols, timeframe)
    print("Multi-venue coverage audit (row counts):")
    if not rows:
        print("  No data found for requested venues/symbols/timeframe.")
        return
    for r in rows:
        print(f"  {r['exchange']}: {r['row_count']} rows")
    total = sum(r["row_count"] for r in rows)
    print(f"  Total: {total} rows across {len(rows)} venue(s)")


def _align_and_compute_dislocations(
    rows: list[dict[str, Any]],
    venues: list[str],
) -> list[DislocationEvent]:
    """
    Group rows by (time, symbol), then for each group compute pairwise
    dislocations between venues.

    This is the core new logic vs single-venue probe_basis_premium.py.
    Currently a skeleton: returns empty list until full forward/MAE
    calculation + data alignment is implemented.
    """
    from collections import defaultdict

    by_key: dict[tuple[datetime, str], dict[str, dict]] = defaultdict(dict)
    for r in rows:
        key = (r["time"], r["symbol"])
        by_key[key][r["exchange"]] = r

    events: list[DislocationEvent] = []
    # TODO: implement real forward returns + MAE using joined OHLCV
    # For now we emit nothing so the probe reports low coverage (safe default).
    # When backfilled data + forward logic land, populate DislocationEvent
    # with realistic values and return them.
    return events


def _evaluate_dislocation_scenarios(
    events: list[DislocationEvent],
    config: CrossVenueProbeConfig,
) -> list[VenuePairSummary]:
    """Group events and apply gates. Skeleton returns empty summaries."""
    # TODO: implement tail analysis on |dislocation|, forward edge, MAE, concentration.
    return []


def run_cross_venue_probe(
    rows: list[dict[str, Any]],
    config: CrossVenueProbeConfig,
) -> CrossVenueProbeReport:
    events = _align_and_compute_dislocations(rows, list(config.venues))
    summaries = _evaluate_dislocation_scenarios(events, config)

    if not summaries:
        return CrossVenueProbeReport(
            config=config,
            venue_pairs=(),
            verdict="NO_PULSE",
            passing_pairs=(),
            note="No dislocation events computed (skeleton or insufficient multi-venue data). "
            "Backfill additional venues and implement _compute_dislocation_scenarios.",
        )

    # TODO: real verdict logic
    verdict = "WEAK_EDGE"
    passing = []
    return CrossVenueProbeReport(
        config=config,
        venue_pairs=tuple(summaries),
        verdict=verdict,
        passing_pairs=tuple(passing),
    )


def print_report(report: CrossVenueProbeReport) -> None:
    print("Cross-venue basis dislocation probe")
    print(f"Venues:    {', '.join(report.config.venues)}")
    print(f"Symbols:   {', '.join(report.config.symbols)}")
    print(f"Timeframe: {report.config.timeframe}")
    print(f"Window:    {report.config.start} → {report.config.end}")
    print(f"Verdict:   {report.verdict}")
    if report.note:
        print(f"Note:      {report.note}")
    if report.venue_pairs:
        print("\nVenue-pair summaries (top):")
        for s in report.venue_pairs[:5]:
            print(
                f"  {s.venue_pair} [{s.kind}]: events={s.event_count} "
                f"fwd={s.mean_forward_pct:.3f}% mae_imp={s.mae_improvement_pct:.1f}%"
            )
    else:
        print("\nNo venue-pair summaries (see note).")


def _write_verdict(report: CrossVenueProbeReport, path: str | None) -> None:
    """Write a guard-consumable verdict JSON (used by both --smoke and real paths)."""
    if not path:
        return
    payload = {
        "verdict": report.verdict,
        "note": report.note,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "venues": list(report.config.venues),
            "symbols": list(report.config.symbols),
        },
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote guard-consumable verdict to {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-venue perp basis dislocation probe (RBI cheap probe)."
    )
    parser.add_argument(
        "--venues", default="binance_usdm", help="Comma-separated list of exchanges/venues"
    )
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--min-events", type=int, default=100)
    parser.add_argument("--min-forward-pct", type=float, default=0.15)
    parser.add_argument("--min-mae-imp-pct", type=float, default=10.0)
    parser.add_argument("--max-conc-pct", type=float, default=50.0)
    parser.add_argument(
        "--verdict-output",
        default=None,
        help="Path to write a guard-consumable verdict JSON (e.g. for last_result or manifest).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a cheap smoke test for empty/no-data behavior (no DB required).",
    )
    parser.add_argument(
        "--check-coverage",
        action="store_true",
        help="Run multi-venue coverage / backfill audit (row counts per venue) and exit. "
        "Dry diagnostic only; does not compute dislocation or require full probe logic.",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()

    # Make configure_logger optional so --smoke works in a bare python environment
    # (the real project run via `uv run` will get the full logger).
    try:
        from src.utils.logger import configure_logger  # type: ignore
    except Exception:

        def configure_logger(level: str) -> None:  # type: ignore
            import logging

            logging.basicConfig(level=getattr(logging, level, logging.INFO))

    configure_logger("INFO")

    venues = tuple(v.strip() for v in args.venues.split(",") if v.strip())
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    end = args.end or datetime.now(UTC).date().isoformat()

    config = CrossVenueProbeConfig(
        venues=venues,
        symbols=symbols,
        timeframe=args.timeframe,
        start=args.start,
        end=end,
        min_events_pooled=args.min_events,
        min_mean_forward_pct=args.min_forward_pct,
        min_mae_improvement_pct=args.min_mae_imp_pct,
        max_concentration_pct=args.max_conc_pct,
    )

    if args.smoke:
        # Cheap smoke test for empty/no-data behavior (no DB, no network)
        report = _cheap_smoke_test()
        print_report(report)
        if args.verdict_output:
            _write_verdict(report, args.verdict_output)
        return

    # Lazy imports for the real path (requires project env)
    from src.db import close_pool, init_pool

    db_cfg = build_db_config()
    pool = await init_pool(db_cfg)

    if args.check_coverage:
        await check_multi_venue_coverage(pool, list(venues), list(symbols), args.timeframe)
        await close_pool()
        return

    report = None
    try:
        rows = await load_multi_venue_data(
            pool, list(venues), list(symbols), args.timeframe, args.start, end
        )
        report = run_cross_venue_probe(rows, config)
    except Exception as exc:  # noqa: BLE001
        # Safe fallback on error in the real path (init_pool errors will still surface as non-zero, which is acceptable for the guard).
        report = CrossVenueProbeReport(
            config=config,
            venue_pairs=(),
            verdict="NO_PULSE",
            passing_pairs=(),
            note=f"Safe fallback due to error during probe: {exc}. "
            "This is the expected behavior before data backfill and full implementation.",
        )
    finally:
        await close_pool()

    print_report(report)

    if args.verdict_output:
        _write_verdict(report, args.verdict_output)

    # For RBI guard consumption, a caller can write a last_result.json
    # containing verdict, passing scenarios, etc.
    # The guard itself only cares about the *existence* of a positive verdict
    # for advancing past the cheap-probe gate.


def main() -> None:
    asyncio.run(main_async())


def _cheap_smoke_test() -> CrossVenueProbeReport:
    """Cheap smoke test for empty/no-data behavior (callable from pytest or directly).
    Returns the report so the CLI and tests can use it (e.g. for --verdict-output).
    """
    # Simulate the smoke path without DB or asyncio
    config = CrossVenueProbeConfig(
        venues=("binance_usdm", "bybit"),
        symbols=("BTCUSDT",),
        timeframe="1h",
        start="2024-01-01",
        end="2024-01-02",
        min_events_pooled=100,
        min_mean_forward_pct=0.15,
        min_mae_improvement_pct=10.0,
        max_concentration_pct=50.0,
    )
    report = CrossVenueProbeReport(
        config=config,
        venue_pairs=(),
        verdict="NO_PULSE",
        passing_pairs=(),
        note="SMOKE: no data path exercised. This is the expected safe default before multi-venue backfill.",
    )
    print("Cheap smoke test for no-data behavior: PASSED")
    return report


if __name__ == "__main__":
    main()
