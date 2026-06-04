#!/usr/bin/env python3
"""Cheap feasibility probe for funding-normalization primary surface.

Answers before full implementation (see docs/specs/funding-crowding-primary-surface-v0.md):
- How often does funding normalize from an extreme?
- After normalization, are 12h/24h forward returns positive net of funding drag?
- Is edge concentrated in one event?
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from src.db import close_pool, get_pool, init_pool
from src.features.reader import IndicatorReader
from src.utils.logger import configure_logger


class FundingState(StrEnum):
    IDLE = "idle"
    EXTREME_NEGATIVE = "extreme_negative"
    COOLDOWN_NEGATIVE = "cooldown_negative"
    EXTREME_POSITIVE = "extreme_positive"
    COOLDOWN_POSITIVE = "cooldown_positive"


class NormalizationSide(StrEnum):
    LONG_FROM_NEGATIVE = "long_from_negative"
    SHORT_FROM_POSITIVE = "short_from_positive"


@dataclass(frozen=True)
class ProbeConfig:
    symbol: str
    timeframe: str
    start: str
    end: str
    entry_threshold: float
    exit_threshold: float
    forward_bars_12h: int
    forward_bars_24h: int
    min_events_for_pulse: int
    max_profit_concentration_pct: float
    long_only: bool


@dataclass(frozen=True)
class FundingTick:
    time: datetime
    funding_rate: float


@dataclass(frozen=True)
class NormalizationEvent:
    time: datetime
    side: NormalizationSide
    funding_rate: float
    prior_extreme_rate: float
    gross_forward_12h_pct: float
    gross_forward_24h_pct: float
    funding_drag_12h_pct: float
    funding_drag_24h_pct: float

    @property
    def net_forward_12h_pct(self) -> float:
        return self._net(self.gross_forward_12h_pct, self.funding_drag_12h_pct)

    @property
    def net_forward_24h_pct(self) -> float:
        return self._net(self.gross_forward_24h_pct, self.funding_drag_24h_pct)

    def _net(self, gross: float, drag: float) -> float:
        if self.side == NormalizationSide.LONG_FROM_NEGATIVE:
            return gross - drag
        return gross + drag


@dataclass(frozen=True)
class HorizonStats:
    horizon_bars: int
    event_count: int
    mean_gross_pct: float
    mean_net_pct: float
    median_net_pct: float
    win_rate_pct: float
    max_event_share_of_positive_net_pct: float


@dataclass(frozen=True)
class ProbeSummary:
    symbol: str
    timeframe: str
    funding_ticks: int
    price_bars: int
    events: tuple[NormalizationEvent, ...]

    @property
    def long_events(self) -> tuple[NormalizationEvent, ...]:
        return tuple(e for e in self.events if e.side == NormalizationSide.LONG_FROM_NEGATIVE)

    def stats_for(self, horizon: str) -> HorizonStats:
        if horizon == "12h":
            bars = 12
            gross_attr = "gross_forward_12h_pct"
            net_prop = "net_forward_12h_pct"
        else:
            bars = 24
            gross_attr = "gross_forward_24h_pct"
            net_prop = "net_forward_24h_pct"

        events = self.long_events
        if not events:
            return HorizonStats(bars, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

        gross_vals = [getattr(e, gross_attr) for e in events]
        net_vals = [getattr(e, net_prop) for e in events]
        positive = [v for v in net_vals if v > 0]
        concentration = 0.0
        if positive:
            concentration = max(positive) / sum(positive) * 100.0

        wins = sum(1 for v in net_vals if v > 0.0)
        return HorizonStats(
            horizon_bars=bars,
            event_count=len(events),
            mean_gross_pct=_mean(gross_vals),
            mean_net_pct=_mean(net_vals),
            median_net_pct=_median(net_vals),
            win_rate_pct=wins / len(events) * 100.0,
            max_event_share_of_positive_net_pct=concentration,
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


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(
        description="Probe funding normalization frequency and crude net forward edge."
    )
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--entry-threshold", type=float, default=0.0005)
    parser.add_argument("--exit-threshold", type=float, default=0.00015)
    parser.add_argument("--forward-bars-12h", type=int, default=12)
    parser.add_argument("--forward-bars-24h", type=int, default=24)
    parser.add_argument("--min-events", type=int, default=20)
    parser.add_argument("--max-concentration-pct", type=float, default=30.0)
    parser.add_argument(
        "--include-short-events",
        action="store_true",
        help="Count positive-normalization short signals (research only)",
    )
    args = parser.parse_args(argv)
    return ProbeConfig(
        symbol=args.symbol.upper(),
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        entry_threshold=args.entry_threshold,
        exit_threshold=args.exit_threshold,
        forward_bars_12h=args.forward_bars_12h,
        forward_bars_24h=args.forward_bars_24h,
        min_events_for_pulse=args.min_events,
        max_profit_concentration_pct=args.max_concentration_pct,
        long_only=not args.include_short_events,
    )


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


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def index_price_bars(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[datetime], list[float]]:
    times = [_parse_dt(row["time"]) for row in rows]
    closes = [float(row["close_price"]) for row in rows]
    return times, closes


def _forward_return_pct(closes: Sequence[float], index: int, forward_bars: int) -> float | None:
    if index + forward_bars >= len(closes):
        return None
    current = closes[index]
    future = closes[index + forward_bars]
    if current <= 0:
        return None
    return (future / current - 1.0) * 100.0


def _find_price_index(price_times: Sequence[datetime], event_time: datetime) -> int | None:
    for index, bar_time in enumerate(price_times):
        if bar_time >= event_time:
            return index
    return None


def _funding_drag_pct(
    funding_ticks: Sequence[FundingTick],
    event_index: int,
    event_time: datetime,
    end_time: datetime,
    side: NormalizationSide,
) -> float:
    """Sum funding payments during hold window; long pays positive rate."""
    drag = 0.0
    for tick in funding_ticks[event_index:]:
        if tick.time <= event_time:
            continue
        if tick.time > end_time:
            break
        if side == NormalizationSide.LONG_FROM_NEGATIVE:
            drag += tick.funding_rate
        else:
            drag -= tick.funding_rate
    return drag * 100.0


def detect_normalization_events(
    funding_ticks: Sequence[FundingTick],
    price_times: Sequence[datetime],
    price_closes: Sequence[float],
    config: ProbeConfig,
) -> list[NormalizationEvent]:
    """Detect one entry per extreme→normalized cycle; long-only by default."""
    events: list[NormalizationEvent] = []
    state = FundingState.IDLE
    prior_extreme_rate = 0.0
    max_forward = max(config.forward_bars_12h, config.forward_bars_24h)

    for index, tick in enumerate(funding_ticks):
        rate = tick.funding_rate
        normalized = abs(rate) < config.exit_threshold

        if rate <= -config.entry_threshold:
            if state in {FundingState.IDLE, FundingState.COOLDOWN_NEGATIVE}:
                state = FundingState.EXTREME_NEGATIVE
                prior_extreme_rate = rate
            elif state == FundingState.EXTREME_NEGATIVE:
                prior_extreme_rate = min(prior_extreme_rate, rate)
            continue

        if state == FundingState.EXTREME_NEGATIVE and normalized:
            side = NormalizationSide.LONG_FROM_NEGATIVE
            event = _build_event(
                tick,
                side,
                prior_extreme_rate,
                index,
                funding_ticks,
                price_times,
                price_closes,
                config,
                max_forward,
            )
            if event is not None:
                events.append(event)
            state = FundingState.COOLDOWN_NEGATIVE
            continue

        if not config.long_only:
            if rate >= config.entry_threshold:
                if state in {FundingState.IDLE, FundingState.COOLDOWN_POSITIVE}:
                    state = FundingState.EXTREME_POSITIVE
                    prior_extreme_rate = rate
                elif state == FundingState.EXTREME_POSITIVE:
                    prior_extreme_rate = max(prior_extreme_rate, rate)
                continue

            if state == FundingState.EXTREME_POSITIVE and normalized:
                side = NormalizationSide.SHORT_FROM_POSITIVE
                event = _build_event(
                    tick,
                    side,
                    prior_extreme_rate,
                    index,
                    funding_ticks,
                    price_times,
                    price_closes,
                    config,
                    max_forward,
                )
                if event is not None:
                    events.append(event)
                state = FundingState.COOLDOWN_POSITIVE
                continue

        if normalized and state not in {
            FundingState.EXTREME_NEGATIVE,
            FundingState.EXTREME_POSITIVE,
        }:
            state = FundingState.IDLE

    return events


def _build_event(
    tick: FundingTick,
    side: NormalizationSide,
    prior_extreme_rate: float,
    funding_index: int,
    funding_ticks: Sequence[FundingTick],
    price_times: Sequence[datetime],
    price_closes: Sequence[float],
    config: ProbeConfig,
    max_forward: int,
) -> NormalizationEvent | None:
    price_index = _find_price_index(price_times, tick.time)
    if price_index is None:
        return None

    gross_12 = _forward_return_pct(price_closes, price_index, config.forward_bars_12h)
    gross_24 = _forward_return_pct(price_closes, price_index, config.forward_bars_24h)
    if gross_12 is None or gross_24 is None:
        return None

    # Hold window follows bar count on the probe timeframe (1h bars => hours).
    end_12 = tick.time + timedelta(hours=config.forward_bars_12h)
    end_24 = tick.time + timedelta(hours=config.forward_bars_24h)
    drag_12 = _funding_drag_pct(funding_ticks, funding_index, tick.time, end_12, side)
    drag_24 = _funding_drag_pct(funding_ticks, funding_index, tick.time, end_24, side)

    return NormalizationEvent(
        time=tick.time,
        side=side,
        funding_rate=tick.funding_rate,
        prior_extreme_rate=prior_extreme_rate,
        gross_forward_12h_pct=gross_12,
        gross_forward_24h_pct=gross_24,
        funding_drag_12h_pct=drag_12,
        funding_drag_24h_pct=drag_24,
    )


def probe_funding_series(
    funding_ticks: Sequence[FundingTick],
    price_rows: Sequence[Mapping[str, object]],
    config: ProbeConfig,
) -> ProbeSummary:
    price_times, price_closes = index_price_bars(price_rows)
    events = detect_normalization_events(funding_ticks, price_times, price_closes, config)
    if config.long_only:
        events = [event for event in events if event.side == NormalizationSide.LONG_FROM_NEGATIVE]
    return ProbeSummary(
        symbol=config.symbol,
        timeframe=config.timeframe,
        funding_ticks=len(funding_ticks),
        price_bars=len(price_rows),
        events=tuple(events),
    )


async def _fetch_funding_ticks(symbol: str, start: str, end: str) -> list[FundingTick]:
    pool = get_pool()
    query = """
        SELECT funding_time, funding_rate
        FROM funding_rates
        WHERE symbol = $1
          AND funding_time >= $2
          AND funding_time <= $3
        ORDER BY funding_time ASC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            query,
            symbol,
            _parse_dt(start),
            _parse_dt(end),
        )
    return [
        FundingTick(time=row["funding_time"], funding_rate=float(row["funding_rate"]))
        for row in rows
    ]


async def run_probe(config: ProbeConfig) -> ProbeSummary:
    db_config = build_db_config()
    await init_pool(db_config)
    try:
        funding_ticks = await _fetch_funding_ticks(config.symbol, config.start, config.end)
        reader = IndicatorReader(db_config)
        async with reader:
            price_rows = await reader.fetch_range(
                config.symbol,
                config.timeframe,
                config.start,
                config.end,
            )
    finally:
        await close_pool()

    return probe_funding_series(funding_ticks, price_rows, config)


def evaluate_pulse(summary: ProbeSummary, config: ProbeConfig) -> str:
    long_count = len(summary.long_events)
    if long_count == 0:
        return "NO_PULSE"
    if long_count < config.min_events_for_pulse:
        return "SPARSE"

    stats_12 = summary.stats_for("12h")
    stats_24 = summary.stats_for("24h")
    has_positive_horizon = stats_12.mean_net_pct > 0 or stats_24.mean_net_pct > 0
    concentration_ok = (
        stats_12.max_event_share_of_positive_net_pct <= config.max_profit_concentration_pct
        and stats_24.max_event_share_of_positive_net_pct <= config.max_profit_concentration_pct
    )

    if not has_positive_horizon:
        return "WEAK_EDGE"
    if not concentration_ok:
        return "CONCENTRATED"
    return "HAS_PULSE"


def print_summary(summary: ProbeSummary, config: ProbeConfig) -> None:
    verdict = evaluate_pulse(summary, config)
    long_events = summary.long_events
    rate_pct = len(long_events) / summary.funding_ticks * 100.0 if summary.funding_ticks else 0.0

    print("Funding Normalization Feasibility Probe")
    print("=" * 52)
    print(f"Symbol:           {summary.symbol}")
    print(f"Timeframe:        {summary.timeframe}")
    print(f"Window:           {config.start} -> {config.end}")
    print(f"Funding ticks:    {summary.funding_ticks}")
    print(f"Price bars:       {summary.price_bars}")
    print(f"Long norm events: {len(long_events)} ({rate_pct:.2f}% of funding ticks)")
    print(f"Thresholds:       entry={config.entry_threshold:.5f} exit={config.exit_threshold:.5f}")
    print()

    for label in ("12h", "24h"):
        stats = summary.stats_for(label)
        print(f"Horizon {label} ({stats.horizon_bars} bars)")
        print("-" * 52)
        print(f"  Mean gross:        {stats.mean_gross_pct:.2f}%")
        print(f"  Mean net:          {stats.mean_net_pct:.2f}%")
        print(f"  Median net:        {stats.median_net_pct:.2f}%")
        print(f"  Win rate (net>0):  {stats.win_rate_pct:.1f}%")
        print(
            f"  Max event share:   {stats.max_event_share_of_positive_net_pct:.1f}% "
            f"(cap {config.max_profit_concentration_pct:.0f}%)"
        )
        print()

    messages = {
        "NO_PULSE": "Verdict: NO PULSE - zero normalization events.",
        "SPARSE": "Verdict: SPARSE - reshape thresholds before implementation.",
        "WEAK_EDGE": "Verdict: WEAK EDGE - events exist but net forward edge non-positive.",
        "CONCENTRATED": "Verdict: CONCENTRATED - edge dominated by one event.",
        "HAS_PULSE": "Verdict: HAS PULSE - justify funding_normalization implementation.",
    }
    print(messages[verdict])


async def _main() -> int:
    configure_logger("WARNING")
    config = parse_args()
    summary = await run_probe(config)
    print_summary(summary, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
