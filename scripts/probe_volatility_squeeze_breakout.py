#!/usr/bin/env python3
"""Feasibility probe for volatility squeeze breakout (Option F).

Counts compression→expansion entry events and crude forward returns before
implementing a bounded autoresearch family. Default symbol order: BTC → ETH → SOL.

See docs/specs/volatility-squeeze-breakout-bounded-surface-v0.md
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class ProbeConfig:
    symbol: str
    timeframe: str
    start: str
    end: str
    squeeze_lookback: int
    squeeze_percentile: float
    momentum_period: int
    min_atr_pct: float
    forward_bars_12h: int
    forward_bars_24h: int
    forward_bars_48h: int
    min_events_for_pulse: int
    max_profit_concentration_pct: float


@dataclass(frozen=True)
class SqueezeEvent:
    time: datetime
    close_price: float
    bb_width_pct_rank: float
    momentum: float
    forward_12h_pct: float
    forward_24h_pct: float
    forward_48h_pct: float


@dataclass(frozen=True)
class HorizonStats:
    horizon_bars: int
    event_count: int
    mean_forward_pct: float
    median_forward_pct: float
    win_rate_pct: float
    max_event_share_of_positive_pct: float


@dataclass(frozen=True)
class ProbeSummary:
    symbol: str
    timeframe: str
    price_bars: int
    events: tuple[SqueezeEvent, ...]

    def stats_for(self, horizon: str) -> HorizonStats:
        if horizon == "12h":
            attr = "forward_12h_pct"
            bars = 12
        elif horizon == "24h":
            attr = "forward_24h_pct"
            bars = 24
        else:
            attr = "forward_48h_pct"
            bars = 48
        values = [getattr(event, attr) for event in self.events]
        if not values:
            return HorizonStats(bars, 0, 0.0, 0.0, 0.0, 0.0)
        positive = [value for value in values if value > 0]
        concentration = 0.0
        if positive:
            concentration = max(positive) / sum(positive) * 100.0
        wins = sum(1 for value in values if value > 0.0)
        return HorizonStats(
            horizon_bars=bars,
            event_count=len(values),
            mean_forward_pct=_mean(values),
            median_forward_pct=_median(values),
            win_rate_pct=wins / len(values) * 100.0,
            max_event_share_of_positive_pct=concentration,
        )


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


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _percentile_rank(history: list[float], current: float) -> float:
    if not history:
        return 1.0
    below = sum(1 for item in history if item < current)
    return below / len(history)


def _compute_momentum(close_history: list[float]) -> float:
    if len(close_history) < 2:
        return 0.0
    past = close_history[0]
    current = close_history[-1]
    if past <= 0:
        return 0.0
    return (current - past) / past


def _forward_return_pct(closes: Sequence[float], index: int, forward_bars: int) -> float | None:
    if index + forward_bars >= len(closes):
        return None
    current = closes[index]
    future = closes[index + forward_bars]
    if current <= 0:
        return None
    return (future / current - 1.0) * 100.0


def probe_squeeze_rows(rows: Sequence[Mapping[str, object]], config: ProbeConfig) -> ProbeSummary:
    events: list[SqueezeEvent] = []
    cooldown_until = -1
    max_forward = max(config.forward_bars_12h, config.forward_bars_24h, config.forward_bars_48h)
    closes = []
    times = []
    width_hist = deque(maxlen=config.squeeze_lookback)
    close_hist = deque(maxlen=config.momentum_period + 1)

    for row in rows:
        close = _coerce_float(row.get("close_price"))
        if close is None or close <= 0:
            continue
        bb_upper = _coerce_float(row.get("bb_upper_dist"))
        bb_lower = _coerce_float(row.get("bb_lower_dist"))
        atr_pct = _coerce_float(row.get("atr_pct"))
        sma_20 = _coerce_float(row.get("sma_20"))
        if None in {bb_upper, bb_lower, atr_pct, sma_20}:
            closes.append(close)
            times.append(row["time"])
            continue

        bb_width = (bb_upper + bb_lower) / close
        width_hist.append(bb_width)
        close_hist.append(close)
        closes.append(close)
        times.append(row["time"])
        index = len(closes) - 1

        if index <= cooldown_until:
            continue
        if index + max_forward >= len(closes):
            continue

        if len(width_hist) < min(config.squeeze_lookback, 10):
            continue
        if len(close_hist) < config.momentum_period + 1:
            continue

        pct_rank = _percentile_rank(list(width_hist), bb_width)
        momentum = _compute_momentum(list(close_hist))
        if not (
            pct_rank < config.squeeze_percentile
            and close > sma_20
            and momentum > 0
            and atr_pct >= config.min_atr_pct
        ):
            continue

        forward_12 = _forward_return_pct(closes, index, config.forward_bars_12h)
        forward_24 = _forward_return_pct(closes, index, config.forward_bars_24h)
        forward_48 = _forward_return_pct(closes, index, config.forward_bars_48h)
        if forward_12 is None or forward_24 is None or forward_48 is None:
            continue

        events.append(
            SqueezeEvent(
                time=times[index],
                close_price=close,
                bb_width_pct_rank=pct_rank,
                momentum=momentum,
                forward_12h_pct=forward_12,
                forward_24h_pct=forward_24,
                forward_48h_pct=forward_48,
            )
        )
        cooldown_until = index + max_forward

    return ProbeSummary(
        symbol=config.symbol,
        timeframe=config.timeframe,
        price_bars=len(rows),
        events=tuple(events),
    )


def evaluate_pulse(summary: ProbeSummary, config: ProbeConfig) -> str:
    if not summary.events:
        return "NO_PULSE"
    if len(summary.events) < config.min_events_for_pulse:
        return "SPARSE"
    stats_12 = summary.stats_for("12h")
    stats_24 = summary.stats_for("24h")
    stats_48 = summary.stats_for("48h")
    has_positive = (
        stats_12.mean_forward_pct > 0
        or stats_24.mean_forward_pct > 0
        or stats_48.mean_forward_pct > 0
    )
    concentration_ok = all(
        stats.max_event_share_of_positive_pct <= config.max_profit_concentration_pct
        for stats in (stats_12, stats_24, stats_48)
    )
    if not has_positive:
        return "WEAK_EDGE"
    if not concentration_ok:
        return "CONCENTRATED"
    return "HAS_PULSE"


def print_summary(summary: ProbeSummary, config: ProbeConfig) -> None:
    verdict = evaluate_pulse(summary, config)
    rate = len(summary.events) / summary.price_bars * 100.0 if summary.price_bars else 0.0
    print("Volatility Squeeze Breakout Feasibility Probe")
    print("=" * 52)
    print(f"Symbol:      {summary.symbol}")
    print(f"Timeframe:   {summary.timeframe}")
    print(f"Window:      {config.start} -> {config.end}")
    print(f"Price bars:  {summary.price_bars}")
    print(f"Events:      {len(summary.events)} ({rate:.2f}% of bars)")
    print(
        f"Params:      lookback={config.squeeze_lookback} "
        f"pct<{config.squeeze_percentile:.2f} mom={config.momentum_period} "
        f"min_atr%={config.min_atr_pct:.4f}"
    )
    print()
    for label in ("12h", "24h", "48h"):
        stats = summary.stats_for(label)
        print(f"Horizon {label} ({stats.horizon_bars} bars)")
        print("-" * 52)
        print(f"  Mean forward:  {stats.mean_forward_pct:.2f}%")
        print(f"  Median:        {stats.median_forward_pct:.2f}%")
        print(f"  Win rate:      {stats.win_rate_pct:.1f}%")
        print(
            f"  Max event share: {stats.max_event_share_of_positive_pct:.1f}% "
            f"(cap {config.max_profit_concentration_pct:.0f}%)"
        )
        print()
    messages = {
        "NO_PULSE": "Verdict: NO PULSE - zero squeeze breakout events.",
        "SPARSE": "Verdict: SPARSE - reshape parameters before implementation.",
        "WEAK_EDGE": "Verdict: WEAK EDGE - events exist but forward returns non-positive.",
        "CONCENTRATED": "Verdict: CONCENTRATED - edge dominated by one event.",
        "HAS_PULSE": "Verdict: HAS PULSE - justify bounded squeeze autoresearch.",
    }
    print(messages[verdict])


async def run_probe(config: ProbeConfig) -> ProbeSummary:
    db_config = build_db_config()
    await init_pool(db_config)
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            rows = await reader.fetch_range(
                config.symbol,
                config.timeframe,
                config.start,
                config.end,
            )
    finally:
        await close_pool()
    return probe_squeeze_rows(rows, config)


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(description="Probe volatility squeeze breakout feasibility.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--squeeze-lookback", type=int, default=50)
    parser.add_argument("--squeeze-percentile", type=float, default=0.20)
    parser.add_argument("--momentum-period", type=int, default=10)
    parser.add_argument("--min-atr-pct", type=float, default=0.005)
    parser.add_argument("--min-events", type=int, default=20)
    parser.add_argument("--max-concentration-pct", type=float, default=30.0)
    args = parser.parse_args(argv)
    return ProbeConfig(
        symbol=args.symbol.upper(),
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        squeeze_lookback=args.squeeze_lookback,
        squeeze_percentile=args.squeeze_percentile,
        momentum_period=args.momentum_period,
        min_atr_pct=args.min_atr_pct,
        forward_bars_12h=12,
        forward_bars_24h=24,
        forward_bars_48h=48,
        min_events_for_pulse=args.min_events,
        max_profit_concentration_pct=args.max_concentration_pct,
    )


async def _main() -> int:
    configure_logger("WARNING")
    config = parse_args()
    summary = await run_probe(config)
    print_summary(summary, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
