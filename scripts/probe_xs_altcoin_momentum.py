#!/usr/bin/env python3
"""Cheap Gate-1 probe — cross-sectional altcoin momentum (dollar-neutral long/short).

Tests the one signal *object* the terminal research program never ran: a relative-value
rank basket across a broad perp universe, not a single-name directional forecast. See the
lane brief:

    docs/specs/xs-altcoin-momentum-probe-v0.md

Read-only and self-contained: pulls daily klines from Binance's public USDⓈ-M futures REST
endpoint (no auth, no DB dependency). Forms a weekly-rebalanced, equal-weight, dollar-neutral
long(top-quantile)/short(bottom-quantile) momentum basket and reports the five pre-registered
HAS_PULSE gates, including a label-shuffle null. Emits probe_result.json (verdict consumable
by rbi_loop_guard.py) and probe_report.md.

This decides only whether the full surface is worth building. It registers no strategy, runs
no sweep, and touches nothing live.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from src.utils.logger import configure_logger, get_logger

logger = get_logger("probe.xs_altcoin_momentum")

FAPI_BASE = "https://fapi.binance.com"
DAY_MS = 86_400_000
# Bases that are stable/fiat or otherwise not a momentum candidate.
_EXCLUDE_BASES = {
    "USDC", "USDT", "TUSD", "BUSD", "FDUSD", "DAI", "USDP", "USDD", "EUR", "GBP",
}
# Leveraged-token / index suffixes that are not spot-like perps.
_EXCLUDE_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


@dataclass(frozen=True)
class ProbeConfig:
    universe_size: int
    lookbacks: tuple[int, ...]  # momentum lookback in days
    quantiles: tuple[float, ...]  # fraction of the universe per leg
    hold_days: int  # rebalance / holding period
    skip_recent_days: int  # skip most-recent N days of the lookback (reversal guard)
    cost_bps: float  # per-leg round-trip turnover cost, basis points
    funding_bps_per_day: float  # carry allowance on gross notional, bps/day
    start: str
    end: str
    min_history_days: int
    bootstrap: int
    seed: int


@dataclass
class CellResult:
    lookback: int
    quantile: float
    n_periods: int
    mean_net_per_period: float
    mean_gross_spread: float
    cum_net: float
    terminal_wealth: float  # geometric, ruin-capped at 0
    min_period_net: float
    n_ruin_periods: int  # periods with net <= -100% (short-leg blowup)
    solvent: bool  # wealth never hit <= 0
    best_period_share: float
    bootstrap_p: float
    shuffle_mean_net: float
    passes_net_positive: bool
    passes_significant: bool


@dataclass
class ProbeReport:
    config: ProbeConfig
    universe: list[str]
    span_start: str
    span_end: str
    cells: list[CellResult] = field(default_factory=list)
    verdict: str = "NO_PULSE"
    gate_detail: dict[str, object] = field(default_factory=dict)


def _http_get_json(url: str, *, retries: int = 3, pause: float = 0.5) -> object:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "xs-mom-probe/0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - network boundary, retry then raise
            last_exc = exc
            time.sleep(pause * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last_exc


def _to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def select_universe(size: int) -> list[str]:
    """Top-`size` USDT perpetuals by 24h quote volume (excludes stables/leveraged tokens).

    Survivorship caveat (documented, not hidden): ranking uses *current* liquidity, so the
    universe is biased toward names that survived to today. Acceptable for a feasibility
    probe; a promotion sweep would need a point-in-time listing set.
    """
    info = _http_get_json(f"{FAPI_BASE}/fapi/v1/exchangeInfo")
    perp_usdt: set[str] = set()
    for sym in info.get("symbols", []):  # type: ignore[union-attr]
        if (
            sym.get("contractType") == "PERPETUAL"
            and sym.get("quoteAsset") == "USDT"
            and sym.get("status") == "TRADING"
            and sym.get("baseAsset") not in _EXCLUDE_BASES
            and not str(sym.get("symbol", "")).endswith(_EXCLUDE_SUFFIXES)
        ):
            perp_usdt.add(str(sym["symbol"]))
    tickers = _http_get_json(f"{FAPI_BASE}/fapi/v1/ticker/24hr")
    ranked = sorted(
        (t for t in tickers if t.get("symbol") in perp_usdt),  # type: ignore[union-attr]
        key=lambda t: float(t.get("quoteVolume", 0.0)),
        reverse=True,
    )
    return [str(t["symbol"]) for t in ranked[:size]]


def fetch_daily_closes(symbol: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Daily UTC close series keyed by day-open epoch-ms. Empty before listing (no backfill)."""
    out: dict[int, float] = {}
    cursor = start_ms
    while cursor < end_ms:
        url = (
            f"{FAPI_BASE}/fapi/v1/klines?symbol={symbol}&interval=1d"
            f"&startTime={cursor}&endTime={end_ms}&limit=1500"
        )
        rows = _http_get_json(url)
        if not rows:
            break
        for row in rows:  # type: ignore[union-attr]
            out[int(row[0])] = float(row[4])
        last_open = int(rows[-1][0])  # type: ignore[index]
        if last_open + DAY_MS >= end_ms or len(rows) < 1500:  # type: ignore[arg-type]
            break
        cursor = last_open + DAY_MS
    return out


def build_panel(
    symbols: list[str], start_ms: int, end_ms: int
) -> tuple[list[int], dict[str, dict[int, float]]]:
    panel: dict[str, dict[int, float]] = {}
    all_days: set[int] = set()
    for sym in symbols:
        series = fetch_daily_closes(sym, start_ms, end_ms)
        if series:
            panel[sym] = series
            all_days.update(series.keys())
        logger.info("fetched %s: %d daily bars", sym, len(series))
    return sorted(all_days), panel


def _basket_long_short(
    ranked: list[tuple[str, float]], q: float
) -> tuple[list[str], list[str]]:
    n_leg = max(1, int(len(ranked) * q))
    longs = [s for s, _ in ranked[:n_leg]]
    shorts = [s for s, _ in ranked[-n_leg:]]
    return longs, shorts


def _ret(panel: dict[str, dict[int, float]], sym: str, d0: int, d1: int) -> float | None:
    p0 = panel[sym].get(d0)
    p1 = panel[sym].get(d1)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def run_cell(
    days: list[int],
    panel: dict[str, dict[int, float]],
    cfg: ProbeConfig,
    lookback: int,
    q: float,
    *,
    shuffle: bool = False,
    rng: random.Random | None = None,
) -> tuple[list[float], list[float]]:
    """Return (net_period_returns, gross_long_short_spreads) over rebalance dates."""
    net_returns: list[float] = []
    spreads: list[float] = []
    rng = rng or random.Random(cfg.seed)
    cost_frac = cfg.cost_bps / 10_000.0
    fund_frac = cfg.funding_bps_per_day / 10_000.0
    prev_members: set[str] = set()

    start_i = lookback + cfg.skip_recent_days
    for i in range(start_i, len(days) - cfg.hold_days, cfg.hold_days):
        d_now = days[i]
        d_skip = days[i - cfg.skip_recent_days]
        d_look = days[i - cfg.skip_recent_days - lookback]
        d_fwd = days[i + cfg.hold_days]
        # eligible: full lookback + forward window present (no backfill / no look-ahead)
        moms: list[tuple[str, float]] = []
        fwd: dict[str, float] = {}
        for sym in panel:
            m = _ret(panel, sym, d_look, d_skip)
            f = _ret(panel, sym, d_now, d_fwd)
            if m is not None and f is not None:
                moms.append((sym, m))
                fwd[sym] = f
        if len(moms) < 6:  # need a meaningful cross-section
            continue
        if shuffle:
            scores = [m for _, m in moms]
            rng.shuffle(scores)
            moms = [(sym, scores[j]) for j, (sym, _) in enumerate(moms)]
        moms.sort(key=lambda t: t[1], reverse=True)
        longs, shorts = _basket_long_short(moms, q)
        long_ret = statistics.fmean(fwd[s] for s in longs)
        short_ret = statistics.fmean(fwd[s] for s in shorts)
        gross = long_ret - short_ret  # dollar-neutral spread
        members = set(longs) | set(shorts)
        # turnover: names entering/leaving the book since last rebalance (both legs trade)
        turnover = len(members ^ prev_members) / max(1, len(members | prev_members))
        prev_members = members
        cost = cost_frac * (1.0 + turnover)  # establish + rebalance fraction
        funding = fund_frac * cfg.hold_days  # carry on gross notional over the hold
        net = gross - cost - funding
        spreads.append(gross)
        net_returns.append(net)
    return net_returns, spreads


def _bootstrap_p_gt0(samples: list[float], n: int, seed: int) -> float:
    """One-sided bootstrap p that the mean is > 0 (fraction of resample means ≤ 0)."""
    if not samples:
        return 1.0
    rng = random.Random(seed)
    k = len(samples)
    le_zero = 0
    for _ in range(n):
        m = statistics.fmean(samples[rng.randrange(k)] for _ in range(k))
        if m <= 0:
            le_zero += 1
    return le_zero / n


def evaluate(report: ProbeReport) -> None:
    cells = report.cells
    total_cells = len(cells)
    solvent_cells = [c for c in cells if c.solvent]
    winning_cells = [c for c in solvent_cells if c.cum_net > 0]  # survived AND made money
    # Gate 0 (NEW): solvency — no cell may bankrupt the book (a period <= -100% on the short leg)
    g0 = total_cells > 0 and all(c.solvent for c in cells)
    # Gate 1: net positive judged on GEOMETRIC terminal wealth, not arithmetic mean
    median_wealth = statistics.median(c.terminal_wealth for c in cells) if cells else 0.0
    g1 = median_wealth > 1.0
    # Gate 2: robust across grid (≥ 2/3 of cells survive AND make money)
    g2 = total_cells > 0 and len(winning_cells) / total_cells >= 2 / 3
    # Gate 3: not concentration-driven, evaluated on the winning cells only
    worst_conc = max((c.best_period_share for c in winning_cells), default=1.0)
    g3 = bool(winning_cells) and worst_conc <= 0.35
    # Gate 4: significant sign-correct spread on a winning, solvent cell
    g4 = any(c.passes_significant and c.mean_gross_spread > 0 for c in winning_cells)
    # Gate 5: shuffle null kills the edge (mean over solvent cells)
    real = statistics.fmean([c.mean_net_per_period for c in solvent_cells]) if solvent_cells else 0.0
    shuf = statistics.fmean([c.shuffle_mean_net for c in solvent_cells]) if solvent_cells else 0.0
    g5 = real > 0 and (shuf <= 0 or shuf <= 0.25 * real)

    report.gate_detail = {
        "g0_solvency": g0,
        "g1_median_wealth_gt_1": g1,
        "g2_robust_two_thirds": g2,
        "g3_not_concentrated": g3,
        "g4_significant_spread": g4,
        "g5_shuffle_null_dies": g5,
        "winning_cells": len(winning_cells),
        "solvent_cells": len(solvent_cells),
        "total_cells": total_cells,
        "median_terminal_wealth": median_wealth,
        "worst_concentration": worst_conc,
        "real_mean_net": real,
        "shuffle_mean_net": shuf,
    }
    if g0 and g1 and g2 and g3 and g4 and g5:
        report.verdict = "HAS_PULSE"
    elif g0 and g1 and (g2 or g4):
        report.verdict = "WEAK_EDGE"
    else:
        report.verdict = "NO_PULSE"


def run_probe(cfg: ProbeConfig) -> ProbeReport:
    start_ms, end_ms = _to_ms(cfg.start), _to_ms(cfg.end)
    universe = select_universe(cfg.universe_size)
    logger.info("universe (%d): %s", len(universe), ", ".join(universe))
    days, panel = build_panel(universe, start_ms, end_ms)
    panel = {s: v for s, v in panel.items() if len(v) >= cfg.min_history_days}
    report = ProbeReport(
        config=cfg,
        universe=sorted(panel.keys()),
        span_start=datetime.fromtimestamp(days[0] / 1000, UTC).date().isoformat() if days else cfg.start,
        span_end=datetime.fromtimestamp(days[-1] / 1000, UTC).date().isoformat() if days else cfg.end,
    )
    for lookback in cfg.lookbacks:
        for q in cfg.quantiles:
            net, spreads = run_cell(days, panel, cfg, lookback, q)
            if not net:
                continue
            shuf_net, _ = run_cell(
                days, panel, cfg, lookback, q, shuffle=True, rng=random.Random(cfg.seed + 1)
            )
            # Geometric terminal wealth with ruin detection (a period <= -100% bankrupts the book)
            wealth = 1.0
            solvent = True
            n_ruin = 0
            for r in net:
                if r <= -1.0:
                    n_ruin += 1
                wealth *= 1.0 + r
                if wealth <= 0.0:
                    wealth = 0.0
                    solvent = False
                    break
            cum_net = wealth - 1.0
            gains = [r for r in net if r > 0]
            best_share = (max(gains) / sum(gains)) if gains else 1.0
            p = _bootstrap_p_gt0(spreads, cfg.bootstrap, cfg.seed)
            report.cells.append(
                CellResult(
                    lookback=lookback,
                    quantile=q,
                    n_periods=len(net),
                    mean_net_per_period=statistics.fmean(net),
                    mean_gross_spread=statistics.fmean(spreads),
                    cum_net=cum_net,
                    terminal_wealth=wealth,
                    min_period_net=min(net),
                    n_ruin_periods=n_ruin,
                    solvent=solvent,
                    best_period_share=best_share,
                    bootstrap_p=p,
                    shuffle_mean_net=statistics.fmean(shuf_net) if shuf_net else 0.0,
                    passes_net_positive=solvent and cum_net > 0,
                    passes_significant=p < 0.05,
                )
            )
            logger.info(
                "cell L=%dd q=%.2f: periods=%d mean_net=%.3f%% wealth=%.2fx solvent=%s "
                "ruin=%d min=%.1f%% conc=%.2f p=%.3f shuf=%.3f%%",
                lookback, q, len(net), statistics.fmean(net) * 100, wealth, solvent,
                n_ruin, min(net) * 100, best_share, p,
                (statistics.fmean(shuf_net) * 100) if shuf_net else 0.0,
            )
    evaluate(report)
    return report


def render_md(report: ProbeReport) -> str:
    c = report.config
    lines = [
        "# Cross-Sectional Altcoin Momentum Probe — Result",
        "",
        f"**Verdict:** **{report.verdict}**",
        f"**Date:** {datetime.now(UTC).date().isoformat()}",
        f"**Universe:** {len(report.universe)} USDT perps (top by 24h quote volume)",
        f"**Span:** {report.span_start} → {report.span_end}",
        f"**Costs:** {c.cost_bps:.1f} bps/leg turnover + {c.funding_bps_per_day:.1f} bps/day funding",
        "",
        "## Grid (geometric terminal wealth, ruin-aware)",
        "",
        "| lookback | quantile | periods | mean_net% | wealth(x) | solvent | ruin_periods | min_period% | concentration | bootstrap_p | shuffle_net% |",
        "|---:|---:|---:|---:|---:|:--:|---:|---:|---:|---:|---:|",
    ]
    for cell in report.cells:
        lines.append(
            f"| {cell.lookback}d | {cell.quantile:.2f} | {cell.n_periods} | "
            f"{cell.mean_net_per_period * 100:.3f} | {cell.terminal_wealth:.2f} | "
            f"{'yes' if cell.solvent else 'NO'} | {cell.n_ruin_periods} | "
            f"{cell.min_period_net * 100:.1f} | {cell.best_period_share:.2f} | "
            f"{cell.bootstrap_p:.3f} | {cell.shuffle_mean_net * 100:.3f} |"
        )
    g = report.gate_detail
    lines += [
        "",
        "## Pre-registered gates",
        "",
        f"- G0 solvency (no cell bankrupts the book): **{g.get('g0_solvency')}** "
        f"({g.get('solvent_cells')}/{g.get('total_cells')} cells solvent)",
        f"- G1 median terminal wealth > 1.0: **{g.get('g1_median_wealth_gt_1')}** "
        f"(median {float(g.get('median_terminal_wealth', 0.0)):.2f}x)",
        f"- G2 robust ≥⅔ cells survive AND profit: **{g.get('g2_robust_two_thirds')}** "
        f"({g.get('winning_cells')}/{g.get('total_cells')})",
        f"- G3 not concentration-driven (≤0.35): **{g.get('g3_not_concentrated')}** "
        f"(worst {float(g.get('worst_concentration', 1.0)):.2f})",
        f"- G4 significant sign-correct spread (p<0.05): **{g.get('g4_significant_spread')}**",
        f"- G5 shuffle null dies: **{g.get('g5_shuffle_null_dies')}** "
        f"(real {float(g.get('real_mean_net', 0.0)) * 100:.3f}% vs shuffled "
        f"{float(g.get('shuffle_mean_net', 0.0)) * 100:.3f}%)",
        "",
        f"**Overall verdict: {report.verdict}**",
    ]
    return "\n".join(lines)


def parse_args() -> tuple[ProbeConfig, str]:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--universe-size", type=int, default=40)
    p.add_argument("--lookbacks", default="7,14,30")
    p.add_argument("--quantiles", default="0.2,0.3")
    p.add_argument("--hold-days", type=int, default=7)
    p.add_argument("--skip-recent-days", type=int, default=1)
    p.add_argument("--cost-bps", type=float, default=12.0)
    p.add_argument("--funding-bps-per-day", type=float, default=3.0)
    p.add_argument("--start", default="2023-01-01")
    p.add_argument("--end", default="2026-06-01")
    p.add_argument("--min-history-days", type=int, default=120)
    p.add_argument("--bootstrap", type=int, default=1000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--output-dir", default="research/rbi_loop/xs-altcoin-momentum-v0")
    a = p.parse_args()
    cfg = ProbeConfig(
        universe_size=a.universe_size,
        lookbacks=tuple(int(x) for x in a.lookbacks.split(",")),
        quantiles=tuple(float(x) for x in a.quantiles.split(",")),
        hold_days=a.hold_days,
        skip_recent_days=a.skip_recent_days,
        cost_bps=a.cost_bps,
        funding_bps_per_day=a.funding_bps_per_day,
        start=a.start,
        end=a.end,
        min_history_days=a.min_history_days,
        bootstrap=a.bootstrap,
        seed=a.seed,
    )
    return cfg, a.output_dir


def main() -> int:
    configure_logger("INFO")
    cfg, output_dir = parse_args()
    report = run_probe(cfg)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "verdict": report.verdict,
        "universe": report.universe,
        "span_start": report.span_start,
        "span_end": report.span_end,
        "gate_detail": report.gate_detail,
        "cells": [c.__dict__ for c in report.cells],
        "config": OrderedDict(sorted(cfg.__dict__.items())),
    }
    (out_dir / "probe_result.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (out_dir / "probe_report.md").write_text(render_md(report) + "\n", encoding="utf-8")
    logger.info("verdict=%s -> %s", report.verdict, out_dir / "probe_report.md")
    return 0 if report.verdict == "HAS_PULSE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
