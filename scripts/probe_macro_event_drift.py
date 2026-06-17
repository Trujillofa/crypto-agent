#!/usr/bin/env python3
"""Cheap feasibility probe for scheduled US macro-event forward drift (Gate 1).

Step 0: data-feasibility audit on frozen FOMC/CPI/NFP release timestamps.
Step 1: align events to BTC/ETH/SOL 1h OHLCV; measure H1 (directional drift)
and H2 (volatility elevation) vs matched random-window baseline.

Read-only against prod ohlcv. See docs/specs/macro-event-drift-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_higher_tf_trend_following import build_db_config
from src.db import close_pool, init_pool
from src.utils.logger import configure_logger, get_logger

DEFAULT_EVENTS_CSV = (
    Path(__file__).resolve().parent.parent / "data/macro_events/us_macro_releases.csv"
)

PROBE_QUERY = """
    SELECT time, close_price
    FROM ohlcv
    WHERE symbol = $1
      AND timeframe = $2
      AND time >= $3
      AND time <= $4
    ORDER BY time ASC
"""

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
MIN_USABLE_EVENTS = 50
HORIZONS_HOURS = (6, 24, 72)
BASELINE_VOL_BARS = 72
EVENT_EXCLUSION_HOURS = 48
DIRECTIONAL_CONSISTENCY_PCT = 60.0
FEE_NOISE_BAR_PCT = 0.3
MIN_SYMBOLS_H1_PASS = 2
H2_VOL_RATIO_THRESHOLD = 1.0
H2_VOL_ELEVATED_FRACTION = 0.5
RANDOM_BASELINE_SEED = 42


@dataclass(frozen=True)
class MacroEvent:
    event_type: str
    release_date_et: str
    release_ts: datetime
    source: str


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    timeframe: str
    start: str
    end: str
    events_csv: Path
    horizons_hours: tuple[int, ...]
    baseline_vol_bars: int
    event_exclusion_hours: int
    one_way_fee_pct: float
    min_usable_events: int
    directional_consistency_pct: float
    fee_noise_bar_pct: float
    min_symbols_h1_pass: int
    random_baseline_seed: int


@dataclass(frozen=True)
class HourlyBar:
    time: datetime
    close_price: float


@dataclass(frozen=True)
class DataAudit:
    total_events: int
    events_by_type: dict[str, int]
    min_timestamp_precision: str
    sources: tuple[str, ...]
    blocked: bool
    blocked_reason: str | None
    sample_timestamps: tuple[str, ...]


@dataclass(frozen=True)
class HorizonMetrics:
    horizon_hours: int
    event_count: int
    mean_return_pct: float
    median_return_pct: float
    directional_consistency_pct: float
    dominant_sign: str
    baseline_mean_return_pct: float
    excess_vs_baseline_pct: float
    mean_event_vol: float
    mean_baseline_vol: float
    vol_ratio: float
    vol_elevated_fraction: float
    h1_pass: bool
    h2_pass: bool


@dataclass(frozen=True)
class SymbolProbeResult:
    symbol: str
    usable_events: int
    horizons: tuple[HorizonMetrics, ...]


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    data_audit: DataAudit
    events: tuple[MacroEvent, ...]
    symbol_results: tuple[SymbolProbeResult, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _mean(values: Sequence[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def _parse_release_ts(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_frozen_events(csv_path: Path) -> tuple[MacroEvent, ...]:
    events: list[MacroEvent] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            events.append(
                MacroEvent(
                    event_type=row["event_type"].strip(),
                    release_date_et=row["release_date_et"].strip(),
                    release_ts=_parse_release_ts(row["release_ts_utc"]),
                    source=row["source"].strip(),
                )
            )
    return tuple(sorted(events, key=lambda event: event.release_ts))


def audit_macro_data(
    events: Sequence[MacroEvent],
    *,
    min_usable_events: int,
) -> DataAudit:
    if not events:
        return DataAudit(
            total_events=0,
            events_by_type={},
            min_timestamp_precision="none",
            sources=(),
            blocked=True,
            blocked_reason="no events in CSV",
            sample_timestamps=(),
        )

    by_type: dict[str, int] = {}
    sources: set[str] = set()
    for event in events:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
        sources.add(event.source)

    sample = tuple(
        f"{event.event_type} {event.release_ts.isoformat()}" for event in events[:3]
    ) + tuple(f"{event.event_type} {event.release_ts.isoformat()}" for event in events[-2:])

    blocked = len(events) < min_usable_events
    blocked_reason: str | None = None
    if blocked:
        blocked_reason = (
            f"only {len(events)} reliably-timestamped events (need >={min_usable_events})"
        )

    return DataAudit(
        total_events=len(events),
        events_by_type=by_type,
        min_timestamp_precision="minute (DST-aware ET→UTC)",
        sources=tuple(sorted(sources)),
        blocked=blocked,
        blocked_reason=blocked_reason,
        sample_timestamps=sample,
    )


def _bar_overlaps_release(bar_time: datetime, release_ts: datetime) -> bool:
    """True if the 1h bar [open, open+1h) overlaps the release instant."""
    bar_open = bar_time.astimezone(UTC) if bar_time.tzinfo else bar_time.replace(tzinfo=UTC)
    release = release_ts.astimezone(UTC)
    bar_close = bar_open + timedelta(hours=1)
    return bar_open <= release < bar_close


def entry_bar_index(bars: Sequence[HourlyBar], release_ts: datetime) -> int | None:
    """First bar open strictly after release (no overlapping bar)."""
    for index, bar in enumerate(bars):
        bar_open = bar.time.astimezone(UTC) if bar.time.tzinfo else bar.time.replace(tzinfo=UTC)
        if bar_open > release_ts.astimezone(UTC):
            return index
    return None


def forward_return_pct(closes: Sequence[float], entry_idx: int, horizon_bars: int) -> float | None:
    exit_idx = entry_idx + horizon_bars
    if exit_idx >= len(closes):
        return None
    entry = closes[entry_idx]
    if entry <= 0:
        return None
    return (closes[exit_idx] / entry - 1.0) * 100.0


def realized_vol_pct(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    return statistics.pstdev(returns) * 100.0


def _bar_returns(closes: Sequence[float], start: int, end: int) -> list[float]:
    out: list[float] = []
    for idx in range(start + 1, end + 1):
        prev_close = closes[idx - 1]
        if prev_close <= 0:
            continue
        out.append(closes[idx] / prev_close - 1.0)
    return out


def _directional_consistency(returns: Sequence[float]) -> tuple[float, str]:
    if not returns:
        return 0.0, "none"
    positive = sum(1 for value in returns if value > 0)
    negative = sum(1 for value in returns if value < 0)
    total = len(returns)
    if positive >= negative:
        return positive / total * 100.0, "positive"
    return negative / total * 100.0, "negative"


def _build_exclusion_set(
    bars: Sequence[HourlyBar],
    events: Sequence[MacroEvent],
    exclusion_hours: int,
) -> set[int]:
    excluded: set[int] = set()
    delta = timedelta(hours=exclusion_hours)
    for event in events:
        start = event.release_ts - delta
        end = event.release_ts + delta
        for index, bar in enumerate(bars):
            bar_time = bar.time.astimezone(UTC) if bar.time.tzinfo else bar.time.replace(tzinfo=UTC)
            if start <= bar_time <= end:
                excluded.add(index)
    return excluded


def _eligible_random_indices(
    bars: Sequence[HourlyBar],
    *,
    count: int,
    max_horizon: int,
    baseline_bars: int,
    excluded: set[int],
    seed: int,
) -> list[int]:
    candidates = [
        index for index in range(baseline_bars, len(bars) - max_horizon) if index not in excluded
    ]
    if len(candidates) < count:
        return candidates
    rng = random.Random(seed)
    return rng.sample(candidates, count)


def evaluate_symbol_events(
    bars: Sequence[HourlyBar],
    events: Sequence[MacroEvent],
    config: ProbeConfig,
    *,
    symbol: str,
) -> SymbolProbeResult:
    closes = [bar.close_price for bar in bars]
    max_horizon = max(config.horizons_hours)
    event_returns: dict[int, list[float]] = {hours: [] for hours in config.horizons_hours}
    event_vols: dict[int, list[float]] = {hours: [] for hours in config.horizons_hours}
    baseline_vols: dict[int, list[float]] = {hours: [] for hours in config.horizons_hours}
    usable = 0

    for event in events:
        entry_idx = entry_bar_index(bars, event.release_ts)
        if entry_idx is None:
            continue
        if _bar_overlaps_release(bars[entry_idx].time, event.release_ts):
            continue
        if entry_idx < config.baseline_vol_bars:
            continue
        if entry_idx + max_horizon >= len(closes):
            continue

        trail_returns = _bar_returns(closes, entry_idx - config.baseline_vol_bars, entry_idx - 1)
        trail_vol = realized_vol_pct(trail_returns)
        usable += 1

        for horizon in config.horizons_hours:
            fwd = forward_return_pct(closes, entry_idx, horizon)
            if fwd is None:
                continue
            event_returns[horizon].append(fwd)
            window_returns = _bar_returns(closes, entry_idx, entry_idx + horizon)
            event_vols[horizon].append(realized_vol_pct(window_returns))
            baseline_vols[horizon].append(trail_vol)

    excluded = _build_exclusion_set(bars, events, config.event_exclusion_hours)
    random_indices = _eligible_random_indices(
        bars,
        count=max(usable, 1),
        max_horizon=max_horizon,
        baseline_bars=config.baseline_vol_bars,
        excluded=excluded,
        seed=config.random_baseline_seed + hash(bars[0].time if bars else 0) % 10_000,
    )

    baseline_returns_by_horizon: dict[int, list[float]] = {
        hours: [] for hours in config.horizons_hours
    }
    for entry_idx in random_indices:
        for horizon in config.horizons_hours:
            fwd = forward_return_pct(closes, entry_idx, horizon)
            if fwd is not None:
                baseline_returns_by_horizon[horizon].append(fwd)

    horizon_metrics: list[HorizonMetrics] = []
    for horizon in config.horizons_hours:
        returns = event_returns[horizon]
        consistency, dominant = _directional_consistency(returns)
        baseline_mean = _mean(baseline_returns_by_horizon[horizon])
        event_mean = _mean(returns)
        excess = event_mean - baseline_mean
        mean_event_vol = _mean(event_vols[horizon])
        mean_base_vol = _mean(baseline_vols[horizon])
        vol_ratio = mean_event_vol / mean_base_vol if mean_base_vol > 0 else 0.0
        elevated = (
            sum(
                1
                for ev, bv in zip(event_vols[horizon], baseline_vols[horizon], strict=True)
                if ev > bv
            )
            / len(event_vols[horizon])
            if event_vols[horizon]
            else 0.0
        )

        h1_pass = (
            len(returns) > 0
            and consistency >= config.directional_consistency_pct
            and excess > config.fee_noise_bar_pct
        )
        h2_pass = (
            len(event_vols[horizon]) > 0
            and vol_ratio > H2_VOL_RATIO_THRESHOLD
            and elevated >= H2_VOL_ELEVATED_FRACTION
        )

        horizon_metrics.append(
            HorizonMetrics(
                horizon_hours=horizon,
                event_count=len(returns),
                mean_return_pct=event_mean,
                median_return_pct=float(statistics.median(returns)) if returns else 0.0,
                directional_consistency_pct=consistency,
                dominant_sign=dominant,
                baseline_mean_return_pct=baseline_mean,
                excess_vs_baseline_pct=excess,
                mean_event_vol=mean_event_vol,
                mean_baseline_vol=mean_base_vol,
                vol_ratio=vol_ratio,
                vol_elevated_fraction=elevated,
                h1_pass=h1_pass,
                h2_pass=h2_pass,
            )
        )

    return SymbolProbeResult(
        symbol=symbol,
        usable_events=usable,
        horizons=tuple(horizon_metrics),
    )


def probe_symbol_rows(
    bars: Sequence[HourlyBar],
    events: Sequence[MacroEvent],
    config: ProbeConfig,
    *,
    symbol: str,
) -> SymbolProbeResult:
    return evaluate_symbol_events(bars, events, config, symbol=symbol)


def decide_verdict(
    symbol_results: Sequence[SymbolProbeResult],
    *,
    data_blocked: bool,
    config: ProbeConfig,
) -> tuple[str, str, tuple[str, ...]]:
    if data_blocked:
        return (
            BLOCKED_ON_DATA,
            BLOCKED_ON_DATA,
            ("data-feasibility gate failed — edge test not run",),
        )

    reasons: list[str] = []
    h1_symbol_horizon_passes = 0
    h2_symbol_passes = 0

    for result in symbol_results:
        symbol_h1 = any(metrics.h1_pass for metrics in result.horizons)
        symbol_h2 = any(metrics.h2_pass for metrics in result.horizons)
        if symbol_h1:
            h1_symbol_horizon_passes += 1
        if symbol_h2:
            h2_symbol_passes += 1

    if h1_symbol_horizon_passes >= config.min_symbols_h1_pass:
        return ("OK", "HAS_PULSE", tuple(reasons))

    if h2_symbol_passes >= config.min_symbols_h1_pass:
        reasons.append(
            f"H1 failed on all symbols; H2 vol elevation on {h2_symbol_passes}/"
            f"{len(symbol_results)} symbols (overlay only)"
        )
        return ("OK", "WEAK_EDGE", tuple(reasons))

    reasons.append("neither H1 directional drift nor H2 vol elevation passed gates")
    return ("OK", "NO_PULSE", tuple(reasons))


def default_config() -> ProbeConfig:
    return ProbeConfig(
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        events_csv=DEFAULT_EVENTS_CSV,
        horizons_hours=HORIZONS_HOURS,
        baseline_vol_bars=BASELINE_VOL_BARS,
        event_exclusion_hours=EVENT_EXCLUSION_HOURS,
        one_way_fee_pct=0.04,
        min_usable_events=MIN_USABLE_EVENTS,
        directional_consistency_pct=DIRECTIONAL_CONSISTENCY_PCT,
        fee_noise_bar_pct=FEE_NOISE_BAR_PCT,
        min_symbols_h1_pass=MIN_SYMBOLS_H1_PASS,
        random_baseline_seed=RANDOM_BASELINE_SEED,
    )


async def load_symbol_bars(symbol: str, config: ProbeConfig) -> list[HourlyBar]:
    pool = await init_pool(build_db_config())
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                PROBE_QUERY,
                symbol,
                config.timeframe,
                datetime.fromisoformat(config.start),
                datetime.fromisoformat(config.end),
            )
        bars: list[HourlyBar] = []
        for row in rows:
            bar_time = row["time"]
            close = float(row["close_price"])
            if isinstance(bar_time, datetime) and close > 0:
                bars.append(HourlyBar(time=bar_time, close_price=close))
        return bars
    finally:
        await close_pool()


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.macro_event_drift")

    events = load_frozen_events(config.events_csv)
    data_audit = audit_macro_data(events, min_usable_events=config.min_usable_events)
    logger.info(
        "data audit: %d events (FOMC=%d CPI=%d NFP=%d) blocked=%s",
        data_audit.total_events,
        data_audit.events_by_type.get("FOMC", 0),
        data_audit.events_by_type.get("CPI", 0),
        data_audit.events_by_type.get("NFP", 0),
        data_audit.blocked,
    )

    if data_audit.blocked:
        status, verdict, reasons = decide_verdict((), data_blocked=True, config=config)
        return ProbeReport(
            config=config,
            data_audit=data_audit,
            events=events,
            symbol_results=(),
            status=status,
            verdict=verdict,
            reasons=reasons,
        )

    symbol_results: list[SymbolProbeResult] = []
    for symbol in config.symbols:
        bars = await load_symbol_bars(symbol, config)
        logger.info("%s: loaded %d 1h bars", symbol, len(bars))
        symbol_results.append(probe_symbol_rows(bars, events, config, symbol=symbol))

    status, verdict, reasons = decide_verdict(symbol_results, data_blocked=False, config=config)
    return ProbeReport(
        config=config,
        data_audit=data_audit,
        events=events,
        symbol_results=tuple(symbol_results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append("# Scheduled Macro-Event Drift Probe — Report")
    lines.append("")
    lines.append(f"**Verdict:** **{report.verdict}**")
    if report.status == BLOCKED_ON_DATA:
        lines.append(f"**Status:** **{report.status}**")
    lines.append("**Script:** `scripts/probe_macro_event_drift.py`")
    lines.append(
        "**Spec:** [macro-event-drift-probe-v0.md](../specs/macro-event-drift-probe-v0.md)"
    )
    lines.append("")

    audit = report.data_audit
    lines.append("## Step 0 — Data feasibility audit")
    lines.append(f"- Total frozen events: **{audit.total_events}**")
    for event_type, count in sorted(audit.events_by_type.items()):
        lines.append(f"- {event_type}: **{count}**")
    lines.append(f"- Timestamp precision: **{audit.min_timestamp_precision}**")
    lines.append(f"- Sources: {', '.join(audit.sources)}")
    if audit.blocked:
        lines.append(f"- **Blocked:** {audit.blocked_reason}")
    else:
        lines.append("- Data gate: **PASS** (≥50 events, minute-level UTC timestamps)")
    lines.append("")

    if not report.symbol_results:
        lines.append("## Pulse metrics")
        lines.append("")
        lines.append("Not run — data gate failed.")
        return "\n".join(lines)

    lines.append("## Config")
    lines.append(f"- Symbols: {', '.join(report.config.symbols)}")
    lines.append(f"- Horizons: {', '.join(f'+{h}h' for h in report.config.horizons_hours)}")
    lines.append(f"- Fee/noise bar (H1): **{report.config.fee_noise_bar_pct}%** net")
    lines.append(
        f"- Baseline: matched random windows (n=events, {report.config.event_exclusion_hours}h exclusion)"
    )
    lines.append("")

    lines.append("## Pulse metrics (per symbol, per horizon)")
    lines.append("")
    for result in report.symbol_results:
        lines.append(f"### {result.symbol} ({result.usable_events} usable events)")
        lines.append("")
        lines.append(
            "| Horizon | Mean ret % | Baseline % | Excess % | Dir consist % | "
            "Event vol | Base vol | Vol ratio | H1 | H2 |"
        )
        lines.append(
            "|---------|------------|------------|----------|---------------|"
            "----------|----------|-----------|----|----|"
        )
        for metrics in result.horizons:
            lines.append(
                f"| +{metrics.horizon_hours}h | {metrics.mean_return_pct:.2f} | "
                f"{metrics.baseline_mean_return_pct:.2f} | {metrics.excess_vs_baseline_pct:.2f} | "
                f"{metrics.directional_consistency_pct:.0f} ({metrics.dominant_sign}) | "
                f"{metrics.mean_event_vol:.3f} | {metrics.mean_baseline_vol:.3f} | "
                f"{metrics.vol_ratio:.2f} | {metrics.h1_pass} | {metrics.h2_pass} |"
            )
        lines.append("")

    if report.reasons:
        lines.append("## Notes")
        for reason in report.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append(f"**Overall verdict:** {report.verdict}")
    return "\n".join(lines)


def report_to_json(report: ProbeReport) -> dict[str, object]:
    payload: dict[str, object] = {
        "verdict": report.verdict,
        "status": report.status,
        "reasons": list(report.reasons),
        "data_audit": {
            "total_events": report.data_audit.total_events,
            "events_by_type": report.data_audit.events_by_type,
            "min_timestamp_precision": report.data_audit.min_timestamp_precision,
            "sources": list(report.data_audit.sources),
            "blocked": report.data_audit.blocked,
            "blocked_reason": report.data_audit.blocked_reason,
        },
        "config": {
            "symbols": list(report.config.symbols),
            "horizons_hours": list(report.config.horizons_hours),
            "fee_noise_bar_pct": report.config.fee_noise_bar_pct,
            "min_usable_events": report.config.min_usable_events,
        },
    }
    if report.symbol_results:
        payload["symbols"] = [
            {
                "symbol": result.symbol,
                "usable_events": result.usable_events,
                "horizons": [
                    {
                        "horizon_hours": metrics.horizon_hours,
                        "event_count": metrics.event_count,
                        "mean_return_pct": round(metrics.mean_return_pct, 3),
                        "baseline_mean_return_pct": round(metrics.baseline_mean_return_pct, 3),
                        "excess_vs_baseline_pct": round(metrics.excess_vs_baseline_pct, 3),
                        "directional_consistency_pct": round(
                            metrics.directional_consistency_pct, 1
                        ),
                        "dominant_sign": metrics.dominant_sign,
                        "vol_ratio": round(metrics.vol_ratio, 3),
                        "vol_elevated_fraction": round(metrics.vol_elevated_fraction, 3),
                        "h1_pass": metrics.h1_pass,
                        "h2_pass": metrics.h2_pass,
                    }
                    for metrics in result.horizons
                ],
            }
            for result in report.symbol_results
        ]
    return payload


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Macro-event drift cheap probe (Gate 1)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--events-csv",
        type=Path,
        default=None,
        help="Override frozen event CSV path",
    )
    args = parser.parse_args(argv)

    config = default_config()
    if args.events_csv is not None:
        config = ProbeConfig(
            symbols=config.symbols,
            timeframe=config.timeframe,
            start=config.start,
            end=config.end,
            events_csv=args.events_csv,
            horizons_hours=config.horizons_hours,
            baseline_vol_bars=config.baseline_vol_bars,
            event_exclusion_hours=config.event_exclusion_hours,
            one_way_fee_pct=config.one_way_fee_pct,
            min_usable_events=config.min_usable_events,
            directional_consistency_pct=config.directional_consistency_pct,
            fee_noise_bar_pct=config.fee_noise_bar_pct,
            min_symbols_h1_pass=config.min_symbols_h1_pass,
            random_baseline_seed=config.random_baseline_seed,
        )

    report = await run_probe(config)

    if args.json:
        print(json.dumps(report_to_json(report), indent=2, default=str))
    else:
        print(render_report(report))

    if report.verdict == BLOCKED_ON_DATA:
        return 2
    return 0 if report.verdict == "HAS_PULSE" else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
