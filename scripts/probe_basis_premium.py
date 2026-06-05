#!/usr/bin/env python3
"""Cheap feasibility probe for perp basis / premium primitives.

Read-only: perp_basis_metrics + ohlcv (+ optional funding context in report).
See docs/specs/basis-premium-data-ingestion-brief-v0.md for pass gates.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from src.db import close_pool, get_pool, init_pool
from src.utils.logger import configure_logger

PROBE_QUERY = """
    SELECT
        p.time,
        p.basis_bps,
        p.premium_index,
        o.close_price,
        o.low_price
    FROM perp_basis_metrics p
    INNER JOIN ohlcv o
        ON p.time = o.time
        AND p.symbol = o.symbol
        AND p.timeframe = o.timeframe
    WHERE p.exchange = $1
      AND p.symbol = $2
      AND p.timeframe = $3
      AND p.time >= $4
      AND p.time <= $5
    ORDER BY p.time ASC
"""


class NormalizationSide(StrEnum):
    FROM_POSITIVE = "from_positive"
    FROM_NEGATIVE = "from_negative"


class ScenarioKind(StrEnum):
    EXTREME_POSITIVE = "extreme_positive"
    EXTREME_NEGATIVE = "extreme_negative"
    NORMALIZATION = "normalization"


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    timeframe: str
    exchange: str
    start: str
    end: str
    tail_pcts: tuple[int, ...]
    forward_bars_12h: int
    forward_bars_24h: int
    min_events_per_symbol: int
    min_events_pooled: int
    min_mean_forward_pct: float
    min_mae_improvement_pct: float
    max_concentration_pct: float


@dataclass(frozen=True)
class PremiumBar:
    time: datetime
    basis_bps: float
    premium_index: float
    close_price: float
    low_price: float


@dataclass(frozen=True)
class MetricEvent:
    time: datetime
    metric: str
    tail_pct: int
    side: str
    basis_bps: float
    forward_12h_pct: float
    forward_24h_pct: float
    mae_12h_pct: float
    mae_24h_pct: float


@dataclass(frozen=True)
class HorizonStats:
    horizon_bars: int
    event_count: int
    mean_forward_pct: float
    median_forward_pct: float
    mean_mae_pct: float
    baseline_mae_pct: float
    mae_improvement_pct: float
    max_event_share_of_positive_pct: float


@dataclass(frozen=True)
class ScenarioResult:
    symbol: str
    kind: ScenarioKind
    metric: str
    tail_pct: int
    events: tuple[MetricEvent, ...]
    baseline_mae_12h: float
    baseline_mae_24h: float

    def stats_for(self, horizon: str, baseline_mae: float) -> HorizonStats:
        if horizon == "12h":
            bars = 12
            fwd_attr = "forward_12h_pct"
            mae_attr = "mae_12h_pct"
        else:
            bars = 24
            fwd_attr = "forward_24h_pct"
            mae_attr = "mae_24h_pct"

        if not self.events:
            return HorizonStats(bars, 0, 0.0, 0.0, 0.0, baseline_mae, 0.0, 0.0)

        forwards = [getattr(event, fwd_attr) for event in self.events]
        maes = [getattr(event, mae_attr) for event in self.events]
        positive = [value for value in forwards if value > 0]
        concentration = max(positive) / sum(positive) * 100.0 if positive else 0.0
        mean_mae = _mean(maes)
        improvement = 0.0
        if baseline_mae > 0:
            improvement = (baseline_mae - mean_mae) / baseline_mae * 100.0

        return HorizonStats(
            horizon_bars=bars,
            event_count=len(self.events),
            mean_forward_pct=_mean(forwards),
            median_forward_pct=_median(forwards),
            mean_mae_pct=mean_mae,
            baseline_mae_pct=baseline_mae,
            mae_improvement_pct=improvement,
            max_event_share_of_positive_pct=concentration,
        )


@dataclass(frozen=True)
class SymbolProbeSummary:
    symbol: str
    bar_count: int
    scenarios: tuple[ScenarioResult, ...]
    baseline_mae_12h: float
    baseline_mae_24h: float


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


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def tail_threshold_high(values: Sequence[float], tail_pct: int) -> float:
    return _percentile(values, 100.0 - tail_pct)


def tail_threshold_low(values: Sequence[float], tail_pct: int) -> float:
    return _percentile(values, float(tail_pct))


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
    return (entry - min(window_lows)) / entry * 100.0


def _metric_value(bar: PremiumBar, metric: str) -> float:
    if metric == "premium_index":
        return bar.premium_index
    return bar.basis_bps


def compute_baseline_mae(
    bars: Sequence[PremiumBar],
    forward_bars: int,
) -> float:
    closes = [bar.close_price for bar in bars]
    lows = [bar.low_price for bar in bars]
    maes: list[float] = []
    for index in range(len(bars)):
        mae = _adverse_excursion_pct(closes[index], lows, index, forward_bars)
        if mae is not None:
            maes.append(mae)
    return _mean(maes)


def _build_event(
    bar: PremiumBar,
    *,
    metric: str,
    tail_pct: int,
    side: str,
    closes: Sequence[float],
    lows: Sequence[float],
    index: int,
    config: ProbeConfig,
) -> MetricEvent | None:
    forward_12 = _forward_return_pct(closes, index, config.forward_bars_12h)
    forward_24 = _forward_return_pct(closes, index, config.forward_bars_24h)
    mae_12 = _adverse_excursion_pct(bar.close_price, lows, index, config.forward_bars_12h)
    mae_24 = _adverse_excursion_pct(bar.close_price, lows, index, config.forward_bars_24h)
    if None in (forward_12, forward_24, mae_12, mae_24):
        return None
    return MetricEvent(
        time=bar.time,
        metric=metric,
        tail_pct=tail_pct,
        side=side,
        basis_bps=bar.basis_bps,
        forward_12h_pct=forward_12,
        forward_24h_pct=forward_24,
        mae_12h_pct=mae_12,
        mae_24h_pct=mae_24,
    )


def detect_extreme_events(
    bars: Sequence[PremiumBar],
    *,
    metric: str,
    tail_pct: int,
    kind: ScenarioKind,
    config: ProbeConfig,
) -> list[MetricEvent]:
    values = [_metric_value(bar, metric) for bar in bars]
    if kind == ScenarioKind.EXTREME_POSITIVE:
        threshold = tail_threshold_high(values, tail_pct)
        side = "positive"
    else:
        threshold = tail_threshold_low(values, tail_pct)
        side = "negative"

    closes = [bar.close_price for bar in bars]
    lows = [bar.low_price for bar in bars]
    events: list[MetricEvent] = []
    for index, bar in enumerate(bars):
        metric_value = _metric_value(bar, metric)
        if kind == ScenarioKind.EXTREME_POSITIVE:
            if metric_value < threshold:
                continue
        elif metric_value > threshold:
            continue
        event = _build_event(
            bar,
            metric=metric,
            tail_pct=tail_pct,
            side=side,
            closes=closes,
            lows=lows,
            index=index,
            config=config,
        )
        if event is not None:
            events.append(event)
    return events


def detect_normalization_events(
    bars: Sequence[PremiumBar],
    *,
    metric: str,
    tail_pct: int,
    config: ProbeConfig,
) -> list[MetricEvent]:
    values = [_metric_value(bar, metric) for bar in bars]
    high_entry = tail_threshold_high(values, tail_pct)
    low_entry = tail_threshold_low(values, tail_pct)
    exit_band = _percentile([abs(value) for value in values], 50.0)

    closes = [bar.close_price for bar in bars]
    lows = [bar.low_price for bar in bars]
    events: list[MetricEvent] = []
    state: str = "idle"
    prior_extreme = 0.0

    for index, bar in enumerate(bars):
        value = _metric_value(bar, metric)
        if state == "idle":
            if value >= high_entry:
                state = "extreme_positive"
                prior_extreme = value
            elif value <= low_entry:
                state = "extreme_negative"
                prior_extreme = value
            continue

        if state == "extreme_positive":
            if value >= high_entry:
                prior_extreme = max(prior_extreme, value)
                continue
            if abs(value) <= exit_band:
                event = _build_event(
                    bar,
                    metric=metric,
                    tail_pct=tail_pct,
                    side=NormalizationSide.FROM_POSITIVE,
                    closes=closes,
                    lows=lows,
                    index=index,
                    config=config,
                )
                if event is not None:
                    events.append(event)
                state = "idle"
            elif value <= low_entry:
                state = "extreme_negative"
                prior_extreme = value
            continue

        if state == "extreme_negative":
            if value <= low_entry:
                prior_extreme = min(prior_extreme, value)
                continue
            if abs(value) <= exit_band:
                event = _build_event(
                    bar,
                    metric=metric,
                    tail_pct=tail_pct,
                    side=NormalizationSide.FROM_NEGATIVE,
                    closes=closes,
                    lows=lows,
                    index=index,
                    config=config,
                )
                if event is not None:
                    events.append(event)
                state = "idle"
            elif value >= high_entry:
                state = "extreme_positive"
                prior_extreme = value

    return events


def scenario_label(result: ScenarioResult) -> str:
    return f"{result.symbol}:{result.kind}:{result.metric}:tail{result.tail_pct}"


def forward_passes(stats_12: HorizonStats, stats_24: HorizonStats, min_pct: float) -> bool:
    hits: list[float] = []
    if abs(stats_12.mean_forward_pct) > min_pct:
        hits.append(stats_12.mean_forward_pct)
    if abs(stats_24.mean_forward_pct) > min_pct:
        hits.append(stats_24.mean_forward_pct)
    if not hits:
        return False
    if len(hits) == 2 and hits[0] * hits[1] < 0:
        return False
    return True


def mae_passes(stats_12: HorizonStats, stats_24: HorizonStats, min_improvement: float) -> bool:
    return (
        stats_12.mae_improvement_pct >= min_improvement
        or stats_24.mae_improvement_pct >= min_improvement
    )


def concentration_passes(
    stats_12: HorizonStats,
    stats_24: HorizonStats,
    max_concentration: float,
) -> bool:
    if stats_12.event_count == 0:
        return True
    return (
        stats_12.max_event_share_of_positive_pct <= max_concentration
        and stats_24.max_event_share_of_positive_pct <= max_concentration
    )


def evaluate_scenario(
    result: ScenarioResult,
    *,
    event_count: int,
    config: ProbeConfig,
) -> bool:
    if event_count < config.min_events_per_symbol:
        return False
    stats_12 = result.stats_for("12h", result.baseline_mae_12h)
    stats_24 = result.stats_for("24h", result.baseline_mae_24h)
    edge = forward_passes(stats_12, stats_24, config.min_mean_forward_pct) or mae_passes(
        stats_12, stats_24, config.min_mae_improvement_pct
    )
    return edge and concentration_passes(stats_12, stats_24, config.max_concentration_pct)


def evaluate_pooled_scenario(
    results: Sequence[ScenarioResult],
    *,
    config: ProbeConfig,
) -> bool:
    """Same scenario key pooled across symbols."""
    if not results:
        return False
    pooled_events = [event for result in results for event in result.events]
    if len(pooled_events) < config.min_events_pooled:
        return False

    baseline_mae_12 = _mean(result.baseline_mae_12h for result in results)
    baseline_mae_24 = _mean(result.baseline_mae_24h for result in results)
    pooled = ScenarioResult(
        symbol="POOLED",
        kind=results[0].kind,
        metric=results[0].metric,
        tail_pct=results[0].tail_pct,
        events=tuple(pooled_events),
        baseline_mae_12h=baseline_mae_12,
        baseline_mae_24h=baseline_mae_24,
    )
    stats_12 = pooled.stats_for("12h", baseline_mae_12)
    stats_24 = pooled.stats_for("24h", baseline_mae_24)
    edge = forward_passes(stats_12, stats_24, config.min_mean_forward_pct) or mae_passes(
        stats_12, stats_24, config.min_mae_improvement_pct
    )
    return edge and concentration_passes(stats_12, stats_24, config.max_concentration_pct)


def probe_symbol(
    bars: Sequence[PremiumBar], symbol: str, config: ProbeConfig
) -> SymbolProbeSummary:
    baseline_mae_12 = compute_baseline_mae(bars, config.forward_bars_12h)
    baseline_mae_24 = compute_baseline_mae(bars, config.forward_bars_24h)

    scenarios: list[ScenarioResult] = []
    for metric in ("basis_bps", "premium_index"):
        for tail_pct in config.tail_pcts:
            for kind in (ScenarioKind.EXTREME_POSITIVE, ScenarioKind.EXTREME_NEGATIVE):
                events = detect_extreme_events(
                    bars,
                    metric=metric,
                    tail_pct=tail_pct,
                    kind=kind,
                    config=config,
                )
                scenarios.append(
                    ScenarioResult(
                        symbol=symbol,
                        kind=kind,
                        metric=metric,
                        tail_pct=tail_pct,
                        events=tuple(events),
                        baseline_mae_12h=baseline_mae_12,
                        baseline_mae_24h=baseline_mae_24,
                    )
                )
            norm_events = detect_normalization_events(
                bars,
                metric=metric,
                tail_pct=tail_pct,
                config=config,
            )
            scenarios.append(
                ScenarioResult(
                    symbol=symbol,
                    kind=ScenarioKind.NORMALIZATION,
                    metric=metric,
                    tail_pct=tail_pct,
                    events=tuple(norm_events),
                    baseline_mae_12h=baseline_mae_12,
                    baseline_mae_24h=baseline_mae_24,
                )
            )

    return SymbolProbeSummary(
        symbol=symbol,
        bar_count=len(bars),
        scenarios=tuple(scenarios),
        baseline_mae_12h=baseline_mae_12,
        baseline_mae_24h=baseline_mae_24,
    )


def evaluate_report(report: ProbeReport) -> tuple[str, tuple[str, ...]]:
    passing: list[str] = []
    scenario_groups: dict[tuple[ScenarioKind, str, int], list[ScenarioResult]] = {}

    for summary in report.symbols:
        for scenario in summary.scenarios:
            key = (scenario.kind, scenario.metric, scenario.tail_pct)
            scenario_groups.setdefault(key, []).append(scenario)
            if evaluate_scenario(
                scenario,
                event_count=len(scenario.events),
                config=report.config,
            ):
                passing.append(scenario_label(scenario))

    for key, grouped in scenario_groups.items():
        kind, metric, tail_pct = key
        if evaluate_pooled_scenario(grouped, config=report.config):
            passing.append(f"POOLED:{kind}:{metric}:tail{tail_pct}")

    if passing:
        return "HAS_PULSE", tuple(sorted(set(passing)))
    if not any(scenario.events for summary in report.symbols for scenario in summary.scenarios):
        return "NO_PULSE", ()
    if all(
        len(scenario.events) < report.config.min_events_per_symbol
        for summary in report.symbols
        for scenario in summary.scenarios
    ):
        return "SPARSE", ()
    return "WEAK_EDGE", ()


async def fetch_bars(
    symbol: str,
    *,
    exchange: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[PremiumBar]:
    pool = get_pool()
    rows = await pool.fetch(
        PROBE_QUERY,
        exchange,
        symbol,
        timeframe,
        start,
        end,
    )
    bars: list[PremiumBar] = []
    for row in rows:
        bars.append(
            PremiumBar(
                time=row["time"],
                basis_bps=float(row["basis_bps"]),
                premium_index=float(row["premium_index"]),
                close_price=float(row["close_price"]),
                low_price=float(row["low_price"]),
            )
        )
    return bars


async def run_probe(config: ProbeConfig) -> ProbeReport:
    start = datetime.fromisoformat(config.start)
    end = datetime.fromisoformat(config.end)
    summaries: list[SymbolProbeSummary] = []
    for symbol in config.symbols:
        bars = await fetch_bars(
            symbol,
            exchange=config.exchange,
            timeframe=config.timeframe,
            start=start,
            end=end,
        )
        summaries.append(probe_symbol(bars, symbol, config))

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
    print("Basis / Premium Feasibility Probe")
    print("=" * 72)
    print(f"Symbols:     {', '.join(report.config.symbols)}")
    print(f"Timeframe:   {report.config.timeframe}")
    print(f"Exchange:    {report.config.exchange}")
    print(f"Window:      {report.config.start} -> {report.config.end}")
    print()

    for summary in report.symbols:
        print(f"## {summary.symbol} ({summary.bar_count} bars)")
        print(
            f"Baseline MAE: 12h={summary.baseline_mae_12h:.3f}% 24h={summary.baseline_mae_24h:.3f}%"
        )
        for scenario in summary.scenarios:
            if not scenario.events:
                continue
            stats_12 = scenario.stats_for("12h", scenario.baseline_mae_12h)
            stats_24 = scenario.stats_for("24h", scenario.baseline_mae_24h)
            print(
                f"  {scenario.kind} {scenario.metric} tail{scenario.tail_pct}% "
                f"n={len(scenario.events)} "
                f"fwd12={stats_12.mean_forward_pct:+.3f}% "
                f"fwd24={stats_24.mean_forward_pct:+.3f}% "
                f"mae12={stats_12.mean_mae_pct:.3f}% "
                f"(Δ{stats_12.mae_improvement_pct:+.1f}%) "
                f"mae24={stats_24.mean_mae_pct:.3f}% "
                f"(Δ{stats_24.mae_improvement_pct:+.1f}%)"
            )
        print()

    print(f"Verdict: {report.verdict}")
    if report.passing_scenarios:
        print("Passing scenarios:")
        for label in report.passing_scenarios:
            print(f"  - {label}")
    else:
        messages = {
            "NO_PULSE": "No qualifying events — close lane or reshape tails.",
            "SPARSE": "Events below per-symbol floor — lane too sparse.",
            "WEAK_EDGE": "Events exist but fail forward/MAE/concentration gates.",
            "HAS_PULSE": "At least one scenario passed — justify surface brief.",
        }
        print(messages.get(report.verdict, ""))


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(description="Probe perp basis/premium feasibility.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--exchange", default="binance_usdm")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--min-events-per-symbol", type=int, default=30)
    parser.add_argument("--min-events-pooled", type=int, default=100)
    parser.add_argument("--min-mean-forward-pct", type=float, default=0.15)
    parser.add_argument("--min-mae-improvement-pct", type=float, default=10.0)
    parser.add_argument("--max-concentration-pct", type=float, default=50.0)
    args = parser.parse_args(argv)
    symbols = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    return ProbeConfig(
        symbols=symbols,
        timeframe=args.timeframe,
        exchange=args.exchange,
        start=args.start,
        end=args.end,
        tail_pcts=(5, 10),
        forward_bars_12h=12,
        forward_bars_24h=24,
        min_events_per_symbol=args.min_events_per_symbol,
        min_events_pooled=args.min_events_pooled,
        min_mean_forward_pct=args.min_mean_forward_pct,
        min_mae_improvement_pct=args.min_mae_improvement_pct,
        max_concentration_pct=args.max_concentration_pct,
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
