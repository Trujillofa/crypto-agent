#!/usr/bin/env python3
"""Cheap feasibility probe for liquidity sweep / failed breakout structure.

Read-only: ohlcv only. Tests whether sweep-and-reject events mean-revert over
6h/12h/24h with controlled MAE. See docs/specs/liquidity-sweep-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from src.db import close_pool, get_pool, init_pool
from src.utils.logger import configure_logger

PROBE_QUERY = """
    SELECT
        time,
        open_price,
        high_price,
        low_price,
        close_price,
        volume
    FROM ohlcv
    WHERE symbol = $1
      AND timeframe = $2
      AND time >= $3
      AND time <= $4
    ORDER BY time ASC
"""


class EventSide(StrEnum):
    LONG = "long_failed_breakdown"
    SHORT = "short_failed_breakout"


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    timeframe: str
    start: str
    end: str
    lookback_bars: int
    range_expansion_mult: float
    volume_expansion_mult: float
    forward_bars_6h: int
    forward_bars_12h: int
    forward_bars_24h: int
    min_events_per_symbol: int
    min_events_pooled: int
    min_mean_forward_pct: float
    round_trip_fee_pct: float
    min_mae_improvement_pct: float
    max_concentration_pct: float
    max_month_share_pct: float


@dataclass(frozen=True)
class LiquidityBar:
    time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


@dataclass(frozen=True)
class SweepEvent:
    time: datetime
    side: EventSide
    close_price: float
    forward_6h_pct: float
    forward_12h_pct: float
    forward_24h_pct: float
    mae_6h_pct: float
    mae_12h_pct: float
    mae_24h_pct: float
    mfe_6h_pct: float
    mfe_12h_pct: float
    mfe_24h_pct: float


@dataclass(frozen=True)
class HorizonStats:
    horizon_bars: int
    event_count: int
    mean_forward_pct: float
    mean_forward_after_fees_pct: float
    median_forward_pct: float
    mean_mae_pct: float
    baseline_mae_pct: float
    mae_improvement_pct: float
    mean_mfe_pct: float
    max_event_share_of_positive_pct: float
    max_month_share_pct: float


@dataclass(frozen=True)
class SideResult:
    symbol: str
    side: EventSide
    events: tuple[SweepEvent, ...]
    baseline_mae_6h: float
    baseline_mae_12h: float
    baseline_mae_24h: float

    def stats_for(self, horizon: str, baseline_mae: float, fee_pct: float) -> HorizonStats:
        if horizon == "6h":
            fwd_attr = "forward_6h_pct"
            mae_attr = "mae_6h_pct"
            mfe_attr = "mfe_6h_pct"
            bars = 6
        elif horizon == "12h":
            fwd_attr = "forward_12h_pct"
            mae_attr = "mae_12h_pct"
            mfe_attr = "mfe_12h_pct"
            bars = 12
        else:
            fwd_attr = "forward_24h_pct"
            mae_attr = "mae_24h_pct"
            mfe_attr = "mfe_24h_pct"
            bars = 24

        if not self.events:
            return HorizonStats(bars, 0, 0.0, 0.0, 0.0, 0.0, baseline_mae, 0.0, 0.0, 0.0, 0.0)

        forwards = [getattr(event, fwd_attr) for event in self.events]
        maes = [getattr(event, mae_attr) for event in self.events]
        mfes = [getattr(event, mfe_attr) for event in self.events]
        mean_fwd = _mean(forwards)
        positive = [value for value in forwards if value > 0]
        concentration = max(positive) / sum(positive) * 100.0 if positive else 0.0
        months = Counter(event.time.strftime("%Y-%m") for event in self.events)
        max_month = max(months.values()) / len(self.events) * 100.0
        mean_mae = _mean(maes)
        improvement = 0.0
        if baseline_mae > 0:
            improvement = (baseline_mae - mean_mae) / baseline_mae * 100.0

        return HorizonStats(
            horizon_bars=bars,
            event_count=len(self.events),
            mean_forward_pct=mean_fwd,
            mean_forward_after_fees_pct=mean_fwd - fee_pct,
            median_forward_pct=_median(forwards),
            mean_mae_pct=mean_mae,
            baseline_mae_pct=baseline_mae,
            mae_improvement_pct=improvement,
            mean_mfe_pct=_mean(mfes),
            max_event_share_of_positive_pct=concentration,
            max_month_share_pct=max_month,
        )


@dataclass(frozen=True)
class SymbolProbeSummary:
    symbol: str
    bar_count: int
    sides: tuple[SideResult, ...]
    baseline_mae_6h_long: float
    baseline_mae_12h_long: float
    baseline_mae_24h_long: float
    baseline_mae_6h_short: float
    baseline_mae_12h_short: float
    baseline_mae_24h_short: float


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    symbols: tuple[SymbolProbeSummary, ...]
    verdict: str
    passing_scenarios: tuple[str, ...]


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


def _bar_range(bar: LiquidityBar) -> float:
    return bar.high_price - bar.low_price


def _long_forward_return_pct(
    closes: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(closes):
        return None
    entry = closes[index]
    future = closes[index + forward_bars]
    if entry <= 0:
        return None
    return (future - entry) / entry * 100.0


def _short_forward_return_pct(
    closes: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(closes):
        return None
    entry = closes[index]
    future = closes[index + forward_bars]
    if entry <= 0:
        return None
    return (entry - future) / entry * 100.0


def _long_mae_pct(
    entry: float, lows: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(lows):
        return None
    if entry <= 0:
        return None
    window_lows = lows[index + 1 : index + forward_bars + 1]
    if not window_lows:
        return None
    return (entry - min(window_lows)) / entry * 100.0


def _short_mae_pct(
    entry: float, highs: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(highs):
        return None
    if entry <= 0:
        return None
    window_highs = highs[index + 1 : index + forward_bars + 1]
    if not window_highs:
        return None
    return (max(window_highs) - entry) / entry * 100.0


def _long_mfe_pct(
    entry: float, highs: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(highs):
        return None
    if entry <= 0:
        return None
    window_highs = highs[index + 1 : index + forward_bars + 1]
    if not window_highs:
        return None
    return (max(window_highs) - entry) / entry * 100.0


def _short_mfe_pct(
    entry: float, lows: Sequence[float], index: int, forward_bars: int
) -> float | None:
    if index + forward_bars >= len(lows):
        return None
    if entry <= 0:
        return None
    window_lows = lows[index + 1 : index + forward_bars + 1]
    if not window_lows:
        return None
    return (entry - min(window_lows)) / entry * 100.0


def compute_baseline_long_mae(bars: Sequence[LiquidityBar], forward_bars: int) -> float:
    closes = [bar.close_price for bar in bars]
    lows = [bar.low_price for bar in bars]
    maes: list[float] = []
    for index in range(len(bars)):
        mae = _long_mae_pct(closes[index], lows, index, forward_bars)
        if mae is not None:
            maes.append(mae)
    return _mean(maes)


def compute_baseline_short_mae(bars: Sequence[LiquidityBar], forward_bars: int) -> float:
    closes = [bar.close_price for bar in bars]
    highs = [bar.high_price for bar in bars]
    maes: list[float] = []
    for index in range(len(bars)):
        mae = _short_mae_pct(closes[index], highs, index, forward_bars)
        if mae is not None:
            maes.append(mae)
    return _mean(maes)


def _prior_slice(values: Sequence[float], index: int, lookback: int) -> Sequence[float]:
    start = max(0, index - lookback)
    return values[start:index]


def _expansion_ok(
    bar: LiquidityBar,
    *,
    prior_ranges: Sequence[float],
    prior_volumes: Sequence[float],
    config: ProbeConfig,
) -> bool:
    if not prior_ranges or not prior_volumes:
        return False
    mean_range = _mean(prior_ranges)
    mean_volume = _mean(prior_volumes)
    if mean_range <= 0 or mean_volume <= 0:
        return False
    return (
        _bar_range(bar) >= config.range_expansion_mult * mean_range
        and bar.volume >= config.volume_expansion_mult * mean_volume
    )


def _build_sweep_event(
    bar: LiquidityBar,
    *,
    side: EventSide,
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    index: int,
    config: ProbeConfig,
) -> SweepEvent | None:
    entry = bar.close_price
    if side is EventSide.LONG:
        fwd_6 = _long_forward_return_pct(closes, index, config.forward_bars_6h)
        fwd_12 = _long_forward_return_pct(closes, index, config.forward_bars_12h)
        fwd_24 = _long_forward_return_pct(closes, index, config.forward_bars_24h)
        mae_6 = _long_mae_pct(entry, lows, index, config.forward_bars_6h)
        mae_12 = _long_mae_pct(entry, lows, index, config.forward_bars_12h)
        mae_24 = _long_mae_pct(entry, lows, index, config.forward_bars_24h)
        mfe_6 = _long_mfe_pct(entry, highs, index, config.forward_bars_6h)
        mfe_12 = _long_mfe_pct(entry, highs, index, config.forward_bars_12h)
        mfe_24 = _long_mfe_pct(entry, highs, index, config.forward_bars_24h)
    else:
        fwd_6 = _short_forward_return_pct(closes, index, config.forward_bars_6h)
        fwd_12 = _short_forward_return_pct(closes, index, config.forward_bars_12h)
        fwd_24 = _short_forward_return_pct(closes, index, config.forward_bars_24h)
        mae_6 = _short_mae_pct(entry, highs, index, config.forward_bars_6h)
        mae_12 = _short_mae_pct(entry, highs, index, config.forward_bars_12h)
        mae_24 = _short_mae_pct(entry, highs, index, config.forward_bars_24h)
        mfe_6 = _short_mfe_pct(entry, lows, index, config.forward_bars_6h)
        mfe_12 = _short_mfe_pct(entry, lows, index, config.forward_bars_12h)
        mfe_24 = _short_mfe_pct(entry, lows, index, config.forward_bars_24h)

    metrics = (fwd_6, fwd_12, fwd_24, mae_6, mae_12, mae_24, mfe_6, mfe_12, mfe_24)
    if any(value is None for value in metrics):
        return None

    return SweepEvent(
        time=bar.time,
        side=side,
        close_price=entry,
        forward_6h_pct=fwd_6,
        forward_12h_pct=fwd_12,
        forward_24h_pct=fwd_24,
        mae_6h_pct=mae_6,
        mae_12h_pct=mae_12,
        mae_24h_pct=mae_24,
        mfe_6h_pct=mfe_6,
        mfe_12h_pct=mfe_12,
        mfe_24h_pct=mfe_24,
    )


def detect_failed_upside_breakouts(
    bars: Sequence[LiquidityBar],
    *,
    config: ProbeConfig,
) -> list[SweepEvent]:
    highs = [bar.high_price for bar in bars]
    lows = [bar.low_price for bar in bars]
    closes = [bar.close_price for bar in bars]
    ranges = [_bar_range(bar) for bar in bars]
    volumes = [bar.volume for bar in bars]
    events: list[SweepEvent] = []

    for index, bar in enumerate(bars):
        if index < config.lookback_bars:
            continue
        prior_highs = _prior_slice(highs, index, config.lookback_bars)
        prior_high = max(prior_highs)
        if bar.high_price <= prior_high:
            continue
        if bar.close_price >= prior_high:
            continue
        if not _expansion_ok(
            bar,
            prior_ranges=_prior_slice(ranges, index, config.lookback_bars),
            prior_volumes=_prior_slice(volumes, index, config.lookback_bars),
            config=config,
        ):
            continue
        event = _build_sweep_event(
            bar,
            side=EventSide.SHORT,
            closes=closes,
            highs=highs,
            lows=lows,
            index=index,
            config=config,
        )
        if event is not None:
            events.append(event)
    return events


def detect_failed_downside_breakdowns(
    bars: Sequence[LiquidityBar],
    *,
    config: ProbeConfig,
) -> list[SweepEvent]:
    highs = [bar.high_price for bar in bars]
    lows = [bar.low_price for bar in bars]
    closes = [bar.close_price for bar in bars]
    ranges = [_bar_range(bar) for bar in bars]
    volumes = [bar.volume for bar in bars]
    events: list[SweepEvent] = []

    for index, bar in enumerate(bars):
        if index < config.lookback_bars:
            continue
        prior_lows = _prior_slice(lows, index, config.lookback_bars)
        prior_low = min(prior_lows)
        if bar.low_price >= prior_low:
            continue
        if bar.close_price <= prior_low:
            continue
        if not _expansion_ok(
            bar,
            prior_ranges=_prior_slice(ranges, index, config.lookback_bars),
            prior_volumes=_prior_slice(volumes, index, config.lookback_bars),
            config=config,
        ):
            continue
        event = _build_sweep_event(
            bar,
            side=EventSide.LONG,
            closes=closes,
            highs=highs,
            lows=lows,
            index=index,
            config=config,
        )
        if event is not None:
            events.append(event)
    return events


def side_label(result: SideResult) -> str:
    return f"{result.symbol}:{result.side}"


def forward_passes(
    stats_6: HorizonStats,
    stats_12: HorizonStats,
    stats_24: HorizonStats,
    min_pct: float,
) -> bool:
    hits: list[float] = []
    for stats in (stats_6, stats_12, stats_24):
        if stats.mean_forward_after_fees_pct > min_pct:
            hits.append(stats.mean_forward_after_fees_pct)
    if not hits:
        return False
    if len(hits) >= 2 and min(hits) * max(hits) < 0:
        return False
    return True


def mae_passes(
    stats_6: HorizonStats,
    stats_12: HorizonStats,
    stats_24: HorizonStats,
    min_improvement: float,
) -> bool:
    return any(
        stats.mae_improvement_pct >= min_improvement
        for stats in (stats_6, stats_12, stats_24)
        if stats.event_count > 0
    )


def concentration_passes(
    stats_6: HorizonStats,
    stats_12: HorizonStats,
    stats_24: HorizonStats,
    config: ProbeConfig,
) -> bool:
    if stats_12.event_count == 0:
        return True
    for stats in (stats_6, stats_12, stats_24):
        if stats.event_count == 0:
            continue
        if stats.max_event_share_of_positive_pct > config.max_concentration_pct:
            return False
        if stats.max_month_share_pct > config.max_month_share_pct:
            return False
    return True


def evaluate_side(result: SideResult, *, config: ProbeConfig) -> bool:
    if len(result.events) < config.min_events_per_symbol:
        return False
    stats_6 = result.stats_for("6h", result.baseline_mae_6h, config.round_trip_fee_pct)
    stats_12 = result.stats_for("12h", result.baseline_mae_12h, config.round_trip_fee_pct)
    stats_24 = result.stats_for("24h", result.baseline_mae_24h, config.round_trip_fee_pct)
    return (
        forward_passes(stats_6, stats_12, stats_24, config.min_mean_forward_pct)
        and mae_passes(stats_6, stats_12, stats_24, config.min_mae_improvement_pct)
        and concentration_passes(stats_6, stats_12, stats_24, config)
    )


def evaluate_pooled_side(
    results: Sequence[SideResult],
    *,
    config: ProbeConfig,
) -> bool:
    if not results:
        return False
    pooled_events = [event for result in results for event in result.events]
    if len(pooled_events) < config.min_events_pooled:
        return False
    baseline_mae_6 = _mean(result.baseline_mae_6h for result in results)
    baseline_mae_12 = _mean(result.baseline_mae_12h for result in results)
    baseline_mae_24 = _mean(result.baseline_mae_24h for result in results)
    pooled = SideResult(
        symbol="POOLED",
        side=results[0].side,
        events=tuple(pooled_events),
        baseline_mae_6h=baseline_mae_6,
        baseline_mae_12h=baseline_mae_12,
        baseline_mae_24h=baseline_mae_24,
    )
    stats_6 = pooled.stats_for("6h", baseline_mae_6, config.round_trip_fee_pct)
    stats_12 = pooled.stats_for("12h", baseline_mae_12, config.round_trip_fee_pct)
    stats_24 = pooled.stats_for("24h", baseline_mae_24, config.round_trip_fee_pct)
    return (
        forward_passes(stats_6, stats_12, stats_24, config.min_mean_forward_pct)
        and mae_passes(stats_6, stats_12, stats_24, config.min_mae_improvement_pct)
        and concentration_passes(stats_6, stats_12, stats_24, config)
    )


def symbols_contradict(
    grouped: Sequence[SideResult],
    *,
    min_events: int,
    threshold: float,
) -> bool:
    signs: list[int] = []
    for result in grouped:
        if len(result.events) < min_events:
            continue
        stats = result.stats_for("24h", result.baseline_mae_24h, 0.0)
        if stats.mean_forward_pct > threshold:
            signs.append(1)
        elif stats.mean_forward_pct < -threshold:
            signs.append(-1)
    if len(signs) < 2:
        return False
    return min(signs) < 0 < max(signs)


def probe_symbol(
    bars: Sequence[LiquidityBar], symbol: str, config: ProbeConfig
) -> SymbolProbeSummary:
    baseline_mae_6h_long = compute_baseline_long_mae(bars, config.forward_bars_6h)
    baseline_mae_12h_long = compute_baseline_long_mae(bars, config.forward_bars_12h)
    baseline_mae_24h_long = compute_baseline_long_mae(bars, config.forward_bars_24h)
    baseline_mae_6h_short = compute_baseline_short_mae(bars, config.forward_bars_6h)
    baseline_mae_12h_short = compute_baseline_short_mae(bars, config.forward_bars_12h)
    baseline_mae_24h_short = compute_baseline_short_mae(bars, config.forward_bars_24h)

    long_events = detect_failed_downside_breakdowns(bars, config=config)
    short_events = detect_failed_upside_breakouts(bars, config=config)

    sides = (
        SideResult(
            symbol=symbol,
            side=EventSide.LONG,
            events=tuple(long_events),
            baseline_mae_6h=baseline_mae_6h_long,
            baseline_mae_12h=baseline_mae_12h_long,
            baseline_mae_24h=baseline_mae_24h_long,
        ),
        SideResult(
            symbol=symbol,
            side=EventSide.SHORT,
            events=tuple(short_events),
            baseline_mae_6h=baseline_mae_6h_short,
            baseline_mae_12h=baseline_mae_12h_short,
            baseline_mae_24h=baseline_mae_24h_short,
        ),
    )

    return SymbolProbeSummary(
        symbol=symbol,
        bar_count=len(bars),
        sides=sides,
        baseline_mae_6h_long=baseline_mae_6h_long,
        baseline_mae_12h_long=baseline_mae_12h_long,
        baseline_mae_24h_long=baseline_mae_24h_long,
        baseline_mae_6h_short=baseline_mae_6h_short,
        baseline_mae_12h_short=baseline_mae_12h_short,
        baseline_mae_24h_short=baseline_mae_24h_short,
    )


def evaluate_report(report: ProbeReport) -> tuple[str, tuple[str, ...]]:
    passing: list[str] = []
    side_groups: dict[EventSide, list[SideResult]] = {}

    for summary in report.symbols:
        for side_result in summary.sides:
            side_groups.setdefault(side_result.side, []).append(side_result)
            if evaluate_side(side_result, config=report.config):
                passing.append(side_label(side_result))

    for side, grouped in side_groups.items():
        if symbols_contradict(
            grouped,
            min_events=report.config.min_events_per_symbol,
            threshold=report.config.min_mean_forward_pct,
        ):
            continue
        if evaluate_pooled_side(grouped, config=report.config):
            passing.append(f"POOLED:{side}")

    if passing:
        return "HAS_PULSE", tuple(sorted(set(passing)))
    if not any(side.events for summary in report.symbols for side in summary.sides):
        return "NO_PULSE", ()
    if all(
        len(side.events) < report.config.min_events_per_symbol
        for summary in report.symbols
        for side in summary.sides
    ):
        return "SPARSE", ()
    return "WEAK_EDGE", ()


async def fetch_bars(
    symbol: str,
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[LiquidityBar]:
    pool = get_pool()
    rows = await pool.fetch(PROBE_QUERY, symbol, timeframe, start, end)
    return [
        LiquidityBar(
            time=row["time"],
            open_price=float(row["open_price"]),
            high_price=float(row["high_price"]),
            low_price=float(row["low_price"]),
            close_price=float(row["close_price"]),
            volume=float(row["volume"]),
        )
        for row in rows
    ]


async def run_probe(config: ProbeConfig) -> ProbeReport:
    start = datetime.fromisoformat(config.start)
    end = datetime.fromisoformat(config.end)
    summaries = [
        probe_symbol(
            await fetch_bars(symbol, timeframe=config.timeframe, start=start, end=end),
            symbol,
            config,
        )
        for symbol in config.symbols
    ]
    preliminary = ProbeReport(
        config=config, symbols=tuple(summaries), verdict="", passing_scenarios=()
    )
    verdict, passing = evaluate_report(preliminary)
    return ProbeReport(
        config=config,
        symbols=tuple(summaries),
        verdict=verdict,
        passing_scenarios=passing,
    )


def print_report(report: ProbeReport) -> None:
    print("Liquidity Sweep / Failed Breakout Probe")
    print("=" * 72)
    print(f"Symbols:     {', '.join(report.config.symbols)}")
    print(f"Timeframe:   {report.config.timeframe}")
    print(f"Window:      {report.config.start} -> {report.config.end}")
    print(f"Lookback:    {report.config.lookback_bars} bars")
    print(f"Fee drag:    {report.config.round_trip_fee_pct:.3f}% round-trip")
    print()

    for summary in report.symbols:
        print(f"## {summary.symbol} ({summary.bar_count} bars)")
        print(
            f"Baseline long MAE:  6h={summary.baseline_mae_6h_long:.3f}% "
            f"12h={summary.baseline_mae_12h_long:.3f}% "
            f"24h={summary.baseline_mae_24h_long:.3f}%"
        )
        print(
            f"Baseline short MAE: 6h={summary.baseline_mae_6h_short:.3f}% "
            f"12h={summary.baseline_mae_12h_short:.3f}% "
            f"24h={summary.baseline_mae_24h_short:.3f}%"
        )
        for side_result in summary.sides:
            if not side_result.events:
                continue
            stats_6 = side_result.stats_for(
                "6h", side_result.baseline_mae_6h, report.config.round_trip_fee_pct
            )
            stats_12 = side_result.stats_for(
                "12h", side_result.baseline_mae_12h, report.config.round_trip_fee_pct
            )
            stats_24 = side_result.stats_for(
                "24h", side_result.baseline_mae_24h, report.config.round_trip_fee_pct
            )
            print(
                f"  {side_result.side} n={len(side_result.events)} "
                f"fwd6={stats_6.mean_forward_pct:+.3f}% "
                f"(net {stats_6.mean_forward_after_fees_pct:+.3f}%) "
                f"fwd12={stats_12.mean_forward_pct:+.3f}% "
                f"(net {stats_12.mean_forward_after_fees_pct:+.3f}%) "
                f"fwd24={stats_24.mean_forward_pct:+.3f}% "
                f"(net {stats_24.mean_forward_after_fees_pct:+.3f}%) "
                f"mae12={stats_12.mean_mae_pct:.3f}% (Δ{stats_12.mae_improvement_pct:+.1f}%) "
                f"mfe12={stats_12.mean_mfe_pct:.3f}% "
                f"month_max={stats_24.max_month_share_pct:.1f}%"
            )
        print()

    print(f"Verdict: {report.verdict}")
    if report.passing_scenarios:
        print("Passing scenarios:")
        for label in report.passing_scenarios:
            print(f"  - {label}")
    else:
        messages = {
            "NO_PULSE": "No qualifying events — close liquidity sweep lane.",
            "SPARSE": "Events below per-symbol floor — lane too sparse.",
            "WEAK_EDGE": "Events exist but fail forward+MAE/concentration gates.",
            "HAS_PULSE": "At least one side passed — justify surface brief.",
        }
        print(messages.get(report.verdict, ""))


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(
        description="Probe liquidity sweep / failed breakout mean-reversion."
    )
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--lookback-bars", type=int, default=24)
    parser.add_argument("--range-expansion-mult", type=float, default=1.2)
    parser.add_argument("--volume-expansion-mult", type=float, default=1.2)
    parser.add_argument("--min-events-per-symbol", type=int, default=20)
    parser.add_argument("--min-events-pooled", type=int, default=80)
    parser.add_argument("--min-mean-forward-pct", type=float, default=0.15)
    parser.add_argument("--round-trip-fee-pct", type=float, default=0.08)
    parser.add_argument("--min-mae-improvement-pct", type=float, default=10.0)
    parser.add_argument("--max-concentration-pct", type=float, default=50.0)
    parser.add_argument("--max-month-share-pct", type=float, default=40.0)
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    return ProbeConfig(
        symbols=symbols,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        lookback_bars=args.lookback_bars,
        range_expansion_mult=args.range_expansion_mult,
        volume_expansion_mult=args.volume_expansion_mult,
        forward_bars_6h=6,
        forward_bars_12h=12,
        forward_bars_24h=24,
        min_events_per_symbol=args.min_events_per_symbol,
        min_events_pooled=args.min_events_pooled,
        min_mean_forward_pct=args.min_mean_forward_pct,
        round_trip_fee_pct=args.round_trip_fee_pct,
        min_mae_improvement_pct=args.min_mae_improvement_pct,
        max_concentration_pct=args.max_concentration_pct,
        max_month_share_pct=args.max_month_share_pct,
    )


async def _main() -> int:
    configure_logger("WARNING")
    config = parse_args()
    await init_pool(build_db_config())
    try:
        report = await run_probe(config)
    finally:
        await close_pool()
    print_report(report)
    return 0 if report.verdict == "HAS_PULSE" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
