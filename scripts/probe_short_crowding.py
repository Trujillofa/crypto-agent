#!/usr/bin/env python3
"""Cheap feasibility probe for short entries in perp crowding regimes.

Read-only: perp_basis_metrics + funding_rates + ohlcv.
Tests whether extreme bullish crowding supports short forward edge or lower
short adverse excursion. See docs/specs/short-side-parity-audit-v0.md.
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
        p.time,
        p.basis_bps,
        p.premium_index,
        o.close_price,
        o.high_price,
        o.low_price,
        fr.funding_rate
    FROM perp_basis_metrics p
    INNER JOIN ohlcv o
        ON p.time = o.time
        AND p.symbol = o.symbol
        AND p.timeframe = o.timeframe
    LEFT JOIN LATERAL (
        SELECT funding_rate
        FROM funding_rates
        WHERE symbol = p.symbol
          AND funding_time <= p.time
        ORDER BY funding_time DESC
        LIMIT 1
    ) fr ON TRUE
    WHERE p.exchange = $1
      AND p.symbol = $2
      AND p.timeframe = $3
      AND p.time >= $4
      AND p.time <= $5
    ORDER BY p.time ASC
"""


class ScenarioKind(StrEnum):
    POSITIVE_PREMIUM_TAIL = "positive_premium_tail"
    POSITIVE_FUNDING_TAIL = "positive_funding_tail"
    COMBINED_CROWDED = "combined_crowded"
    NORMALIZATION_FROM_POSITIVE = "normalization_from_positive"


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
    round_trip_fee_pct: float
    min_mae_improvement_pct: float
    max_concentration_pct: float
    max_month_share_pct: float


@dataclass(frozen=True)
class CrowdingBar:
    time: datetime
    basis_bps: float
    premium_index: float
    funding_rate: float | None
    close_price: float
    high_price: float
    low_price: float


@dataclass(frozen=True)
class ShortEvent:
    time: datetime
    kind: ScenarioKind
    metric: str
    tail_pct: int
    basis_bps: float
    funding_rate: float | None
    forward_12h_pct: float
    forward_24h_pct: float
    mae_12h_pct: float
    mae_24h_pct: float


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
    max_event_share_of_positive_pct: float
    max_month_share_pct: float


@dataclass(frozen=True)
class ScenarioResult:
    symbol: str
    kind: ScenarioKind
    metric: str
    tail_pct: int
    events: tuple[ShortEvent, ...]
    baseline_mae_12h: float
    baseline_mae_24h: float

    def stats_for(self, horizon: str, baseline_mae: float, fee_pct: float) -> HorizonStats:
        if horizon == "12h":
            fwd_attr = "forward_12h_pct"
            mae_attr = "mae_12h_pct"
            bars = 12
        else:
            fwd_attr = "forward_24h_pct"
            mae_attr = "mae_24h_pct"
            bars = 24

        if not self.events:
            return HorizonStats(bars, 0, 0.0, 0.0, 0.0, 0.0, baseline_mae, 0.0, 0.0, 0.0)

        forwards = [getattr(event, fwd_attr) for event in self.events]
        maes = [getattr(event, mae_attr) for event in self.events]
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
            max_event_share_of_positive_pct=concentration,
            max_month_share_pct=max_month,
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


def _short_adverse_excursion_pct(
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


def compute_baseline_short_mae(bars: Sequence[CrowdingBar], forward_bars: int) -> float:
    closes = [bar.close_price for bar in bars]
    highs = [bar.high_price for bar in bars]
    maes: list[float] = []
    for index in range(len(bars)):
        mae = _short_adverse_excursion_pct(closes[index], highs, index, forward_bars)
        if mae is not None:
            maes.append(mae)
    return _mean(maes)


def _premium_metric(bar: CrowdingBar, metric: str) -> float:
    if metric == "premium_index":
        return bar.premium_index
    return bar.basis_bps


def _build_short_event(
    bar: CrowdingBar,
    *,
    kind: ScenarioKind,
    metric: str,
    tail_pct: int,
    closes: Sequence[float],
    highs: Sequence[float],
    index: int,
    config: ProbeConfig,
) -> ShortEvent | None:
    forward_12 = _short_forward_return_pct(closes, index, config.forward_bars_12h)
    forward_24 = _short_forward_return_pct(closes, index, config.forward_bars_24h)
    mae_12 = _short_adverse_excursion_pct(bar.close_price, highs, index, config.forward_bars_12h)
    mae_24 = _short_adverse_excursion_pct(bar.close_price, highs, index, config.forward_bars_24h)
    if None in (forward_12, forward_24, mae_12, mae_24):
        return None
    return ShortEvent(
        time=bar.time,
        kind=kind,
        metric=metric,
        tail_pct=tail_pct,
        basis_bps=bar.basis_bps,
        funding_rate=bar.funding_rate,
        forward_12h_pct=forward_12,
        forward_24h_pct=forward_24,
        mae_12h_pct=mae_12,
        mae_24h_pct=mae_24,
    )


def detect_positive_premium_tail_events(
    bars: Sequence[CrowdingBar],
    *,
    metric: str,
    tail_pct: int,
    config: ProbeConfig,
) -> list[ShortEvent]:
    values = [_premium_metric(bar, metric) for bar in bars]
    threshold = tail_threshold_high(values, tail_pct)
    closes = [bar.close_price for bar in bars]
    highs = [bar.high_price for bar in bars]
    events: list[ShortEvent] = []
    for index, bar in enumerate(bars):
        if _premium_metric(bar, metric) < threshold:
            continue
        event = _build_short_event(
            bar,
            kind=ScenarioKind.POSITIVE_PREMIUM_TAIL,
            metric=metric,
            tail_pct=tail_pct,
            closes=closes,
            highs=highs,
            index=index,
            config=config,
        )
        if event is not None:
            events.append(event)
    return events


def detect_positive_funding_tail_events(
    bars: Sequence[CrowdingBar],
    *,
    tail_pct: int,
    config: ProbeConfig,
) -> list[ShortEvent]:
    rates = [bar.funding_rate for bar in bars if bar.funding_rate is not None]
    if not rates:
        return []
    threshold = tail_threshold_high(rates, tail_pct)
    closes = [bar.close_price for bar in bars]
    highs = [bar.high_price for bar in bars]
    events: list[ShortEvent] = []
    for index, bar in enumerate(bars):
        if bar.funding_rate is None or bar.funding_rate < threshold or bar.funding_rate <= 0:
            continue
        event = _build_short_event(
            bar,
            kind=ScenarioKind.POSITIVE_FUNDING_TAIL,
            metric="funding_rate",
            tail_pct=tail_pct,
            closes=closes,
            highs=highs,
            index=index,
            config=config,
        )
        if event is not None:
            events.append(event)
    return events


def detect_combined_crowded_events(
    bars: Sequence[CrowdingBar],
    *,
    premium_metric: str,
    tail_pct: int,
    config: ProbeConfig,
) -> list[ShortEvent]:
    premium_values = [_premium_metric(bar, premium_metric) for bar in bars]
    funding_values = [bar.funding_rate for bar in bars if bar.funding_rate is not None]
    if not funding_values:
        return []
    premium_threshold = tail_threshold_high(premium_values, tail_pct)
    funding_threshold = tail_threshold_high(funding_values, tail_pct)
    closes = [bar.close_price for bar in bars]
    highs = [bar.high_price for bar in bars]
    events: list[ShortEvent] = []
    for index, bar in enumerate(bars):
        if bar.funding_rate is None:
            continue
        if _premium_metric(bar, premium_metric) < premium_threshold:
            continue
        if bar.funding_rate < funding_threshold or bar.funding_rate <= 0:
            continue
        event = _build_short_event(
            bar,
            kind=ScenarioKind.COMBINED_CROWDED,
            metric=f"{premium_metric}+funding_rate",
            tail_pct=tail_pct,
            closes=closes,
            highs=highs,
            index=index,
            config=config,
        )
        if event is not None:
            events.append(event)
    return events


def detect_normalization_from_positive_events(
    bars: Sequence[CrowdingBar],
    *,
    metric: str,
    tail_pct: int,
    config: ProbeConfig,
) -> list[ShortEvent]:
    values = [_premium_metric(bar, metric) for bar in bars]
    high_entry = tail_threshold_high(values, tail_pct)
    exit_band = _percentile([abs(value) for value in values], 50.0)
    closes = [bar.close_price for bar in bars]
    highs = [bar.high_price for bar in bars]
    events: list[ShortEvent] = []
    state = "idle"

    for index, bar in enumerate(bars):
        value = _premium_metric(bar, metric)
        if state == "idle":
            if value >= high_entry:
                state = "extreme_positive"
            continue

        if state == "extreme_positive":
            if value >= high_entry:
                continue
            if abs(value) <= exit_band:
                event = _build_short_event(
                    bar,
                    kind=ScenarioKind.NORMALIZATION_FROM_POSITIVE,
                    metric=metric,
                    tail_pct=tail_pct,
                    closes=closes,
                    highs=highs,
                    index=index,
                    config=config,
                )
                if event is not None:
                    events.append(event)
                state = "idle"

    return events


def scenario_label(result: ScenarioResult) -> str:
    return f"{result.symbol}:{result.kind}:{result.metric}:tail{result.tail_pct}"


def forward_passes_short(
    stats_12: HorizonStats,
    stats_24: HorizonStats,
    min_pct: float,
) -> bool:
    hits: list[float] = []
    if stats_12.mean_forward_after_fees_pct > min_pct:
        hits.append(stats_12.mean_forward_after_fees_pct)
    if stats_24.mean_forward_after_fees_pct > min_pct:
        hits.append(stats_24.mean_forward_after_fees_pct)
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
    config: ProbeConfig,
) -> bool:
    if stats_12.event_count == 0:
        return True
    return (
        stats_12.max_event_share_of_positive_pct <= config.max_concentration_pct
        and stats_24.max_event_share_of_positive_pct <= config.max_concentration_pct
        and stats_12.max_month_share_pct <= config.max_month_share_pct
        and stats_24.max_month_share_pct <= config.max_month_share_pct
    )


def evaluate_scenario(result: ScenarioResult, *, config: ProbeConfig) -> bool:
    if len(result.events) < config.min_events_per_symbol:
        return False
    stats_12 = result.stats_for("12h", result.baseline_mae_12h, config.round_trip_fee_pct)
    stats_24 = result.stats_for("24h", result.baseline_mae_24h, config.round_trip_fee_pct)
    edge = forward_passes_short(stats_12, stats_24, config.min_mean_forward_pct) or mae_passes(
        stats_12, stats_24, config.min_mae_improvement_pct
    )
    return edge and concentration_passes(stats_12, stats_24, config)


def evaluate_pooled_scenario(
    results: Sequence[ScenarioResult],
    *,
    config: ProbeConfig,
) -> bool:
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
    stats_12 = pooled.stats_for("12h", baseline_mae_12, config.round_trip_fee_pct)
    stats_24 = pooled.stats_for("24h", baseline_mae_24, config.round_trip_fee_pct)
    edge = forward_passes_short(stats_12, stats_24, config.min_mean_forward_pct) or mae_passes(
        stats_12, stats_24, config.min_mae_improvement_pct
    )
    return edge and concentration_passes(stats_12, stats_24, config)


def symbols_contradict(
    grouped: Sequence[ScenarioResult],
    *,
    min_events: int,
    threshold: float,
) -> bool:
    """True when ≥2 symbols with enough events disagree on short forward sign."""
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
    bars: Sequence[CrowdingBar], symbol: str, config: ProbeConfig
) -> SymbolProbeSummary:
    baseline_mae_12 = compute_baseline_short_mae(bars, config.forward_bars_12h)
    baseline_mae_24 = compute_baseline_short_mae(bars, config.forward_bars_24h)
    scenarios: list[ScenarioResult] = []

    for tail_pct in config.tail_pcts:
        for metric in ("basis_bps", "premium_index"):
            premium_events = detect_positive_premium_tail_events(
                bars, metric=metric, tail_pct=tail_pct, config=config
            )
            scenarios.append(
                ScenarioResult(
                    symbol=symbol,
                    kind=ScenarioKind.POSITIVE_PREMIUM_TAIL,
                    metric=metric,
                    tail_pct=tail_pct,
                    events=tuple(premium_events),
                    baseline_mae_12h=baseline_mae_12,
                    baseline_mae_24h=baseline_mae_24,
                )
            )
            norm_events = detect_normalization_from_positive_events(
                bars, metric=metric, tail_pct=tail_pct, config=config
            )
            scenarios.append(
                ScenarioResult(
                    symbol=symbol,
                    kind=ScenarioKind.NORMALIZATION_FROM_POSITIVE,
                    metric=metric,
                    tail_pct=tail_pct,
                    events=tuple(norm_events),
                    baseline_mae_12h=baseline_mae_12,
                    baseline_mae_24h=baseline_mae_24,
                )
            )
            combined = detect_combined_crowded_events(
                bars, premium_metric=metric, tail_pct=tail_pct, config=config
            )
            scenarios.append(
                ScenarioResult(
                    symbol=symbol,
                    kind=ScenarioKind.COMBINED_CROWDED,
                    metric=f"{metric}+funding_rate",
                    tail_pct=tail_pct,
                    events=tuple(combined),
                    baseline_mae_12h=baseline_mae_12,
                    baseline_mae_24h=baseline_mae_24,
                )
            )

        funding_events = detect_positive_funding_tail_events(bars, tail_pct=tail_pct, config=config)
        scenarios.append(
            ScenarioResult(
                symbol=symbol,
                kind=ScenarioKind.POSITIVE_FUNDING_TAIL,
                metric="funding_rate",
                tail_pct=tail_pct,
                events=tuple(funding_events),
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
            if evaluate_scenario(scenario, config=report.config):
                passing.append(scenario_label(scenario))

    for key, grouped in scenario_groups.items():
        if symbols_contradict(
            grouped,
            min_events=report.config.min_events_per_symbol,
            threshold=report.config.min_mean_forward_pct,
        ):
            continue
        if evaluate_pooled_scenario(grouped, config=report.config):
            kind, metric, tail_pct = key
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
) -> list[CrowdingBar]:
    pool = get_pool()
    rows = await pool.fetch(
        PROBE_QUERY,
        exchange,
        symbol,
        timeframe,
        start,
        end,
    )
    bars: list[CrowdingBar] = []
    for row in rows:
        funding = row["funding_rate"]
        bars.append(
            CrowdingBar(
                time=row["time"],
                basis_bps=float(row["basis_bps"]),
                premium_index=float(row["premium_index"]),
                funding_rate=float(funding) if funding is not None else None,
                close_price=float(row["close_price"]),
                high_price=float(row["high_price"]),
                low_price=float(row["low_price"]),
            )
        )
    return bars


async def run_probe(config: ProbeConfig) -> ProbeReport:
    start = datetime.fromisoformat(config.start)
    end = datetime.fromisoformat(config.end)
    summaries = [
        probe_symbol(
            await fetch_bars(
                symbol,
                exchange=config.exchange,
                timeframe=config.timeframe,
                start=start,
                end=end,
            ),
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
    print("Short Crowding Feasibility Probe")
    print("=" * 72)
    print(f"Symbols:     {', '.join(report.config.symbols)}")
    print(f"Timeframe:   {report.config.timeframe}")
    print(f"Exchange:    {report.config.exchange}")
    print(f"Window:      {report.config.start} -> {report.config.end}")
    print(f"Fee drag:    {report.config.round_trip_fee_pct:.3f}% round-trip")
    print()

    for summary in report.symbols:
        print(f"## {summary.symbol} ({summary.bar_count} bars)")
        print(
            f"Baseline short MAE: 12h={summary.baseline_mae_12h:.3f}% "
            f"24h={summary.baseline_mae_24h:.3f}%"
        )
        for scenario in summary.scenarios:
            if not scenario.events:
                continue
            stats_12 = scenario.stats_for(
                "12h", scenario.baseline_mae_12h, report.config.round_trip_fee_pct
            )
            stats_24 = scenario.stats_for(
                "24h", scenario.baseline_mae_24h, report.config.round_trip_fee_pct
            )
            print(
                f"  {scenario.kind} {scenario.metric} tail{scenario.tail_pct}% "
                f"n={len(scenario.events)} "
                f"short_fwd12={stats_12.mean_forward_pct:+.3f}% "
                f"(net {stats_12.mean_forward_after_fees_pct:+.3f}%) "
                f"short_fwd24={stats_24.mean_forward_pct:+.3f}% "
                f"(net {stats_24.mean_forward_after_fees_pct:+.3f}%) "
                f"mae12={stats_12.mean_mae_pct:.3f}% (Δ{stats_12.mae_improvement_pct:+.1f}%) "
                f"mae24={stats_24.mean_mae_pct:.3f}% (Δ{stats_24.mae_improvement_pct:+.1f}%) "
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
            "NO_PULSE": "No qualifying events — close short crowding lane.",
            "SPARSE": "Events below per-symbol floor — lane too sparse.",
            "WEAK_EDGE": "Events exist but fail short forward/MAE/concentration gates.",
            "HAS_PULSE": "At least one scenario passed — justify surface brief.",
        }
        print(messages.get(report.verdict, ""))


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(description="Probe short entries in perp crowding regimes.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--exchange", default="binance_usdm")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
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
        exchange=args.exchange,
        start=args.start,
        end=args.end,
        tail_pcts=(5, 10),
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
