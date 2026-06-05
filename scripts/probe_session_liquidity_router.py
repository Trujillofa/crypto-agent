#!/usr/bin/env python3
"""Cheap feasibility probe for session / liquidity window routing (Option G).

Stratifies 1h bars by UTC session and compares forward return + adverse excursion
vs an all-hours baseline. No strategy code until HAS_PULSE.

See docs/specs/session-liquidity-router-surface-v0.md
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from src.db import close_pool, get_pool, init_pool
from src.utils.logger import configure_logger

# Disjoint UTC windows [start_hour, end_hour) — see surface v0 spec.
DEFAULT_WINDOWS: dict[str, tuple[int, int]] = {
    "asia": (0, 8),
    "europe": (8, 16),
    "americas": (16, 24),
}

PROBE_QUERY = """
    SELECT
        i.time,
        o.close_price,
        o.high_price,
        o.low_price,
        o.volume,
        i.atr_pct
    FROM indicators i
    INNER JOIN ohlcv o
        ON i.time = o.time
        AND i.symbol = o.symbol
        AND i.timeframe = o.timeframe
    WHERE i.symbol = $1
      AND i.timeframe = $2
      AND i.time >= $3
      AND i.time <= $4
    ORDER BY i.time ASC
"""


@dataclass(frozen=True)
class ProbeConfig:
    symbol: str
    timeframe: str
    start: str | datetime
    end: str | datetime
    forward_bars: int
    min_bars_total: int
    min_bars_per_window: int


@dataclass(frozen=True)
class WindowStats:
    window: str
    sample_count: int
    mean_forward_pct: float
    median_forward_pct: float
    mean_adverse_excursion_pct: float
    win_rate_pct: float


@dataclass(frozen=True)
class ProbeSummary:
    symbol: str
    timeframe: str
    eligible_bars: int
    baseline: WindowStats
    windows: tuple[WindowStats, ...]
    best_window: str | None


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


def _median(values: Iterable[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return float(statistics.median(materialized))


def session_for_hour(
    hour: int, windows: Mapping[str, tuple[int, int]] = DEFAULT_WINDOWS
) -> str | None:
    """Return disjoint session name for UTC hour, or None if unmapped."""
    for name, (start, end) in windows.items():
        if start < end:
            if start <= hour < end:
                return name
        elif hour >= start or hour < end:
            return name
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _coerce_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _forward_return_pct(closes: Sequence[float], index: int, forward_bars: int) -> float | None:
    if index + forward_bars >= len(closes):
        return None
    entry = closes[index]
    future = closes[index + forward_bars]
    if entry <= 0:
        return None
    return (future / entry - 1.0) * 100.0


def _adverse_excursion_pct(
    entry: float, lows: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(lows):
        return None
    if entry <= 0:
        return None
    window_lows = lows[index + 1 : index + forward_bars + 1]
    if not window_lows:
        return None
    min_low = min(window_lows)
    return (entry - min_low) / entry * 100.0


def probe_session_rows(
    rows: Sequence[Mapping[str, object]],
    config: ProbeConfig,
    windows: Mapping[str, tuple[int, int]] = DEFAULT_WINDOWS,
) -> ProbeSummary:
    """Label each bar by UTC session; compute forward return and long MAE."""
    forwards_by_window: dict[str, list[float]] = {name: [] for name in windows}
    adverse_by_window: dict[str, list[float]] = {name: [] for name in windows}
    all_forwards: list[float] = []
    all_adverse: list[float] = []

    closes: list[float] = []
    lows: list[float] = []
    hours: list[int] = []

    for row in rows:
        close = _coerce_float(row.get("close_price"))
        low = _coerce_float(row.get("low_price"))
        bar_time = row.get("time")
        if close is None or low is None or close <= 0 or not isinstance(bar_time, datetime):
            continue
        closes.append(close)
        lows.append(low)
        hours.append(bar_time.hour)

    for index in range(len(closes)):
        forward = _forward_return_pct(closes, index, config.forward_bars)
        adverse = _adverse_excursion_pct(closes[index], lows, index, config.forward_bars)
        if forward is None or adverse is None:
            continue
        session = session_for_hour(hours[index], windows)
        if session is None:
            continue
        forwards_by_window[session].append(forward)
        adverse_by_window[session].append(adverse)
        all_forwards.append(forward)
        all_adverse.append(adverse)

    def _stats(name: str, forwards: list[float], adverse: list[float]) -> WindowStats:
        wins = sum(1 for value in forwards if value > 0.0)
        count = len(forwards)
        return WindowStats(
            window=name,
            sample_count=count,
            mean_forward_pct=_mean(forwards),
            median_forward_pct=_median(forwards),
            mean_adverse_excursion_pct=_mean(adverse),
            win_rate_pct=(wins / count * 100.0) if count else 0.0,
        )

    baseline = _stats("baseline", all_forwards, all_adverse)
    window_stats = tuple(
        _stats(name, forwards_by_window[name], adverse_by_window[name]) for name in windows
    )

    best_window: str | None = None
    best_forward = baseline.mean_forward_pct
    for stats in window_stats:
        if stats.sample_count < config.min_bars_per_window:
            continue
        if (
            stats.mean_forward_pct > baseline.mean_forward_pct
            and stats.mean_adverse_excursion_pct < baseline.mean_adverse_excursion_pct
        ):
            if best_window is None or stats.mean_forward_pct > best_forward:
                best_window = stats.window
                best_forward = stats.mean_forward_pct

    return ProbeSummary(
        symbol=config.symbol,
        timeframe=config.timeframe,
        eligible_bars=len(all_forwards),
        baseline=baseline,
        windows=window_stats,
        best_window=best_window,
    )


def evaluate_pulse(summary: ProbeSummary, config: ProbeConfig) -> str:
    if summary.eligible_bars < config.min_bars_total:
        return "NO_PULSE"
    if all(stats.sample_count < config.min_bars_per_window for stats in summary.windows):
        return "SPARSE"
    if summary.best_window is not None:
        return "HAS_PULSE"
    return "WEAK_EDGE"


def print_summary(summary: ProbeSummary, config: ProbeConfig) -> None:
    verdict = evaluate_pulse(summary, config)
    print("Session Liquidity Router Feasibility Probe")
    print("=" * 52)
    print(f"Symbol:        {summary.symbol}")
    print(f"Timeframe:     {summary.timeframe}")
    print(f"Window:        {config.start} -> {config.end}")
    print(f"Eligible bars: {summary.eligible_bars} (forward {config.forward_bars}h)")
    print()
    print("Baseline (all hours)")
    print("-" * 52)
    _print_stats(summary.baseline)
    print()
    for stats in summary.windows:
        print(f"Session: {stats.window}")
        print("-" * 52)
        _print_stats(stats)
        beats = (
            stats.sample_count >= config.min_bars_per_window
            and stats.mean_forward_pct > summary.baseline.mean_forward_pct
            and stats.mean_adverse_excursion_pct < summary.baseline.mean_adverse_excursion_pct
        )
        print(f"  Beats baseline (return up, MAE down): {beats}")
        print()
    messages = {
        "NO_PULSE": "Verdict: NO PULSE - insufficient eligible bars.",
        "SPARSE": "Verdict: SPARSE - no session has enough samples.",
        "WEAK_EDGE": "Verdict: WEAK EDGE - no session beats baseline on return and MAE.",
        "HAS_PULSE": (
            f"Verdict: HAS PULSE - window '{summary.best_window}' beats baseline; "
            "justify router implementation brief."
        ),
    }
    print(messages[verdict])


def _print_stats(stats: WindowStats) -> None:
    print(f"  Samples:       {stats.sample_count}")
    print(f"  Mean forward:  {stats.mean_forward_pct:.3f}%")
    print(f"  Median:        {stats.median_forward_pct:.3f}%")
    print(f"  Mean MAE:      {stats.mean_adverse_excursion_pct:.3f}%")
    print(f"  Win rate:      {stats.win_rate_pct:.1f}%")


async def fetch_probe_rows(config: ProbeConfig) -> list[dict[str, object]]:
    pool = get_pool()
    records = await pool.fetch(
        PROBE_QUERY,
        config.symbol,
        config.timeframe,
        _coerce_datetime(config.start),
        _coerce_datetime(config.end),
    )
    return [dict(record) for record in records]


async def run_probe(config: ProbeConfig) -> ProbeSummary:
    db_config = build_db_config()
    await init_pool(db_config)
    try:
        rows = await fetch_probe_rows(config)
    finally:
        await close_pool()
    return probe_session_rows(rows, config)


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(description="Probe session liquidity window feasibility.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--forward-bars", type=int, default=12)
    parser.add_argument("--min-bars-total", type=int, default=1000)
    parser.add_argument("--min-bars-per-window", type=int, default=500)
    args = parser.parse_args(argv)
    return ProbeConfig(
        symbol=args.symbol.upper(),
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        forward_bars=args.forward_bars,
        min_bars_total=args.min_bars_total,
        min_bars_per_window=args.min_bars_per_window,
    )


async def _main() -> int:
    configure_logger("WARNING")
    config = parse_args()
    summary = await run_probe(config)
    print_summary(summary, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
