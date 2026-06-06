#!/usr/bin/env python3
"""Cheap feasibility probe for range-break / structural continuation after confirmed sweeps or breakouts.

Read-only: ohlcv only. Tests whether a bar that sweeps the prior N-bar extreme *and closes outside*
(continuation, not the mean-reverting reject) produces tradable forward drift over 6h/12h/24h
with controlled MAE. This is the *opposite* surface from the liquidity-sweep (failed) probe.

See docs/specs/range-break-continuation-probe-v0.md and research-reset-2026-06-06.md (banned the mean-rev variant).
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

from src.db import close_pool, init_pool
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
    LONG = "long_break_continuation"
    SHORT = "short_break_continuation"


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


def detect_upside_break_continuations(
    bars: Sequence[LiquidityBar],
    *,
    config: ProbeConfig,
) -> list[SweepEvent]:
    """Confirmed upside range break (sweep high + close *outside* prior max) → long continuation."""
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
        if bar.close_price <= prior_high:  # must close *outside* (above) for continuation
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


def detect_downside_break_continuations(
    bars: Sequence[LiquidityBar],
    *,
    config: ProbeConfig,
) -> list[SweepEvent]:
    """Confirmed downside range break (sweep low + close *outside* prior min) → short continuation."""
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
        if bar.close_price >= prior_low:  # must close *outside* (below) for continuation
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
    return any(s.mae_improvement_pct >= min_improvement for s in (stats_6, stats_12, stats_24))


def concentration_passes(stats: HorizonStats, max_pct: float) -> bool:
    return stats.max_event_share_of_positive_pct <= max_pct


def month_passes(stats: HorizonStats, max_pct: float) -> bool:
    return stats.max_month_share_pct <= max_pct


def symbols_contradict(sides: Sequence[SideResult], *, min_events: int, threshold: float) -> bool:
    per_symbol: dict[str, float] = {}
    for side in sides:
        if side.events:
            pooled = side.stats_for("12h", 0.0, 0.0)
            if pooled.event_count >= min_events:
                per_symbol[side.symbol] = pooled.mean_forward_after_fees_pct
    if len(per_symbol) < 2:
        return False
    has_strong_pos = any(v > threshold for v in per_symbol.values())
    has_strong_neg = any(v < -threshold for v in per_symbol.values())
    return has_strong_pos and has_strong_neg


def evaluate_side(result: SideResult, *, config: ProbeConfig) -> bool:
    if not result.events:
        return False
    s6 = result.stats_for("6h", result.baseline_mae_6h, config.round_trip_fee_pct)
    s12 = result.stats_for("12h", result.baseline_mae_12h, config.round_trip_fee_pct)
    s24 = result.stats_for("24h", result.baseline_mae_24h, config.round_trip_fee_pct)
    if not forward_passes(s6, s12, s24, config.min_mean_forward_pct):
        return False
    if not mae_passes(s6, s12, s24, config.min_mae_improvement_pct):
        return False
    if not concentration_passes(s12, config.max_concentration_pct):
        return False
    if not month_passes(s12, config.max_month_share_pct):
        return False
    return True


def probe_symbol(
    bars: Sequence[LiquidityBar], symbol: str, config: ProbeConfig
) -> SymbolProbeSummary:
    long_events = detect_upside_break_continuations(bars, config=config)
    short_events = detect_downside_break_continuations(bars, config=config)

    long_res = SideResult(
        symbol,
        EventSide.LONG,
        tuple(long_events),
        compute_baseline_long_mae(bars, config.forward_bars_6h),
        compute_baseline_long_mae(bars, config.forward_bars_12h),
        compute_baseline_long_mae(bars, config.forward_bars_24h),
    )
    short_res = SideResult(
        symbol,
        EventSide.SHORT,
        tuple(short_events),
        compute_baseline_short_mae(bars, config.forward_bars_6h),
        compute_baseline_short_mae(bars, config.forward_bars_12h),
        compute_baseline_short_mae(bars, config.forward_bars_24h),
    )

    return SymbolProbeSummary(
        symbol=symbol,
        bar_count=len(bars),
        sides=(long_res, short_res),
        baseline_mae_6h_long=long_res.baseline_mae_6h,
        baseline_mae_12h_long=long_res.baseline_mae_12h,
        baseline_mae_24h_long=long_res.baseline_mae_24h,
        baseline_mae_6h_short=short_res.baseline_mae_6h,
        baseline_mae_12h_short=short_res.baseline_mae_12h,
        baseline_mae_24h_short=short_res.baseline_mae_24h,
    )


async def run_probe(config: ProbeConfig) -> ProbeReport:
    logger = configure_logger("probe.range_break_continuation")
    pool = await init_pool(build_db_config())
    try:
        summaries: list[SymbolProbeSummary] = []
        all_passing: list[str] = []

        for symbol in config.symbols:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    PROBE_QUERY,
                    symbol,
                    config.timeframe,
                    datetime.fromisoformat(config.start),
                    datetime.fromisoformat(config.end),
                )
            bars = [
                LiquidityBar(
                    time=r["time"],
                    open_price=float(r["open_price"]),
                    high_price=float(r["high_price"]),
                    low_price=float(r["low_price"]),
                    close_price=float(r["close_price"]),
                    volume=float(r["volume"]),
                )
                for r in rows
            ]
            if len(bars) < config.lookback_bars + 30:
                logger.warning("insufficient bars for %s (%d)", symbol, len(bars))
                continue

            summary = probe_symbol(bars, symbol, config)
            summaries.append(summary)

            for side_res in summary.sides:
                s12 = side_res.stats_for(
                    "12h",
                    getattr(
                        summary,
                        f"baseline_mae_12h_{'long' if side_res.side is EventSide.LONG else 'short'}",
                    ),
                    config.round_trip_fee_pct,
                )
                ok = evaluate_side(side_res, config=config)
                label = side_label(side_res)
                logger.info(
                    "%s events=%d fwd12=%.3f mae_imp12=%.1f%% ok=%s",
                    label,
                    len(side_res.events),
                    s12.mean_forward_after_fees_pct,
                    s12.mae_improvement_pct,
                    ok,
                )
                if ok:
                    all_passing.append(label)

        verdict = "HAS_PULSE" if all_passing else "WEAK_EDGE"
        if not any(len(s.sides[0].events) + len(s.sides[1].events) > 0 for s in summaries):
            verdict = "NO_PULSE"

        return ProbeReport(
            config=config,
            symbols=tuple(summaries),
            verdict=verdict,
            passing_scenarios=tuple(all_passing),
        )
    finally:
        await close_pool()


def default_config() -> ProbeConfig:
    return ProbeConfig(
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        lookback_bars=24,
        range_expansion_mult=1.2,
        volume_expansion_mult=1.2,
        forward_bars_6h=6,
        forward_bars_12h=12,
        forward_bars_24h=24,
        min_events_per_symbol=20,
        min_events_pooled=80,
        min_mean_forward_pct=0.15,
        round_trip_fee_pct=0.08,
        min_mae_improvement_pct=10.0,
        max_concentration_pct=50.0,
        max_month_share_pct=40.0,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append("# Range-Break Continuation Probe — Report")
    lines.append("")
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_range_break_continuation.py`")
    lines.append(
        "**Spec:** [range-break-continuation-probe-v0.md](../specs/range-break-continuation-probe-v0.md)"
    )
    lines.append("")
    lines.append("## Config")
    lines.append(f"- Symbols: {', '.join(report.config.symbols)}")
    lines.append(f"- Timeframe: {report.config.timeframe}")
    lines.append(f"- Window: {report.config.start} → {report.config.end}")
    lines.append(f"- Lookback: {report.config.lookback_bars} bars")
    lines.append(f"- Fee drag: {report.config.round_trip_fee_pct}% round-trip")
    lines.append("")
    lines.append("## Event counts (continuation breaks)")
    lines.append("")
    lines.append("| Symbol | Long (upside break cont.) | Short (downside break cont.) |")
    lines.append("|--------|---------------------------|------------------------------|")
    for sym in report.symbols:
        long_n = len(sym.sides[0].events) if sym.sides[0].side is EventSide.LONG else 0
        short_n = len(sym.sides[1].events) if sym.sides[1].side is EventSide.SHORT else 0
        lines.append(f"| {sym.symbol} | {long_n} | {short_n} |")
    lines.append("")
    lines.append("## Gate summary (per side)")
    lines.append("")
    lines.append(
        "| Scenario | Events | Fwd>0.15% net | MAE≥10% better | Conc≤50% | Month≤40% | Pass |"
    )
    lines.append(
        "|----------|--------|---------------|------------------|----------|-----------|------|"
    )
    for sym in report.symbols:
        for side_res in sym.sides:
            s12 = side_res.stats_for("12h", 0.0, report.config.round_trip_fee_pct)
            base_mae = s12.baseline_mae_pct
            s6 = side_res.stats_for("6h", base_mae, report.config.round_trip_fee_pct)
            s12 = side_res.stats_for("12h", base_mae, report.config.round_trip_fee_pct)
            s24 = side_res.stats_for("24h", base_mae, report.config.round_trip_fee_pct)
            ev = len(side_res.events)
            fwd_ok = forward_passes(s6, s12, s24, report.config.min_mean_forward_pct)
            mae_ok = mae_passes(s6, s12, s24, report.config.min_mae_improvement_pct)
            conc_ok = concentration_passes(s12, report.config.max_concentration_pct)
            mon_ok = month_passes(s12, report.config.max_month_share_pct)
            ok = evaluate_side(side_res, config=report.config)
            lines.append(
                f"| {side_label(side_res)} | {ev} | {fwd_ok} | {mae_ok} | {conc_ok} | {mon_ok} | {ok} |"
            )
    lines.append("")
    lines.append("## Passing scenarios")
    if report.passing_scenarios:
        for p in report.passing_scenarios:
            lines.append(f"- {p}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append(f"**Overall verdict:** {report.verdict}")
    lines.append("")
    lines.append("See research-reset-2026-06-06.md for banned surfaces and next-lane rules.")
    return "\n".join(lines)


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--print-report", action="store_true")
    args = parser.parse_args(argv)

    cfg = default_config()
    report = await run_probe(cfg)

    if args.json:
        import json

        payload = {
            "verdict": report.verdict,
            "passing": list(report.passing_scenarios),
            "symbols": [
                {
                    "symbol": s.symbol,
                    "long_events": len(s.sides[0].events),
                    "short_events": len(s.sides[1].events),
                }
                for s in report.symbols
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_report(report))

    if args.print_report:
        print("\n--- RAW ---\n")
        print(report)

    return 0 if report.verdict == "HAS_PULSE" else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
