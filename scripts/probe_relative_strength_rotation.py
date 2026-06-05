#!/usr/bin/env python3
"""Cheap feasibility probe for the relative-strength rotation surface.

This intentionally avoids strategy registration, BacktestEngine plumbing, and
autoresearch candidate generation. It answers whether the ETH/BTC relative
strength idea has enough signal frequency and crude forward edge to justify the
full implementation described in:

docs/specs/relative-strength-rotation-surface-v0.md
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class ProbeConfig:
    target_symbol: str
    anchor_symbol: str
    timeframe: str
    start: str
    end: str
    fast_lookback_bars: int
    slow_lookback_bars: int
    forward_bars: int
    min_fast_rs_pct: float
    min_slow_rs_pct: float
    max_rs_deterioration_pct: float
    max_pullback_distance_pct: float
    rsi_reset_min: float
    rsi_reset_max: float
    anchor_max_fast_loss_pct: float
    anchor_min_ema200_distance_pct: float


@dataclass(frozen=True)
class ProbeEvent:
    time: datetime
    target_close: float
    anchor_close: float
    rs_fast_pct: float
    rs_slow_pct: float
    rs_deterioration_pct: float
    target_forward_return_pct: float
    anchor_forward_return_pct: float
    excess_forward_return_pct: float


@dataclass(frozen=True)
class ProbeSummary:
    target_symbol: str
    anchor_symbol: str
    timeframe: str
    target_rows: int
    anchor_rows: int
    aligned_rows: int
    events: tuple[ProbeEvent, ...]

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def event_rate_pct(self) -> float:
        if self.aligned_rows == 0:
            return 0.0
        return self.event_count / self.aligned_rows * 100.0

    @property
    def mean_forward_return_pct(self) -> float:
        return _mean(event.target_forward_return_pct for event in self.events)

    @property
    def median_forward_return_pct(self) -> float:
        return _median(event.target_forward_return_pct for event in self.events)

    @property
    def mean_excess_forward_return_pct(self) -> float:
        return _mean(event.excess_forward_return_pct for event in self.events)

    @property
    def median_excess_forward_return_pct(self) -> float:
        return _median(event.excess_forward_return_pct for event in self.events)

    @property
    def win_rate_pct(self) -> float:
        if not self.events:
            return 0.0
        wins = sum(1 for event in self.events if event.target_forward_return_pct > 0.0)
        return wins / len(self.events) * 100.0

    @property
    def excess_win_rate_pct(self) -> float:
        if not self.events:
            return 0.0
        wins = sum(1 for event in self.events if event.excess_forward_return_pct > 0.0)
        return wins / len(self.events) * 100.0


def build_db_config(env: Mapping[str, str] | None = None) -> dict[str, object]:
    source = env or os.environ
    return {
        "host": source.get("DB_HOST", "localhost"),
        "port": int(source.get("DB_PORT", 15432)),
        "name": source.get("DB_NAME", "marketdata"),
        "user": source.get("DB_USER", "trading"),
        "password": source.get("DB_PASSWORD", source.get("POSTGRES_PASSWORD", "change_me")),
    }


def parse_args(argv: Sequence[str] | None = None) -> ProbeConfig:
    parser = argparse.ArgumentParser(
        description="Probe relative-strength rotation frequency and crude forward edge."
    )
    parser.add_argument("--target-symbol", default="ETHUSDT")
    parser.add_argument("--anchor-symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01T00:00:00")
    parser.add_argument("--end", default="2026-06-01T00:00:00")
    parser.add_argument("--fast-lookback-bars", type=int, default=6)
    parser.add_argument("--slow-lookback-bars", type=int, default=24)
    parser.add_argument("--forward-bars", type=int, default=12)
    parser.add_argument("--min-fast-rs-pct", type=float, default=0.8)
    parser.add_argument("--min-slow-rs-pct", type=float, default=2.0)
    parser.add_argument("--max-rs-deterioration-pct", type=float, default=-1.5)
    parser.add_argument("--max-pullback-distance-pct", type=float, default=2.0)
    parser.add_argument("--rsi-reset-min", type=float, default=35.0)
    parser.add_argument("--rsi-reset-max", type=float, default=60.0)
    parser.add_argument("--anchor-max-fast-loss-pct", type=float, default=3.0)
    parser.add_argument("--anchor-min-ema200-distance-pct", type=float, default=-2.0)
    args = parser.parse_args(argv)

    return ProbeConfig(
        target_symbol=args.target_symbol.upper(),
        anchor_symbol=args.anchor_symbol.upper(),
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        fast_lookback_bars=args.fast_lookback_bars,
        slow_lookback_bars=args.slow_lookback_bars,
        forward_bars=args.forward_bars,
        min_fast_rs_pct=args.min_fast_rs_pct,
        min_slow_rs_pct=args.min_slow_rs_pct,
        max_rs_deterioration_pct=args.max_rs_deterioration_pct,
        max_pullback_distance_pct=args.max_pullback_distance_pct,
        rsi_reset_min=args.rsi_reset_min,
        rsi_reset_max=args.rsi_reset_max,
        anchor_max_fast_loss_pct=args.anchor_max_fast_loss_pct,
        anchor_min_ema200_distance_pct=args.anchor_min_ema200_distance_pct,
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


def _return_pct(rows: Sequence[Mapping[str, object]], index: int, lookback: int) -> float | None:
    if index - lookback < 0:
        return None
    previous = float(rows[index - lookback]["close_price"])
    current = float(rows[index]["close_price"])
    if previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _forward_return_pct(
    rows: Sequence[Mapping[str, object]],
    index: int,
    forward_bars: int,
) -> float | None:
    if index + forward_bars >= len(rows):
        return None
    current = float(rows[index]["close_price"])
    future = float(rows[index + forward_bars]["close_price"])
    if current <= 0:
        return None
    return (future / current - 1.0) * 100.0


def _distance_pct(price: float, reference: object) -> float | None:
    if reference is None:
        return None
    reference_price = float(reference)
    if reference_price <= 0:
        return None
    return (price / reference_price - 1.0) * 100.0


def align_same_timestamp_rows(
    target_rows: Sequence[Mapping[str, object]],
    anchor_rows: Sequence[Mapping[str, object]],
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
    """Align same-timeframe rows by exact closed candle timestamp."""
    anchor_by_time = {row["time"]: row for row in anchor_rows}
    aligned_target: list[Mapping[str, object]] = []
    aligned_anchor: list[Mapping[str, object]] = []

    for target_row in target_rows:
        anchor_row = anchor_by_time.get(target_row["time"])
        if anchor_row is None:
            continue
        aligned_target.append(target_row)
        aligned_anchor.append(anchor_row)

    return aligned_target, aligned_anchor


def is_rotation_probe_event(
    target_rows: Sequence[Mapping[str, object]],
    anchor_rows: Sequence[Mapping[str, object]],
    index: int,
    config: ProbeConfig,
) -> tuple[bool, dict[str, float]]:
    target_fast = _return_pct(target_rows, index, config.fast_lookback_bars)
    anchor_fast = _return_pct(anchor_rows, index, config.fast_lookback_bars)
    target_slow = _return_pct(target_rows, index, config.slow_lookback_bars)
    anchor_slow = _return_pct(anchor_rows, index, config.slow_lookback_bars)
    if None in {target_fast, anchor_fast, target_slow, anchor_slow}:
        return False, {}

    assert target_fast is not None
    assert anchor_fast is not None
    assert target_slow is not None
    assert anchor_slow is not None

    rs_fast = target_fast - anchor_fast
    rs_slow = target_slow - anchor_slow
    rs_deterioration = rs_fast - rs_slow

    target_row = target_rows[index]
    anchor_row = anchor_rows[index]
    target_close = float(target_row["close_price"])
    anchor_close = float(anchor_row["close_price"])

    pullback_reference = target_row.get("vwap") or target_row.get("ema_50")
    pullback_distance = _distance_pct(target_close, pullback_reference)
    target_ema50_distance = _distance_pct(target_close, target_row.get("ema_50"))
    anchor_ema200_distance = _distance_pct(anchor_close, anchor_row.get("ema_200"))
    rsi_14 = target_row.get("rsi_14")

    if pullback_distance is None or target_ema50_distance is None:
        return False, {}
    if anchor_ema200_distance is None or rsi_14 is None:
        return False, {}

    anchor_non_panic = (
        anchor_fast >= -config.anchor_max_fast_loss_pct
        and anchor_ema200_distance >= config.anchor_min_ema200_distance_pct
    )
    rs_persistent = rs_fast >= config.min_fast_rs_pct and rs_slow >= config.min_slow_rs_pct
    rs_not_collapsing = rs_deterioration >= config.max_rs_deterioration_pct
    controlled_pullback = abs(pullback_distance) <= config.max_pullback_distance_pct
    rsi_reset = config.rsi_reset_min <= float(rsi_14) <= config.rsi_reset_max
    pullback_resolution = target_close >= float(target_row["ema_50"])

    passed = (
        anchor_non_panic
        and rs_persistent
        and rs_not_collapsing
        and controlled_pullback
        and rsi_reset
        and pullback_resolution
    )
    metrics = {
        "target_fast_return_pct": target_fast,
        "anchor_fast_return_pct": anchor_fast,
        "target_slow_return_pct": target_slow,
        "anchor_slow_return_pct": anchor_slow,
        "rs_fast_pct": rs_fast,
        "rs_slow_pct": rs_slow,
        "rs_deterioration_pct": rs_deterioration,
        "pullback_distance_pct": pullback_distance,
        "target_ema50_distance_pct": target_ema50_distance,
        "anchor_ema200_distance_pct": anchor_ema200_distance,
    }
    return passed, metrics


def probe_rows(
    target_rows: Sequence[Mapping[str, object]],
    anchor_rows: Sequence[Mapping[str, object]],
    config: ProbeConfig,
) -> ProbeSummary:
    aligned_target, aligned_anchor = align_same_timestamp_rows(target_rows, anchor_rows)
    events: list[ProbeEvent] = []
    start_index = max(config.fast_lookback_bars, config.slow_lookback_bars)
    stop_index = len(aligned_target) - config.forward_bars

    for index in range(start_index, max(start_index, stop_index)):
        passed, metrics = is_rotation_probe_event(aligned_target, aligned_anchor, index, config)
        if not passed:
            continue

        target_forward = _forward_return_pct(aligned_target, index, config.forward_bars)
        anchor_forward = _forward_return_pct(aligned_anchor, index, config.forward_bars)
        if target_forward is None or anchor_forward is None:
            continue

        events.append(
            ProbeEvent(
                time=aligned_target[index]["time"],
                target_close=float(aligned_target[index]["close_price"]),
                anchor_close=float(aligned_anchor[index]["close_price"]),
                rs_fast_pct=metrics["rs_fast_pct"],
                rs_slow_pct=metrics["rs_slow_pct"],
                rs_deterioration_pct=metrics["rs_deterioration_pct"],
                target_forward_return_pct=target_forward,
                anchor_forward_return_pct=anchor_forward,
                excess_forward_return_pct=target_forward - anchor_forward,
            )
        )

    return ProbeSummary(
        target_symbol=config.target_symbol,
        anchor_symbol=config.anchor_symbol,
        timeframe=config.timeframe,
        target_rows=len(target_rows),
        anchor_rows=len(anchor_rows),
        aligned_rows=len(aligned_target),
        events=tuple(events),
    )


async def run_probe(config: ProbeConfig) -> ProbeSummary:
    db_config = build_db_config()
    await init_pool(db_config)
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            target_rows = await reader.fetch_range(
                config.target_symbol,
                config.timeframe,
                config.start,
                config.end,
            )
            anchor_rows = await reader.fetch_range(
                config.anchor_symbol,
                config.timeframe,
                config.start,
                config.end,
            )
    finally:
        await close_pool()

    return probe_rows(target_rows, anchor_rows, config)


def print_summary(summary: ProbeSummary, config: ProbeConfig) -> None:
    print("Relative Strength Rotation Feasibility Probe")
    print("=" * 52)
    print(f"Target / anchor: {summary.target_symbol} / {summary.anchor_symbol}")
    print(f"Timeframe:        {summary.timeframe}")
    print(f"Window:           {config.start} -> {config.end}")
    print(f"Rows:             target={summary.target_rows} anchor={summary.anchor_rows}")
    print(f"Aligned rows:     {summary.aligned_rows}")
    print(f"Events:           {summary.event_count} ({summary.event_rate_pct:.2f}% of rows)")
    print()
    print("Forward edge after event")
    print("-" * 52)
    print(f"Forward bars:          {config.forward_bars}")
    print(f"Mean target return:    {summary.mean_forward_return_pct:.2f}%")
    print(f"Median target return:  {summary.median_forward_return_pct:.2f}%")
    print(f"Target win rate:       {summary.win_rate_pct:.1f}%")
    print(f"Mean excess vs anchor: {summary.mean_excess_forward_return_pct:.2f}%")
    print(f"Median excess:         {summary.median_excess_forward_return_pct:.2f}%")
    print(f"Excess win rate:       {summary.excess_win_rate_pct:.1f}%")
    print()

    if not summary.events:
        print("Verdict: NO PULSE - preconditions produced zero events.")
        return

    if summary.event_count < 20:
        print("Verdict: SPARSE - inspect thresholds before implementing the full surface.")
    elif summary.mean_excess_forward_return_pct <= 0:
        print("Verdict: WEAK EDGE - frequency exists, but crude forward excess is non-positive.")
    else:
        print("Verdict: HAS PULSE - frequency and crude forward excess justify deeper work.")


async def _main() -> int:
    configure_logger("WARNING")
    config = parse_args()
    summary = await run_probe(config)
    print_summary(summary, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
