#!/usr/bin/env python3
"""Run funding-normalization probe scenarios (reshape study).

Compares fixed thresholds, per-symbol quantile tails, and short-side normalization
(probe-only). Does not implement the full surface.

See docs/reports/candidate-search-options-2026-06-04.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_funding_normalization import (
    ProbeConfig,
    _fetch_funding_ticks,
    build_db_config,
    entry_threshold_negative_tail,
    evaluate_pulse,
    probe_funding_series,
)
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class Scenario:
    name: str
    entry_threshold: float | None
    negative_tail_pct: float | None
    positive_tail_pct: float | None
    long_only: bool


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    symbol: str
    entry_used: float
    long_events: int
    short_events: int
    mean_net_12h: float
    mean_net_24h: float
    verdict_long: str


DEFAULT_SCENARIOS: tuple[Scenario, ...] = (
    Scenario("fixed_0.05pct", 0.0005, None, None, True),
    Scenario("fixed_0.015pct", 0.00015, None, None, True),
    Scenario("neg_tail_5pct", None, 5.0, None, True),
    Scenario("neg_tail_10pct", None, 10.0, None, True),
    Scenario("both_norm_probe", None, 5.0, 5.0, False),
)


def resolve_entry_threshold(
    funding_rates: Sequence[float],
    scenario: Scenario,
    fallback: float = 0.0005,
) -> float:
    if scenario.entry_threshold is not None:
        return scenario.entry_threshold
    if scenario.negative_tail_pct is not None:
        return entry_threshold_negative_tail(funding_rates, scenario.negative_tail_pct)
    return fallback


async def run_scenario(
    symbol: str,
    scenario: Scenario,
    base: ProbeConfig,
    funding_ticks: list,
    price_rows: list,
) -> ScenarioResult:
    rates = [tick.funding_rate for tick in funding_ticks]
    entry = resolve_entry_threshold(rates, scenario)
    config = ProbeConfig(
        symbol=symbol,
        timeframe=base.timeframe,
        start=base.start,
        end=base.end,
        entry_threshold=entry,
        exit_threshold=base.exit_threshold,
        forward_bars_12h=base.forward_bars_12h,
        forward_bars_24h=base.forward_bars_24h,
        min_events_for_pulse=base.min_events_for_pulse,
        max_profit_concentration_pct=base.max_profit_concentration_pct,
        long_only=scenario.long_only,
    )
    summary = probe_funding_series(funding_ticks, price_rows, config)
    short_count = len(summary.events) - len(summary.long_events)
    stats_12 = summary.stats_for("12h")
    stats_24 = summary.stats_for("24h")
    return ScenarioResult(
        scenario=scenario.name,
        symbol=symbol,
        entry_used=entry,
        long_events=len(summary.long_events),
        short_events=short_count,
        mean_net_12h=stats_12.mean_net_pct,
        mean_net_24h=stats_24.mean_net_pct,
        verdict_long=evaluate_pulse(summary, config),
    )


async def run_reshape(symbols: Sequence[str], base: ProbeConfig) -> list[ScenarioResult]:
    db_config = build_db_config()
    await init_pool(db_config)
    results: list[ScenarioResult] = []
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            for symbol in symbols:
                funding_ticks = await _fetch_funding_ticks(symbol, base.start, base.end)
                price_rows = await reader.fetch_range(
                    symbol,
                    base.timeframe,
                    base.start,
                    base.end,
                )
                for scenario in DEFAULT_SCENARIOS:
                    results.append(
                        await run_scenario(symbol, scenario, base, funding_ticks, price_rows)
                    )
    finally:
        await close_pool()
    return results


def print_results(results: Sequence[ScenarioResult]) -> None:
    print("Funding probe reshape matrix")
    print("=" * 88)
    print(
        f"{'scenario':<20} {'symbol':<10} {'entry':>9} {'long':>5} {'short':>5} "
        f"{'net12h':>8} {'net24h':>8} {'verdict':<12}"
    )
    print("-" * 88)
    for row in results:
        print(
            f"{row.scenario:<20} {row.symbol:<10} {row.entry_used:>9.5f} "
            f"{row.long_events:>5} {row.short_events:>5} {row.mean_net_12h:>7.2f}% "
            f"{row.mean_net_24h:>7.2f}% {row.verdict_long:<12}"
        )
    print()
    pulses = [r for r in results if r.verdict_long == "HAS_PULSE"]
    if pulses:
        print("HAS_PULSE scenarios:")
        for row in pulses:
            print(f"  {row.scenario} {row.symbol} entry={row.entry_used:.5f}")
    else:
        print("No HAS_PULSE scenario in this matrix — pick another surface or redesign trigger.")


async def _main() -> int:
    configure_logger("WARNING")
    parser = argparse.ArgumentParser(description="Funding normalization reshape sweep")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--min-events", type=int, default=20)
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    base = ProbeConfig(
        symbol=symbols[0],
        timeframe="1h",
        start=args.start,
        end=args.end,
        entry_threshold=0.0005,
        exit_threshold=0.00015,
        forward_bars_12h=12,
        forward_bars_24h=24,
        min_events_for_pulse=args.min_events,
        max_profit_concentration_pct=30.0,
        long_only=True,
    )
    results = await run_reshape(symbols, base)
    print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
