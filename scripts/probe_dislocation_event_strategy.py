#!/usr/bin/env python3
"""
Cheap feasibility probe for standalone *cross-venue dislocation event strategy*.

This is the sharpened cheap probe for the cross-venue dislocation event lane
(config/autoresearch/rbi_loop.cross-venue-dislocation-event-v0.yaml).

It measures whether entering on no-lookahead cross-venue spread extremes
(or normalization) and exiting at fixed horizon produces positive expectancy
net of fees+slippage, with sufficient event density after deduplication+cooldown,
and acceptable concentration/win-rate, using only trailing or fixed-grid
thresholds (no full-sample lookahead).

See docs/specs/cross-venue-dislocation-event-strategy-v0.md for Gate 1.

Usage (dry / guard-driven, via manifest only after merge + human go-ahead):
  uv run python scripts/probe_dislocation_event_strategy.py \
    --venues binance_usdm,bybit \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --timeframe 1h \
    --start 2024-01-01 \
    --fee-pct 0.08 \
    --slippage-pct 0.02 \
    --verdict-output research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json

--smoke for tests (no DB). Never pass --execute here; real execution is post-merge
via rbi_loop_from_manifest with explicit user approval. No manifest edits in this change.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

# Heavy imports (src.db, build_db_config) are done lazily inside non-smoke paths
# so --smoke works with zero DB/network surface.

PROBE_QUERY = """
    SELECT
        p.time,
        p.exchange,
        p.basis_bps,
        p.premium_index,
        o.close_price,
        o.low_price,
        o.high_price
    FROM perp_basis_metrics p
    INNER JOIN ohlcv o
        ON p.time = o.time
        AND p.symbol = o.symbol
        AND p.timeframe = o.timeframe
    WHERE p.exchange = ANY($1)
      AND p.symbol = $2
      AND p.timeframe = $3
      AND p.time >= $4
      AND p.time <= $5
    ORDER BY p.time ASC
"""


class ScenarioKind(StrEnum):
    EXTREME_POSITIVE = "extreme_positive"
    EXTREME_NEGATIVE = "extreme_negative"
    NORMALIZATION = "normalization"


@dataclass(frozen=True)
class EventBar:
    """Local bar carrying high_price for short-side MAE (PremiumBar only has close/low)."""

    time: datetime
    basis_bps: float
    premium_index: float
    close_price: float
    low_price: float
    high_price: float


@dataclass(frozen=True)
class DislocationEventProbeConfig:
    venues: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframe: str
    start: str
    end: str
    fee_pct: float
    slippage_pct: float
    horizons: tuple[int, ...]
    cooldown_mode: str
    threshold_mode: str  # rolling | fixed | both
    rolling_days: int
    abs_bps_grid: tuple[float, ...]
    tail_pcts: tuple[int, ...]


@dataclass(frozen=True)
class SmokeReport:
    verdict: str
    note: str


@dataclass(frozen=True)
class DislocationProbeReport:
    verdict: str
    note: str
    passing_scenarios: tuple[str, ...]
    per_scenario_stats: dict[str, Any]
    config: DislocationEventProbeConfig


def _cheap_smoke_test() -> SmokeReport:
    """No-DB, no-network safe default used by --smoke and tests."""
    return SmokeReport(
        verdict="NO_PULSE",
        note=(
            "SMOKE: no data path exercised. "
            "This is the expected safe default before any real probe execution "
            "(real run only post-merge via manifest with explicit user go-ahead)."
        ),
    )


def _mean(values: Sequence[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def _median(values: Sequence[float]) -> float:
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
    return ordered[low] * (1.0 - weight) + ordered[high] * (weight)


def tail_threshold_high(values: Sequence[float], tail_pct: int) -> float:
    return _percentile(values, 100.0 - tail_pct)


def tail_threshold_low(values: Sequence[float], tail_pct: int) -> float:
    return _percentile(values, float(tail_pct))


def _metric_value(bar: EventBar, metric: str) -> float:
    if metric == "premium_index":
        return bar.premium_index
    return bar.basis_bps


def build_pair_spread_bars(
    rows: list[dict[str, Any]],
    venues: tuple[str, ...],
) -> dict[str, list[EventBar]]:
    """Adapted from probe_cross_venue_basis.py:106-141 (local EventBar with high_price)."""
    by_time: dict[datetime, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_time.setdefault(row["time"], {})[row["exchange"]] = row

    pairs = [(venues[i], venues[j]) for i in range(len(venues)) for j in range(i + 1, len(venues))]
    out: dict[str, list[EventBar]] = {f"{a}-{b}": [] for a, b in pairs}
    for t in sorted(by_time):
        venue_rows = by_time[t]
        for a, b in pairs:
            row_a = venue_rows.get(a)
            row_b = venue_rows.get(b)
            if row_a is None or row_b is None:
                continue
            out[f"{a}-{b}"].append(
                EventBar(
                    time=t,
                    basis_bps=float(row_a["basis_bps"]) - float(row_b["basis_bps"]),
                    premium_index=float(row_a["premium_index"]) - float(row_b["premium_index"]),
                    close_price=float(row_a["close_price"]),
                    low_price=float(row_a["low_price"]),
                    high_price=float(row_a["high_price"]),
                )
            )
    return out


def _get_window_values(bars: list[EventBar], i: int, metric: str, rolling_days: int) -> list[float]:
    """Values strictly before bar i, within trailing rolling_days window."""
    if i <= 0:
        return []
    t_i = bars[i].time
    cutoff = t_i - timedelta(days=rolling_days)
    vals: list[float] = []
    for j in range(i):
        if bars[j].time >= cutoff:
            vals.append(_metric_value(bars[j], metric))
    return vals


def _compute_threshold(
    bars: list[EventBar],
    i: int,
    metric: str,
    mode: str,
    spec: float,
    rolling_days: int,
    kind: ScenarioKind,
) -> float:
    """Return the (signed where appropriate) threshold applicable at bar i. No full-sample ever."""
    if mode == "fixed":
        if kind == ScenarioKind.EXTREME_POSITIVE:
            return float(spec)
        if kind == ScenarioKind.EXTREME_NEGATIVE:
            return -float(spec)
        return float(spec)
    # rolling: trailing window only
    win = _get_window_values(bars, i, metric, rolling_days)
    if len(win) < 2:
        # insufficient history -> caller must skip
        return 0.0
    tail_pct = int(spec)
    if kind == ScenarioKind.EXTREME_POSITIVE or kind == ScenarioKind.NORMALIZATION:
        return tail_threshold_high(win, tail_pct)
    return tail_threshold_low(win, tail_pct)


def _first_of_clusters(qual_indices: list[int]) -> list[int]:
    if not qual_indices:
        return []
    heads = [qual_indices[0]]
    for idx in qual_indices[1:]:
        if idx > heads[-1] + 1:
            heads.append(idx)
    return heads


def _apply_cooldown_thin(heads: list[int], cooldown: int) -> list[int]:
    if not heads:
        return []
    selected = [heads[0]]
    for h in heads[1:]:
        if h >= selected[-1] + cooldown:
            selected.append(h)
    return selected


def _collect_extreme_candidate_indices(
    bars: list[EventBar],
    metric: str,
    kind: ScenarioKind,
    threshold_mode: str,
    threshold_spec: float,
    rolling_days: int,
) -> list[int]:
    """All bars meeting the (possibly per-bar rolling) threshold. Raw before dedup."""
    cands: list[int] = []
    for i in range(len(bars)):
        val = _metric_value(bars[i], metric)
        if threshold_mode == "rolling":
            win = _get_window_values(bars, i, metric, rolling_days)
            if len(win) < 2:
                continue
        th = _compute_threshold(bars, i, metric, threshold_mode, threshold_spec, rolling_days, kind)
        if kind == ScenarioKind.EXTREME_POSITIVE:
            if val >= th:
                cands.append(i)
        else:
            if val <= th:
                cands.append(i)
    return cands


def _collect_normalization_candidate_indices(
    bars: list[EventBar],
    metric: str,
    threshold_mode: str,
    threshold_spec: float,
    rolling_days: int,
) -> list[int]:
    """State-machine fire points for normalization (pre-cooldown)."""
    if not bars:
        return []
    cands: list[int] = []
    state = "idle"
    prior_extreme = 0.0
    for i, bar in enumerate(bars):
        val = _metric_value(bar, metric)
        if threshold_mode == "rolling":
            win = _get_window_values(bars, i, metric, rolling_days)
            if len(win) < 2:
                continue
            high_entry = tail_threshold_high(win, int(threshold_spec))
            low_entry = tail_threshold_low(win, int(threshold_spec))
            abs_win = [abs(v) for v in win]
            exit_band = _percentile(abs_win, 50.0) if abs_win else 0.0
        else:
            high_entry = float(threshold_spec)
            low_entry = -float(threshold_spec)
            exit_band = 0.0
        if state == "idle":
            if val >= high_entry:
                state = "extreme_positive"
                prior_extreme = val
            elif val <= low_entry:
                state = "extreme_negative"
                prior_extreme = val
            continue
        if state == "extreme_positive":
            if val >= high_entry:
                prior_extreme = max(prior_extreme, val)
                continue
            if abs(val) <= exit_band:
                cands.append(i)
                state = "idle"
            elif val <= low_entry:
                state = "extreme_negative"
                prior_extreme = val
            continue
        if state == "extreme_negative":
            if val <= low_entry:
                prior_extreme = min(prior_extreme, val)
                continue
            if abs(val) <= exit_band:
                cands.append(i)
                state = "idle"
            elif val >= high_entry:
                state = "extreme_positive"
                prior_extreme = val
    return cands


def _compute_directed_event_stats(
    bars: list[EventBar],
    dedup_is: list[int],
    horizon: int,
    direction: str,
    fee_pct: float,
    slippage_pct: float,
) -> dict[str, Any]:
    """Return net stats for one direction at one horizon from deduped entry indices."""
    cost = fee_pct + slippage_pct
    valid_nets: list[float] = []
    valid_maes: list[float] = []
    wins = 0
    month_nets: dict[str, list[float]] = defaultdict(list)
    for idx in dedup_is:
        if idx + horizon >= len(bars):
            continue
        bar = bars[idx]
        entry_p = bar.close_price
        fut_p = bars[idx + horizon].close_price
        if entry_p <= 0:
            continue
        move = (fut_p / entry_p - 1.0) * 100.0
        gross = move if direction == "long" else -move
        net = gross - cost
        valid_nets.append(net)
        w_start = idx + 1
        w_end = idx + horizon + 1
        if direction == "long":
            ws = [bars[j].low_price for j in range(w_start, min(w_end, len(bars)))]
            if ws:
                mae = (entry_p - min(ws)) / entry_p * 100.0 if entry_p > 0 else 0.0
                valid_maes.append(mae)
        else:
            ws = [bars[j].high_price for j in range(w_start, min(w_end, len(bars)))]
            if ws:
                mae = (max(ws) - entry_p) / entry_p * 100.0 if entry_p > 0 else 0.0
                valid_maes.append(mae)
        if net > 0:
            wins += 1
        mon_key = bar.time.strftime("%Y-%m")
        month_nets[mon_key].append(net)
    n = len(valid_nets)
    if n == 0:
        return {
            "deduped_count": 0,
            "net_mean": 0.0,
            "net_median": 0.0,
            "win_rate": 0.0,
            "mae_mean": 0.0,
            "monthly_concentration_pct": 0.0,
        }
    nmean = _mean(valid_nets)
    nmed = _median(valid_nets)
    wr = (wins / n) * 100.0
    mmean = _mean(valid_maes) if valid_maes else 0.0
    tnet = sum(valid_nets)
    msums = [sum(vs) for vs in month_nets.values()]
    if tnet > 0:
        pms = [ms for ms in msums if ms > 0]
        mconc = (max(pms) / tnet * 100.0) if pms else 0.0
    else:
        mconc = 0.0
    return {
        "deduped_count": n,
        "net_mean": nmean,
        "net_median": nmed,
        "win_rate": wr,
        "mae_mean": mmean,
        "monthly_concentration_pct": mconc,
    }


def _probe_bars(
    bars: list[EventBar],
    full_symbol: str,
    config: DislocationEventProbeConfig,
) -> tuple[list[str], dict[str, Any]]:
    """Core per-pair computation. Returns (passing_labels, per_scenario_stats_for_pair)."""
    passing: list[str] = []
    stats: dict[str, Any] = {}
    modes: list[str] = []
    if config.threshold_mode in ("fixed", "both"):
        modes.append("fixed")
    if config.threshold_mode in ("rolling", "both"):
        modes.append("rolling")
    for mode in modes:
        thresh_items: list[tuple[float, str]] = []
        if mode == "fixed":
            for g in config.abs_bps_grid:
                thresh_items.append((float(g), f"abs{g}"))
        else:
            for tp in config.tail_pcts:
                thresh_items.append((float(tp), f"tail{tp}"))
        for metric in ("basis_bps", "premium_index"):
            for kind in (
                ScenarioKind.EXTREME_POSITIVE,
                ScenarioKind.EXTREME_NEGATIVE,
                ScenarioKind.NORMALIZATION,
            ):
                for spec_val, spec_label in thresh_items:
                    horizon_details: dict[str, Any] = {}
                    for horizon in config.horizons:
                        cooldown = horizon  # --cooldown-mode horizon
                        if kind in (ScenarioKind.EXTREME_POSITIVE, ScenarioKind.EXTREME_NEGATIVE):
                            qual = _collect_extreme_candidate_indices(
                                bars, metric, kind, mode, spec_val, config.rolling_days
                            )
                            raw_c = len(qual)
                            heads = _first_of_clusters(qual)
                            dedup_is = _apply_cooldown_thin(heads, cooldown)
                        else:
                            cands = _collect_normalization_candidate_indices(
                                bars, metric, mode, spec_val, config.rolling_days
                            )
                            raw_c = len(cands)
                            dedup_is = _apply_cooldown_thin(cands, cooldown)
                        dir_stats: dict[str, Any] = {}
                        for direction in ("long", "short"):
                            dst = _compute_directed_event_stats(
                                bars,
                                dedup_is,
                                horizon,
                                direction,
                                config.fee_pct,
                                config.slippage_pct,
                            )
                            dir_stats[direction] = dst
                            n = dst["deduped_count"]
                            if (
                                n >= 40
                                and dst["net_mean"] > 0
                                and dst["net_median"] > 0
                                and dst["win_rate"] >= 45.0
                                and dst["monthly_concentration_pct"] <= 50.0
                            ):
                                label = f"{full_symbol}:{kind}:{metric}:{mode}:{spec_label}:{direction}:h{horizon}"
                                passing.append(label)
                        horizon_details[str(horizon)] = {
                            "raw_count": raw_c,
                            "deduped_count_long": dir_stats["long"]["deduped_count"],
                            "deduped_count_short": dir_stats["short"]["deduped_count"],
                            "long": dir_stats["long"],
                            "short": dir_stats["short"],
                        }
                    base_key = f"{full_symbol}:{kind}:{metric}:{mode}:{spec_label}"
                    stats[base_key] = {
                        "threshold_mode": mode,
                        "spec_label": spec_label,
                        "horizons": horizon_details,
                    }
    return passing, stats


def analyze_dislocation_events(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    config: DislocationEventProbeConfig,
) -> DislocationProbeReport:
    """Build per-pair EventBars then run no-lookahead event probe per symbol/pair."""
    all_passing: list[str] = []
    all_stats: dict[str, Any] = {}
    has_events = False
    for symbol, rows in rows_by_symbol.items():
        for pair_label, bars in build_pair_spread_bars(rows, config.venues).items():
            full = f"{symbol}|{pair_label}"
            passing, stats = _probe_bars(bars, full, config)
            all_passing.extend(passing)
            all_stats.update(stats)
            for sk in stats.values():
                for hd in sk.get("horizons", {}).values():
                    if hd.get("deduped_count_long", 0) + hd.get("deduped_count_short", 0) > 0:
                        has_events = True
    if all_passing:
        verdict = "HAS_PULSE"
        note = "Dislocation event probe: net-of-cost forward edge after no-lookahead thresholds (see brief)."
    elif has_events:
        verdict = "WEAK_EDGE"
        note = "Events exist but fail net mean/median>0, win>=45%, conc<=50%, or >=40 deduped in no-lookahead modes."
    else:
        verdict = "NO_PULSE"
        note = "No qualifying events under the configured threshold modes."
    return DislocationProbeReport(
        verdict=verdict,
        note=note,
        passing_scenarios=tuple(sorted(set(all_passing))),
        per_scenario_stats=all_stats,
        config=config,
    )


def print_dislocation_report(
    report: DislocationProbeReport, config: DislocationEventProbeConfig
) -> None:
    print("Dislocation Event Strategy Probe (cross-venue standalone)")
    print(f"Venues:    {', '.join(config.venues)}")
    print(f"Symbols:   {', '.join(config.symbols)}")
    print(f"Timeframe: {config.timeframe}")
    print(f"Window:    {config.start} -> {config.end}")
    print(f"Fee+slip:  {config.fee_pct + config.slippage_pct:.2f}%")
    print(f"Horizons:  {config.horizons}")
    print(
        f"Threshold: {config.threshold_mode} (rolling_days={config.rolling_days}, grid={config.abs_bps_grid})"
    )
    print(f"Verdict:   {report.verdict}")
    if report.passing_scenarios:
        print("Passing scenarios (Gate 1 met for >=1 horizon/dir in no-lookahead mode):")
        for label in report.passing_scenarios:
            print(f"  {label}")
    else:
        print("No scenarios passed Gate 1.")


def _write_verdict(
    verdict: str,
    note: str,
    passing: tuple[str, ...],
    per_scenario_stats: dict[str, Any],
    config: DislocationEventProbeConfig,
    path: str | None,
) -> None:
    if not path:
        return
    payload = {
        "verdict": verdict,
        "note": note,
        "passing_scenarios": list(passing),
        "per_scenario_stats": per_scenario_stats,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "venues": list(config.venues),
            "symbols": list(config.symbols),
            "timeframe": config.timeframe,
            "start": config.start,
            "end": config.end,
            "fee_pct": config.fee_pct,
            "slippage_pct": config.slippage_pct,
            "horizons": list(config.horizons),
            "cooldown_mode": config.cooldown_mode,
            "threshold_mode": config.threshold_mode,
            "rolling_days": config.rolling_days,
            "abs_bps_grid": list(config.abs_bps_grid),
            "tail_pcts": list(config.tail_pcts),
        },
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote guard-consumable verdict to {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-venue dislocation event standalone strategy probe (RBI cheap probe)."
    )
    parser.add_argument("--venues", default="binance_usdm,bybit")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--fee-pct", type=float, default=0.08)
    parser.add_argument("--slippage-pct", type=float, default=0.02)
    parser.add_argument("--horizons", default="6,12,24")
    parser.add_argument("--cooldown-mode", default="horizon", choices=["horizon"])
    parser.add_argument("--threshold-mode", default="both", choices=["rolling", "fixed", "both"])
    parser.add_argument("--rolling-days", type=int, default=90)
    parser.add_argument("--abs-bps-grid", default="3.5,4.5,5.5,7.0")
    parser.add_argument("--tail-pcts", default="5,10")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="No-DB safe default path (always NO_PULSE); used by tests",
    )
    parser.add_argument(
        "--verdict-output",
        default=None,
        help="Write guard-consumable verdict JSON to this path",
    )
    return parser.parse_args()


def _config_from_args(args: argparse.Namespace) -> DislocationEventProbeConfig:
    venues = tuple(v.strip() for v in args.venues.split(",") if v.strip())
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    grid = tuple(float(g) for g in args.abs_bps_grid.split(",") if g.strip())
    tails = tuple(int(t) for t in args.tail_pcts.split(",") if t.strip())
    end = args.end or datetime.now(UTC).date().isoformat()
    return DislocationEventProbeConfig(
        venues=venues,
        symbols=symbols,
        timeframe=args.timeframe,
        start=args.start,
        end=end,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
        horizons=horizons,
        cooldown_mode=args.cooldown_mode,
        threshold_mode=args.threshold_mode,
        rolling_days=args.rolling_days,
        abs_bps_grid=grid,
        tail_pcts=tails,
    )


async def main_async() -> None:
    args = parse_args()
    config = _config_from_args(args)

    if args.smoke:
        report = _cheap_smoke_test()
        print(f"Verdict:   {report.verdict}")
        print(f"Note:      {report.note}")
        _write_verdict(report.verdict, report.note, (), {}, config, args.verdict_output)
        return

    from scripts.probe_basis_premium import build_db_config
    from src.db import close_pool, init_pool
    from src.utils.logger import configure_logger

    configure_logger("WARNING")
    pool = await init_pool(build_db_config())
    try:
        start = datetime.fromisoformat(config.start).replace(tzinfo=UTC)
        end = datetime.fromisoformat(config.end).replace(tzinfo=UTC)
        rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for symbol in config.symbols:
            fetched = await pool.fetch(
                PROBE_QUERY, list(config.venues), symbol, config.timeframe, start, end
            )
            rows_by_symbol[symbol] = [dict(r) for r in fetched]
    finally:
        await close_pool()

    report = analyze_dislocation_events(rows_by_symbol, config)
    print_dislocation_report(report, config)
    _write_verdict(
        report.verdict,
        report.note,
        report.passing_scenarios,
        report.per_scenario_stats,
        config,
        args.verdict_output,
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
