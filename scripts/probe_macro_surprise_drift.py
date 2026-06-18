#!/usr/bin/env python3
"""Cheap feasibility probe for macro-surprise-conditioned forward drift (Gate 1).

Step 0: point-in-time consensus audit on frozen CPI/NFP surprises CSV.
Step 1: join surprises to events; measure H1 (bucketed directional edge) and H2
(return vs z monotonicity) vs matched random baseline.

Reuses point-in-time entry alignment from probe_macro_event_drift.py unchanged.
See docs/specs/macro-surprise-drift-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_macro_event_drift import (
    BLOCKED_ON_DATA,
    HourlyBar,
    MacroEvent,
    ProbeConfig,
    _bar_overlaps_release,
    _build_exclusion_set,
    _eligible_random_indices,
    _mean,
    _parse_release_ts,
    entry_bar_index,
    forward_return_pct,
    load_frozen_events,
    load_symbol_bars,
)
from scripts.probe_macro_event_drift import (
    default_config as calendar_default_config,
)
from src.utils.logger import configure_logger, get_logger

DEFAULT_SURPRISES_CSV = (
    Path(__file__).resolve().parent.parent / "data/macro_events/us_macro_surprises.csv"
)

HORIZONS_HOURS = (6, 24, 72)
FEE_NOISE_BAR_PCT = 0.3
MIN_SYMBOLS_PASS = 2
MIN_EVENTS_WITH_CONSENSUS = 17  # majority of ~28 per series
MAX_MISSING_CONSENSUS = 39

# Ex-ante expected crypto return sign when z > 0 (hot surprise).
# CPI hot → risk-off → crypto down (negative return).
# NFP hot → hawkish Fed / risk-off → crypto down (negative return).
EXPECTED_RETURN_SIGN_WHEN_HOT: dict[str, int] = {
    "CPI": -1,
    "NFP": -1,
}


@dataclass(frozen=True)
class MacroSurprise:
    event_type: str
    release_date_et: str
    release_ts: datetime
    metric: str
    actual: float
    consensus: float
    surprise: float
    z: float
    consensus_source: str
    actual_source: str
    consensus_note: str


@dataclass(frozen=True)
class SurpriseDataAudit:
    total_cpi_nfp_events: int
    rows_with_consensus: int
    missing_consensus: int
    cpi_count: int
    nfp_count: int
    consensus_sources: tuple[str, ...]
    actual_sources: tuple[str, ...]
    point_in_time_caveat: str
    blocked: bool
    blocked_reason: str | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class EventOutcome:
    event: MacroEvent
    surprise: MacroSurprise
    entry_idx: int
    returns_by_horizon: dict[int, float]


@dataclass(frozen=True)
class HorizonSurpriseMetrics:
    horizon_hours: int
    hot_count: int
    cold_count: int
    hot_mean_return_pct: float
    cold_mean_return_pct: float
    oriented_spread_pct: float
    baseline_spread_pct: float
    excess_spread_pct: float
    rank_correlation: float
    slope_pct_per_z: float
    h1_pass: bool
    h2_pass: bool


@dataclass(frozen=True)
class SeriesSymbolResult:
    event_type: str
    symbol: str
    usable_events: int
    horizons: tuple[HorizonSurpriseMetrics, ...]


@dataclass(frozen=True)
class SurpriseProbeReport:
    data_audit: SurpriseDataAudit
    events: tuple[MacroEvent, ...]
    surprises: tuple[MacroSurprise, ...]
    series_symbol_results: tuple[SeriesSymbolResult, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]


def load_surprises(csv_path: Path) -> tuple[MacroSurprise, ...]:
    rows: list[MacroSurprise] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                MacroSurprise(
                    event_type=row["event_type"].strip(),
                    release_date_et=row["release_date_et"].strip(),
                    release_ts=_parse_release_ts(row["release_ts_utc"]),
                    metric=row["metric"].strip(),
                    actual=float(row["actual"]),
                    consensus=float(row["consensus"]),
                    surprise=float(row["surprise"]),
                    z=float(row["z"]),
                    consensus_source=row["consensus_source"].strip(),
                    actual_source=row["actual_source"].strip(),
                    consensus_note=row.get("consensus_note", "").strip(),
                )
            )
    return tuple(sorted(rows, key=lambda item: item.release_ts))


def join_events_to_surprises(
    events: Sequence[MacroEvent],
    surprises: Sequence[MacroSurprise],
) -> tuple[tuple[MacroEvent, MacroSurprise], ...]:
    surprise_map = {(item.event_type, item.release_ts.isoformat()): item for item in surprises}
    joined: list[tuple[MacroEvent, MacroSurprise]] = []
    for event in events:
        if event.event_type not in ("CPI", "NFP"):
            continue
        key = (event.event_type, event.release_ts.isoformat())
        surprise = surprise_map.get(key)
        if surprise is not None:
            joined.append((event, surprise))
    return tuple(joined)


def audit_surprise_data(
    all_cpi_nfp_events: Sequence[MacroEvent],
    surprises: Sequence[MacroSurprise],
) -> SurpriseDataAudit:
    total = sum(1 for event in all_cpi_nfp_events if event.event_type in ("CPI", "NFP"))
    missing = total - len(surprises)
    cpi = sum(1 for item in surprises if item.event_type == "CPI")
    nfp = sum(1 for item in surprises if item.event_type == "NFP")
    sources = sorted({item.consensus_source for item in surprises})
    actual_sources = sorted({item.actual_source for item in surprises})
    notes = sorted({item.consensus_note for item in surprises if item.consensus_note})

    caveat = (
        "Consensus is Investing.com Forecast column from Wayback-archived calendar pages. "
        "Investing.com does not expose an auditable pre-release snapshot API; the Forecast "
        "field is the median shown on the calendar and may be revised after the fact. "
        "Treat as best-effort point-in-time, not cryptographically provable."
    )

    blocked = False
    blocked_reason: str | None = None
    if missing >= MAX_MISSING_CONSENSUS:
        blocked = True
        blocked_reason = (
            f"consensus unobtainable for {missing}/{total} CPI+NFP events "
            f"(>={MAX_MISSING_CONSENSUS} threshold)"
        )
    elif cpi < MIN_EVENTS_WITH_CONSENSUS or nfp < MIN_EVENTS_WITH_CONSENSUS:
        blocked = True
        blocked_reason = (
            f"coverage below majority: CPI={cpi}, NFP={nfp} "
            f"(need >={MIN_EVENTS_WITH_CONSENSUS} each)"
        )

    return SurpriseDataAudit(
        total_cpi_nfp_events=total,
        rows_with_consensus=len(surprises),
        missing_consensus=missing,
        cpi_count=cpi,
        nfp_count=nfp,
        consensus_sources=tuple(sources),
        actual_sources=tuple(actual_sources),
        point_in_time_caveat=caveat,
        blocked=blocked,
        blocked_reason=blocked_reason,
        notes=tuple(notes),
    )


def _rank_correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    n = len(xs)
    rank_x = _rank_values(xs)
    rank_y = _rank_values(ys)
    mean_x = sum(rank_x) / n
    mean_y = sum(rank_y) / n
    num = sum((rx - mean_x) * (ry - mean_y) for rx, ry in zip(rank_x, rank_y, strict=True))
    den_x = sum((rx - mean_x) ** 2 for rx in rank_x)
    den_y = sum((ry - mean_y) ** 2 for ry in rank_y)
    if den_x <= 0 or den_y <= 0:
        return 0.0
    return num / (den_x * den_y) ** 0.5


def _rank_values(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda idx: values[idx])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(values):
        start = index
        value = values[order[index]]
        while index < len(values) and values[order[index]] == value:
            index += 1
        avg_rank = (start + index - 1) / 2.0 + 1.0
        for position in range(start, index):
            ranks[order[position]] = avg_rank
    return ranks


def _ols_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den <= 0:
        return 0.0
    return num / den


def _collect_event_outcomes(
    bars: Sequence[HourlyBar],
    joined: Sequence[tuple[MacroEvent, MacroSurprise]],
    *,
    horizons_hours: Sequence[int],
    baseline_vol_bars: int,
) -> list[EventOutcome]:
    closes = [bar.close_price for bar in bars]
    max_horizon = max(horizons_hours)
    outcomes: list[EventOutcome] = []

    for event, surprise in joined:
        entry_idx = entry_bar_index(bars, event.release_ts)
        if entry_idx is None:
            continue
        if _bar_overlaps_release(bars[entry_idx].time, event.release_ts):
            continue
        if entry_idx < baseline_vol_bars:
            continue
        if entry_idx + max_horizon >= len(closes):
            continue

        returns: dict[int, float] = {}
        for horizon in horizons_hours:
            fwd = forward_return_pct(closes, entry_idx, horizon)
            if fwd is not None:
                returns[horizon] = fwd
        if not returns:
            continue
        outcomes.append(
            EventOutcome(
                event=event,
                surprise=surprise,
                entry_idx=entry_idx,
                returns_by_horizon=returns,
            )
        )
    return outcomes


def _baseline_spreads(
    bars: Sequence[HourlyBar],
    events: Sequence[MacroEvent],
    *,
    horizons_hours: Sequence[int],
    baseline_vol_bars: int,
    event_exclusion_hours: int,
    random_baseline_seed: int,
    sample_count: int,
) -> dict[int, list[float]]:
    closes = [bar.close_price for bar in bars]
    max_horizon = max(horizons_hours)
    excluded = _build_exclusion_set(bars, events, event_exclusion_hours)
    random_indices = _eligible_random_indices(
        bars,
        count=max(sample_count, 1),
        max_horizon=max_horizon,
        baseline_bars=baseline_vol_bars,
        excluded=excluded,
        seed=random_baseline_seed,
    )
    spreads: dict[int, list[float]] = {hours: [] for hours in horizons_hours}
    if len(random_indices) < 4:
        return spreads

    half = len(random_indices) // 2
    first = random_indices[:half]
    second = random_indices[half : half * 2]
    for horizon in horizons_hours:
        first_returns = [
            forward_return_pct(closes, idx, horizon)
            for idx in first
            if forward_return_pct(closes, idx, horizon) is not None
        ]
        second_returns = [
            forward_return_pct(closes, idx, horizon)
            for idx in second
            if forward_return_pct(closes, idx, horizon) is not None
        ]
        if first_returns and second_returns:
            spreads[horizon].append(_mean(first_returns) - _mean(second_returns))
    return spreads


def evaluate_series_symbol(
    bars: Sequence[HourlyBar],
    joined: Sequence[tuple[MacroEvent, MacroSurprise]],
    all_events: Sequence[MacroEvent],
    config: ProbeConfig,
    *,
    event_type: str,
    symbol: str,
) -> SeriesSymbolResult:
    series_joined = tuple(pair for pair in joined if pair[0].event_type == event_type)
    outcomes = _collect_event_outcomes(
        bars,
        series_joined,
        horizons_hours=config.horizons_hours,
        baseline_vol_bars=config.baseline_vol_bars,
    )
    expected_sign = EXPECTED_RETURN_SIGN_WHEN_HOT[event_type]

    baseline_spreads = _baseline_spreads(
        bars,
        all_events,
        horizons_hours=config.horizons_hours,
        baseline_vol_bars=config.baseline_vol_bars,
        event_exclusion_hours=config.event_exclusion_hours,
        random_baseline_seed=config.random_baseline_seed + hash(symbol) % 10_000,
        sample_count=max(len(outcomes), 8),
    )

    horizon_metrics: list[HorizonSurpriseMetrics] = []
    for horizon in config.horizons_hours:
        hot_returns: list[float] = []
        cold_returns: list[float] = []
        zs: list[float] = []
        returns: list[float] = []

        for outcome in outcomes:
            fwd = outcome.returns_by_horizon.get(horizon)
            if fwd is None:
                continue
            zs.append(outcome.surprise.z)
            returns.append(fwd)
            if outcome.surprise.z > 0:
                hot_returns.append(fwd)
            elif outcome.surprise.z < 0:
                cold_returns.append(fwd)

        hot_mean = _mean(hot_returns)
        cold_mean = _mean(cold_returns)
        oriented_spread = expected_sign * (hot_mean - cold_mean)
        baseline_spread = _mean([abs(value) for value in baseline_spreads[horizon]])
        excess_spread = oriented_spread - baseline_spread

        rank_corr = _rank_correlation(zs, returns)
        slope = _ols_slope(zs, returns)

        opposite_signs = (
            len(hot_returns) > 0
            and len(cold_returns) > 0
            and hot_mean * expected_sign > 0
            and cold_mean * expected_sign < 0
        )
        h1_pass = opposite_signs and excess_spread > config.fee_noise_bar_pct
        h2_pass = len(zs) >= 3 and rank_corr * expected_sign > 0 and slope * expected_sign > 0

        horizon_metrics.append(
            HorizonSurpriseMetrics(
                horizon_hours=horizon,
                hot_count=len(hot_returns),
                cold_count=len(cold_returns),
                hot_mean_return_pct=hot_mean,
                cold_mean_return_pct=cold_mean,
                oriented_spread_pct=oriented_spread,
                baseline_spread_pct=baseline_spread,
                excess_spread_pct=excess_spread,
                rank_correlation=rank_corr,
                slope_pct_per_z=slope,
                h1_pass=h1_pass,
                h2_pass=h2_pass,
            )
        )

    return SeriesSymbolResult(
        event_type=event_type,
        symbol=symbol,
        usable_events=len(outcomes),
        horizons=tuple(horizon_metrics),
    )


def decide_surprise_verdict(
    results: Sequence[SeriesSymbolResult],
    *,
    data_blocked: bool,
) -> tuple[str, str, tuple[str, ...]]:
    if data_blocked:
        return (
            BLOCKED_ON_DATA,
            BLOCKED_ON_DATA,
            ("consensus data gate failed — edge test not run",),
        )

    reasons: list[str] = []
    h1_series_pass = False
    h2_series_pass = False

    for event_type in ("CPI", "NFP"):
        for horizon in HORIZONS_HOURS:
            h1_symbols = 0
            h2_symbols = 0
            for result in results:
                if result.event_type != event_type:
                    continue
                metrics = next(item for item in result.horizons if item.horizon_hours == horizon)
                if metrics.h1_pass:
                    h1_symbols += 1
                if metrics.h2_pass:
                    h2_symbols += 1
            if h1_symbols >= MIN_SYMBOLS_PASS:
                h1_series_pass = True
                reasons.append(
                    f"H1 pass: {event_type} +{horizon}h on {h1_symbols}/3 symbols "
                    f"(expected sign {EXPECTED_RETURN_SIGN_WHEN_HOT[event_type]:+d})"
                )
            if h2_symbols >= MIN_SYMBOLS_PASS:
                h2_series_pass = True
                reasons.append(
                    f"H2 pass: {event_type} +{horizon}h rank/slope on {h2_symbols}/3 symbols"
                )

    if h1_series_pass and h2_series_pass:
        return ("OK", "HAS_PULSE", tuple(reasons))
    if h1_series_pass:
        return ("OK", "HAS_PULSE", tuple(reasons))
    if h2_series_pass:
        reasons.append("H1 bucket spread did not clear fee bar broadly; H2 monotonicity only")
        return ("OK", "WEAK_EDGE", tuple(reasons))

    reasons.append("no surprise-conditioned edge after fee bar on CPI/NFP")
    return ("OK", "NO_PULSE", tuple(reasons))


async def run_probe(
    *,
    surprises_csv: Path,
    config: ProbeConfig | None = None,
) -> SurpriseProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.macro_surprise_drift")
    config = config or calendar_default_config()

    all_events = load_frozen_events(config.events_csv)
    cpi_nfp_events = tuple(event for event in all_events if event.event_type in ("CPI", "NFP"))
    surprises = load_surprises(surprises_csv)
    data_audit = audit_surprise_data(cpi_nfp_events, surprises)
    joined = join_events_to_surprises(all_events, surprises)

    logger.info(
        "data audit: %d/%d CPI+NFP with consensus (CPI=%d NFP=%d) blocked=%s",
        data_audit.rows_with_consensus,
        data_audit.total_cpi_nfp_events,
        data_audit.cpi_count,
        data_audit.nfp_count,
        data_audit.blocked,
    )

    if data_audit.blocked:
        status, verdict, reasons = decide_surprise_verdict((), data_blocked=True)
        return SurpriseProbeReport(
            data_audit=data_audit,
            events=cpi_nfp_events,
            surprises=surprises,
            series_symbol_results=(),
            status=status,
            verdict=verdict,
            reasons=reasons,
        )

    series_symbol_results: list[SeriesSymbolResult] = []
    for symbol in config.symbols:
        bars = await load_symbol_bars(symbol, config)
        logger.info("%s: loaded %d 1h bars", symbol, len(bars))
        for event_type in ("CPI", "NFP"):
            series_symbol_results.append(
                evaluate_series_symbol(
                    bars,
                    joined,
                    all_events,
                    config,
                    event_type=event_type,
                    symbol=symbol,
                )
            )

    status, verdict, reasons = decide_surprise_verdict(
        series_symbol_results,
        data_blocked=False,
    )
    return SurpriseProbeReport(
        data_audit=data_audit,
        events=cpi_nfp_events,
        surprises=surprises,
        series_symbol_results=tuple(series_symbol_results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: SurpriseProbeReport) -> str:
    lines: list[str] = []
    lines.append("# Macro-Surprise Drift Probe — Report")
    lines.append("")
    lines.append(f"**Verdict:** **{report.verdict}**")
    if report.status == BLOCKED_ON_DATA:
        lines.append(f"**Status:** **{report.status}**")
    lines.append("**Script:** `scripts/probe_macro_surprise_drift.py`")
    lines.append(
        "**Spec:** [macro-surprise-drift-probe-v0.md](../specs/macro-surprise-drift-probe-v0.md)"
    )
    lines.append("")

    audit = report.data_audit
    lines.append("## Step 0 — Consensus data audit")
    lines.append(f"- CPI+NFP frozen releases: **{audit.total_cpi_nfp_events}**")
    lines.append(f"- Rows with consensus+actual: **{audit.rows_with_consensus}**")
    lines.append(f"- Missing consensus: **{audit.missing_consensus}**")
    lines.append(f"- CPI rows: **{audit.cpi_count}** | NFP rows: **{audit.nfp_count}**")
    lines.append(f"- Consensus sources: {', '.join(audit.consensus_sources)}")
    lines.append(f"- Actual sources: {', '.join(audit.actual_sources)}")
    lines.append(f"- Point-in-time caveat: {audit.point_in_time_caveat}")
    if audit.notes:
        for note in audit.notes:
            lines.append(f"- Note: {note}")
    if audit.blocked:
        lines.append(f"- **Blocked:** {audit.blocked_reason}")
    else:
        lines.append("- Data gate: **PASS**")
    lines.append("")

    lines.append("## Ex-ante expected sign (frozen)")
    for series, sign in EXPECTED_RETURN_SIGN_WHEN_HOT.items():
        direction = "hot surprise → crypto down" if sign < 0 else "hot surprise → crypto up"
        lines.append(f"- **{series}:** {direction}")
    lines.append("")

    if not report.series_symbol_results:
        lines.append("## Pulse metrics")
        lines.append("")
        lines.append("Not run — data gate failed.")
        return "\n".join(lines)

    lines.append("## Pulse metrics (series × symbol × horizon)")
    lines.append("")
    for result in report.series_symbol_results:
        lines.append(
            f"### {result.event_type} / {result.symbol} ({result.usable_events} usable events)"
        )
        lines.append("")
        lines.append(
            "| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | "
            "Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |"
        )
        lines.append(
            "|---------|-------|--------|------------|-------------|-------------------|"
            "-------------------|----------|--------|-------|----|----|"
        )
        for metrics in result.horizons:
            lines.append(
                f"| +{metrics.horizon_hours}h | {metrics.hot_count} | {metrics.cold_count} | "
                f"{metrics.hot_mean_return_pct:.2f} | {metrics.cold_mean_return_pct:.2f} | "
                f"{metrics.oriented_spread_pct:.2f} | {metrics.baseline_spread_pct:.2f} | "
                f"{metrics.excess_spread_pct:.2f} | {metrics.rank_correlation:.2f} | "
                f"{metrics.slope_pct_per_z:.3f} | {metrics.h1_pass} | {metrics.h2_pass} |"
            )
        lines.append("")

    if report.reasons:
        lines.append("## Notes")
        for reason in report.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append(f"**Overall verdict:** {report.verdict}")
    return "\n".join(lines)


def report_to_json(report: SurpriseProbeReport) -> dict[str, object]:
    return {
        "verdict": report.verdict,
        "status": report.status,
        "reasons": list(report.reasons),
        "expected_sign_when_hot": EXPECTED_RETURN_SIGN_WHEN_HOT,
        "data_audit": {
            "total_cpi_nfp_events": report.data_audit.total_cpi_nfp_events,
            "rows_with_consensus": report.data_audit.rows_with_consensus,
            "missing_consensus": report.data_audit.missing_consensus,
            "cpi_count": report.data_audit.cpi_count,
            "nfp_count": report.data_audit.nfp_count,
            "consensus_sources": list(report.data_audit.consensus_sources),
            "actual_sources": list(report.data_audit.actual_sources),
            "point_in_time_caveat": report.data_audit.point_in_time_caveat,
            "blocked": report.data_audit.blocked,
            "blocked_reason": report.data_audit.blocked_reason,
            "notes": list(report.data_audit.notes),
        },
        "results": [
            {
                "event_type": result.event_type,
                "symbol": result.symbol,
                "usable_events": result.usable_events,
                "horizons": [
                    {
                        "horizon_hours": metrics.horizon_hours,
                        "hot_count": metrics.hot_count,
                        "cold_count": metrics.cold_count,
                        "hot_mean_return_pct": round(metrics.hot_mean_return_pct, 3),
                        "cold_mean_return_pct": round(metrics.cold_mean_return_pct, 3),
                        "oriented_spread_pct": round(metrics.oriented_spread_pct, 3),
                        "baseline_spread_pct": round(metrics.baseline_spread_pct, 3),
                        "excess_spread_pct": round(metrics.excess_spread_pct, 3),
                        "rank_correlation": round(metrics.rank_correlation, 3),
                        "slope_pct_per_z": round(metrics.slope_pct_per_z, 4),
                        "h1_pass": metrics.h1_pass,
                        "h2_pass": metrics.h2_pass,
                    }
                    for metrics in result.horizons
                ],
            }
            for result in report.series_symbol_results
        ],
    }


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Macro-surprise drift cheap probe (Gate 1)")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--surprises-csv",
        type=Path,
        default=DEFAULT_SURPRISES_CSV,
        help="Frozen surprise CSV path",
    )
    args = parser.parse_args(argv)

    report = await run_probe(surprises_csv=args.surprises_csv)

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
