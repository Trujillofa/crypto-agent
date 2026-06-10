#!/usr/bin/env python3
"""
Cheap feasibility probe for *cross-venue* perp basis / dislocation primitives.

This is the cheap probe referenced by the cross-venue RBI lane
(config/autoresearch/rbi_loop.cross-venue-basis-v1.yaml).

Methodology (v1): for each (symbol, venue pair), build a *spread series*
(basis_bps and premium_index differences between the two venues) joined with
OHLCV close/low at the same timestamps, then run the exact single-venue
machinery from scripts/probe_basis_premium.py on it:

- events = tail-percentile extremes of the spread (both sides) + normalization
- edge   = forward *price* returns at 12/24 bars and MAE vs baseline MAE
- gates  = min event counts, mean forward, MAE improvement, concentration
- verdict = HAS_PULSE / WEAK_EDGE / SPARSE / NO_PULSE (same semantics)

Measuring forward price returns (not spread self-convergence) is deliberate:
cross-venue spreads on the same underlying always mean-revert, so spread
convergence alone is a tautology, not an edge.

Usage (dry / guard-driven):
  uv run python scripts/probe_cross_venue_basis.py \
    --venues binance_usdm,bybit \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --timeframe 1h \
    --start 2024-01-01 \
    --verdict-output research/rbi_loop/cross-venue-basis-v1/probe-verdict.json

See docs/specs/cross-venue-basis-dislocation-brief-v0.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Heavy imports (src.db, scripts.probe_basis_premium -> asyncpg) are done lazily
# so that --smoke works without a full project environment.

PROBE_QUERY = """
    SELECT
        p.time,
        p.exchange,
        p.basis_bps,
        p.premium_index,
        o.close_price,
        o.low_price
    FROM perp_basis_metrics p
    INNER JOIN ohlcv o
        ON p.time = o.time
        AND p.symbol = o.symbol
        AND p.timeframe = o.timeframe
    WHERE p.exchange = ANY($1)
      AND p.symbol = $2
      AND p.timeframe = $3
      AND p.time >= $4
      AND p.time <= $5
    ORDER BY p.time ASC
"""


@dataclass(frozen=True)
class CrossVenueProbeConfig:
    venues: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframe: str
    start: str
    end: str
    tail_pcts: tuple[int, ...]
    forward_bars_12h: int
    forward_bars_24h: int
    min_events_per_pair: int
    min_events_pooled: int
    min_mean_forward_pct: float
    min_mae_improvement_pct: float
    max_concentration_pct: float


@dataclass(frozen=True)
class SmokeReport:
    verdict: str
    note: str


def _cheap_smoke_test() -> SmokeReport:
    """No-DB, no-network safe default used by --smoke and tests."""
    return SmokeReport(
        verdict="NO_PULSE",
        note=(
            "SMOKE: no data path exercised. "
            "This is the expected safe default before multi-venue backfill."
        ),
    )


def build_pair_spread_bars(
    rows: list[dict[str, Any]],
    venues: tuple[str, ...],
) -> dict[str, list[Any]]:
    """Turn per-venue rows for one symbol into per-venue-pair spread bars.

    Returns {"venueA-venueB": [PremiumBar, ...]} where the bar's basis_bps and
    premium_index hold the *spread* (A minus B) and close/low come from the
    joined OHLCV (identical across venues at the same time/symbol/timeframe).
    """
    from scripts.probe_basis_premium import PremiumBar

    by_time: dict[datetime, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_time.setdefault(row["time"], {})[row["exchange"]] = row

    pairs = [(venues[i], venues[j]) for i in range(len(venues)) for j in range(i + 1, len(venues))]
    out: dict[str, list[Any]] = {f"{a}-{b}": [] for a, b in pairs}
    for t in sorted(by_time):
        venue_rows = by_time[t]
        for a, b in pairs:
            row_a = venue_rows.get(a)
            row_b = venue_rows.get(b)
            if row_a is None or row_b is None:
                continue
            out[f"{a}-{b}"].append(
                PremiumBar(
                    time=t,
                    basis_bps=float(row_a["basis_bps"]) - float(row_b["basis_bps"]),
                    premium_index=float(row_a["premium_index"]) - float(row_b["premium_index"]),
                    close_price=float(row_a["close_price"]),
                    low_price=float(row_a["low_price"]),
                )
            )
    return out


def analyze_cross_venue(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    config: CrossVenueProbeConfig,
) -> Any:
    """Run the single-venue probe machinery on per-pair spread series.

    Returns a scripts.probe_basis_premium.ProbeReport whose "symbols" are
    "<symbol>|<venueA>-<venueB>" entries; metric labels basis_bps /
    premium_index refer to the cross-venue *spreads* of those metrics.
    """
    from scripts.probe_basis_premium import (
        ProbeConfig,
        ProbeReport,
        evaluate_report,
        probe_symbol,
    )

    spb_config = ProbeConfig(
        symbols=config.symbols,
        timeframe=config.timeframe,
        exchange="+".join(config.venues),
        start=config.start,
        end=config.end,
        tail_pcts=config.tail_pcts,
        forward_bars_12h=config.forward_bars_12h,
        forward_bars_24h=config.forward_bars_24h,
        min_events_per_symbol=config.min_events_per_pair,
        min_events_pooled=config.min_events_pooled,
        min_mean_forward_pct=config.min_mean_forward_pct,
        min_mae_improvement_pct=config.min_mae_improvement_pct,
        max_concentration_pct=config.max_concentration_pct,
    )

    summaries = []
    for symbol, rows in rows_by_symbol.items():
        for pair_label, bars in build_pair_spread_bars(rows, config.venues).items():
            summaries.append(probe_symbol(bars, f"{symbol}|{pair_label}", spb_config))

    preliminary = ProbeReport(
        config=spb_config, symbols=tuple(summaries), verdict="", passing_scenarios=()
    )
    verdict, passing = evaluate_report(preliminary)
    return ProbeReport(
        config=spb_config,
        symbols=tuple(summaries),
        verdict=verdict,
        passing_scenarios=passing,
    )


def print_cross_venue_report(report: Any, config: CrossVenueProbeConfig) -> None:
    print("Cross-venue basis dislocation probe")
    print(f"Venues:    {', '.join(config.venues)}")
    print(f"Symbols:   {', '.join(config.symbols)}")
    print(f"Timeframe: {config.timeframe}")
    print(f"Window:    {config.start} -> {config.end}")
    for summary in report.symbols:
        event_total = sum(len(s.events) for s in summary.scenarios)
        print(f"  {summary.symbol}: bars={summary.bar_count} events_total={event_total}")
    print(f"Verdict:   {report.verdict}")
    if report.passing_scenarios:
        print("Passing scenarios (metric = cross-venue spread):")
        for label in report.passing_scenarios:
            print(f"  {label}")


def _write_verdict(
    verdict: str,
    note: str,
    passing: tuple[str, ...],
    config: CrossVenueProbeConfig,
    path: str | None,
) -> None:
    if not path:
        return
    payload = {
        "verdict": verdict,
        "note": note,
        "passing_scenarios": list(passing),
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "venues": list(config.venues),
            "symbols": list(config.symbols),
            "timeframe": config.timeframe,
            "start": config.start,
            "end": config.end,
        },
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote guard-consumable verdict to {path}")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-venue perp basis dislocation probe (RBI cheap probe)."
    )
    parser.add_argument("--venues", default="binance_usdm,bybit")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    # Gate defaults mirror scripts/probe_basis_premium.py.
    parser.add_argument("--tail-pcts", default="5,10")
    parser.add_argument("--min-events-per-pair", type=int, default=30)
    parser.add_argument("--min-events-pooled", type=int, default=100)
    parser.add_argument("--min-forward-pct", type=float, default=0.15)
    parser.add_argument("--min-mae-imp-pct", type=float, default=10.0)
    parser.add_argument("--max-conc-pct", type=float, default=50.0)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="No-DB safe default path (always NO_PULSE); used by tests",
    )
    parser.add_argument(
        "--check-coverage",
        action="store_true",
        help="Only audit per-venue row counts, then exit (no probe, no verdict)",
    )
    parser.add_argument(
        "--verdict-output",
        default=None,
        help="Write guard-consumable verdict JSON to this path",
    )
    return parser.parse_args()


def _config_from_args(args: argparse.Namespace) -> CrossVenueProbeConfig:
    venues = tuple(v.strip() for v in args.venues.split(",") if v.strip())
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    tail_pcts = tuple(int(p) for p in args.tail_pcts.split(",") if p.strip())
    end = args.end or datetime.now(UTC).date().isoformat()
    return CrossVenueProbeConfig(
        venues=venues,
        symbols=symbols,
        timeframe=args.timeframe,
        start=args.start,
        end=end,
        tail_pcts=tail_pcts,
        forward_bars_12h=12,
        forward_bars_24h=24,
        min_events_per_pair=args.min_events_per_pair,
        min_events_pooled=args.min_events_pooled,
        min_mean_forward_pct=args.min_forward_pct,
        min_mae_improvement_pct=args.min_mae_imp_pct,
        max_concentration_pct=args.max_conc_pct,
    )


async def main_async() -> None:
    args = parse_args()
    config = _config_from_args(args)

    if args.smoke:
        report = _cheap_smoke_test()
        print(f"Verdict:   {report.verdict}")
        print(f"Note:      {report.note}")
        _write_verdict(report.verdict, report.note, (), config, args.verdict_output)
        return

    from scripts.probe_basis_premium import build_db_config
    from src.db import close_pool, init_pool
    from src.utils.logger import configure_logger

    configure_logger("WARNING")
    pool = await init_pool(build_db_config())
    try:
        if args.check_coverage:
            await check_multi_venue_coverage(
                pool, list(config.venues), list(config.symbols), config.timeframe
            )
            return

        start = datetime.fromisoformat(config.start).replace(tzinfo=UTC)
        end = datetime.fromisoformat(config.end).replace(tzinfo=UTC)
        rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for symbol in config.symbols:
            fetched = await pool.fetch(
                PROBE_QUERY, list(config.venues), symbol, config.timeframe, start, end
            )
            rows_by_symbol[symbol] = [dict(r) for r in fetched]
    finally:
        await close_pool()

    report = analyze_cross_venue(rows_by_symbol, config)
    print_cross_venue_report(report, config)
    note = "Forward price-return/MAE gates on cross-venue spread extremes (see brief)."
    _write_verdict(report.verdict, note, report.passing_scenarios, config, args.verdict_output)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
