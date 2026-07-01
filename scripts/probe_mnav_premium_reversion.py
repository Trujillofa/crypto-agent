#!/usr/bin/env python3
"""Cheap feasibility probe for crypto-treasury mNAV premium mean-reversion (Gate 1).

Thesis (relative value, NOT direction): equities holding crypto on the balance sheet
trade at market-cap-to-NAV (mNAV) ratios that mean-revert from trailing extremes.

    mnav(i,t) = (shares_outstanding(i,t) * equity_close(i,t))
                / (holdings_units(i,t) * crypto_close(t))

Point-in-time discipline: holdings/shares step-forward-fill from filing dates only.
See docs/specs/mnav-premium-reversion-probe-v0.md.

STEP 0: data-feasibility audit — usable joined mNAV series per seeded name.
STEP 1: H1 mean-reversion from trailing-extreme percentile vs random baseline;
        H2 holds on >= MIN_NAMES_EDGE names (not single-ticker).

Read-only. No DB writes, no orders, no --execute.
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
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_historical import BinanceHistoricalClient, download_klines
from src.utils.logger import configure_logger, get_logger

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE = "HAS_PULSE"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE = "NO_PULSE"

DEFAULT_START = "2024-01-01"
DEFAULT_END = "2026-06-01"
DEFAULT_TRAILING_WINDOW_DAYS = 180
DEFAULT_EXTREME_PCT = 10
DEFAULT_HORIZONS = (10, 21)
DEFAULT_MIN_NAMES = 4
DEFAULT_MIN_NAMES_EDGE = 3
# ~9 months; spec asks for >=12 months where available but allows gap-tolerant joins
# (SBET/DFDV treasuries begin mid-2025).
DEFAULT_MIN_TRADING_DAYS = 180
DEFAULT_RANDOM_BASELINE_SAMPLES = 200
DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_SIGNIFICANCE_ALPHA = 0.05
# Minimum normalized edge (fractional convergence toward median) vs baseline.
DEFAULT_MIN_FRACTIONAL_EDGE = 0.02
DEFAULT_MAX_SINGLE_EVENT_SHARE = 0.25
DEFAULT_MIN_EVENTS = 5
DEFAULT_MIN_DENOMINATOR = 1e-6
# After fixing percentile cuts, expect ~10-25% of eligible days flagged.
EXPECTED_EVENT_FRACTION_LO = 0.05
EXPECTED_EVENT_FRACTION_HI = 0.30
DEFAULT_SEED_CSV = Path("data/treasury_equities/mnav_universe.csv")

# MSTR 10-for-1 split 2024-08-08; yfinance prices are split-adjusted retroactively.
MSTR_SPLIT_DATE = date(2024, 8, 8)
MSTR_SPLIT_RATIO = 10


@dataclass(frozen=True)
class DisclosureRow:
    ticker: str
    crypto_symbol: str
    binance_symbol: str
    as_of_date: date
    holdings_units: float
    shares_outstanding: float
    source: str


@dataclass(frozen=True)
class ProbeConfig:
    tickers: tuple[str, ...]
    start: str
    end: str
    trailing_window_days: int
    extreme_pct: float
    horizons: tuple[int, ...]
    min_names: int
    min_names_edge: int
    min_trading_days: int
    random_baseline_samples: int
    seed_csv: Path
    rng_seed: int


@dataclass(frozen=True)
class NameAudit:
    ticker: str
    rows: int
    span_days: float
    equity_source: str
    max_disclosure_gap_days: int
    stale_disclosure: bool
    usable: bool


@dataclass(frozen=True)
class HorizonResult:
    horizon: int
    event_count: int
    eligible_days: int
    event_fraction: float
    event_mean_convergence: float
    baseline_mean_convergence: float
    edge_vs_baseline: float
    p_value: float
    max_single_event_share: float
    concentration_ok: bool
    h1_pass: bool


@dataclass(frozen=True)
class NameEdgeResult:
    ticker: str
    trading_days: int
    horizon_results: tuple[HorizonResult, ...]
    h1_pass: bool


@dataclass(frozen=True)
class DataAudit:
    total_names: int
    usable_names: int
    per_name: tuple[NameAudit, ...]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class ProbeReport:
    config: dict[str, object]
    data_audit: DataAudit
    name_results: tuple[NameEdgeResult, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _parse_dt(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw.strip())


def load_disclosures(csv_path: Path) -> list[DisclosureRow]:
    rows: list[DisclosureRow] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for entry in reader:
            rows.append(
                DisclosureRow(
                    ticker=entry["ticker"].strip(),
                    crypto_symbol=entry["crypto_symbol"].strip(),
                    binance_symbol=entry["binance_symbol"].strip(),
                    as_of_date=_parse_date(entry["as_of_date"]),
                    holdings_units=float(entry["holdings_units"]),
                    shares_outstanding=float(entry["shares_outstanding"]),
                    source=entry["source"].strip(),
                )
            )
    return sorted(rows, key=lambda r: (r.ticker, r.as_of_date))


def normalize_shares_outstanding(ticker: str, as_of: date, shares: float) -> float:
    """Align pre-split MSTR share counts with yfinance split-adjusted prices."""
    if ticker == "MSTR" and as_of < MSTR_SPLIT_DATE:
        return shares * MSTR_SPLIT_RATIO
    return shares


def forward_fill_disclosures(
    disclosures: Sequence[DisclosureRow],
    trading_days: Sequence[date],
) -> dict[date, tuple[float, float]]:
    """Return day -> (holdings_units, shares_outstanding) using point-in-time step fill."""
    by_ticker = sorted(disclosures, key=lambda r: r.as_of_date)
    if not by_ticker:
        return {}

    out: dict[date, tuple[float, float]] = {}
    cursor = 0
    active_holdings = 0.0
    active_shares = 0.0

    for day in trading_days:
        while cursor < len(by_ticker) and by_ticker[cursor].as_of_date <= day:
            row = by_ticker[cursor]
            active_holdings = row.holdings_units
            active_shares = normalize_shares_outstanding(
                row.ticker, row.as_of_date, row.shares_outstanding
            )
            cursor += 1
        if cursor > 0:
            out[day] = (active_holdings, active_shares)
    return out


def compute_mnav(
    equity_close: float,
    crypto_close: float,
    holdings_units: float,
    shares_outstanding: float,
) -> float | None:
    if holdings_units <= 0 or shares_outstanding <= 0 or crypto_close <= 0 or equity_close <= 0:
        return None
    crypto_nav = holdings_units * crypto_close
    market_cap = shares_outstanding * equity_close
    return market_cap / crypto_nav


def build_mnav_series(
    trading_days: Sequence[date],
    equity_closes: dict[date, float],
    crypto_closes: dict[date, float],
    filled: dict[date, tuple[float, float]],
) -> list[tuple[date, float]]:
    series: list[tuple[date, float]] = []
    for day in trading_days:
        if day not in filled or day not in equity_closes or day not in crypto_closes:
            continue
        holdings, shares = filled[day]
        mnav = compute_mnav(equity_closes[day], crypto_closes[day], holdings, shares)
        if mnav is not None:
            series.append((day, mnav))
    return series


def max_disclosure_gap_days(disclosures: Sequence[DisclosureRow], last_day: date) -> int:
    if not disclosures:
        return 0
    points = sorted({row.as_of_date for row in disclosures})
    gaps = [(points[i] - points[i - 1]).days for i in range(1, len(points))]
    gaps.append((last_day - points[-1]).days)
    return max(gaps) if gaps else 0


def _percentile_threshold(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[rank]


def _trailing_window(values: Sequence[float], end_idx: int, window: int) -> Sequence[float]:
    start = max(0, end_idx - window + 1)
    return values[start : end_idx + 1]


def detect_extreme_events(
    mnav_series: Sequence[tuple[date, float]],
    trailing_window: int,
    extreme_pct: float,
    horizon: int,
) -> tuple[list[int], list[int], int]:
    """Return top/bottom extreme indices and eligible-day count (pre-horizon window required).

    Top-extreme uses the (100 - extreme_pct) percentile (e.g. 90th when extreme_pct=10).
    Bottom-extreme uses the extreme_pct percentile (e.g. 10th).
    """
    values = [value for _, value in mnav_series]
    top_events: list[int] = []
    bottom_events: list[int] = []
    last_idx = len(values) - horizon - 1
    eligible_days = 0
    for idx in range(trailing_window, max(trailing_window, last_idx + 1)):
        window = _trailing_window(values, idx, trailing_window)
        if len(window) < trailing_window // 2:
            continue
        eligible_days += 1
        current = values[idx]
        high_cut = _percentile_threshold(window, 100.0 - extreme_pct)
        low_cut = _percentile_threshold(window, extreme_pct)
        if current > high_cut:
            top_events.append(idx)
        elif current < low_cut:
            bottom_events.append(idx)
    return top_events, bottom_events, eligible_days


def _fractional_convergence(
    current: float,
    future: float,
    median: float,
    extreme_side: str,
    *,
    min_denominator: float = DEFAULT_MIN_DENOMINATOR,
) -> float | None:
    """Unitless fractional move toward trailing median (positive = mean-reversion)."""
    if extreme_side == "high":
        denom = current - median
        if abs(denom) < min_denominator:
            return None
        return (current - future) / denom
    denom = median - current
    if abs(denom) < min_denominator:
        return None
    return (future - current) / denom


def _convergence_toward_median(
    current: float,
    future: float,
    median: float,
    *,
    min_denominator: float = DEFAULT_MIN_DENOMINATOR,
) -> float | None:
    if current > median:
        return _fractional_convergence(
            current, future, median, "high", min_denominator=min_denominator
        )
    if current < median:
        return _fractional_convergence(
            current, future, median, "low", min_denominator=min_denominator
        )
    return None


def _max_single_event_share(scores: Sequence[float]) -> float:
    if not scores:
        return 0.0
    total = sum(abs(score) for score in scores)
    if total <= 0.0:
        return 0.0
    return max(abs(score) for score in scores) / total


def _bootstrap_mean_diff_p_value(
    event_scores: Sequence[float],
    baseline_scores: Sequence[float],
    rng: random.Random,
    *,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> float:
    if len(event_scores) < 2 or len(baseline_scores) < 2:
        return 1.0
    observed = statistics.mean(event_scores) - statistics.mean(baseline_scores)
    pool = list(event_scores) + list(baseline_scores)
    n_events = len(event_scores)
    extreme = 0
    for _ in range(iterations):
        shuffled = list(pool)
        rng.shuffle(shuffled)
        sample_events = shuffled[:n_events]
        sample_baseline = shuffled[n_events:]
        diff = statistics.mean(sample_events) - statistics.mean(sample_baseline)
        if diff >= observed:
            extreme += 1
    return extreme / iterations


def analyze_mean_reversion(
    mnav_series: Sequence[tuple[date, float]],
    config: ProbeConfig,
) -> tuple[HorizonResult, ...]:
    values = [value for _, value in mnav_series]
    results: list[HorizonResult] = []

    for horizon in config.horizons:
        top_events, bottom_events, eligible_days = detect_extreme_events(
            mnav_series, config.trailing_window_days, config.extreme_pct, horizon
        )
        event_scores: list[float] = []
        for idx in top_events:
            window = _trailing_window(values, idx, config.trailing_window_days)
            median = statistics.median(window)
            score = _fractional_convergence(values[idx], values[idx + horizon], median, "high")
            if score is not None:
                event_scores.append(score)
        for idx in bottom_events:
            window = _trailing_window(values, idx, config.trailing_window_days)
            median = statistics.median(window)
            score = _fractional_convergence(values[idx], values[idx + horizon], median, "low")
            if score is not None:
                event_scores.append(score)

        event_count = len(event_scores)
        event_fraction = event_count / eligible_days if eligible_days else 0.0

        sample_rng = random.Random(config.rng_seed + horizon)
        baseline_scores: list[float] = []
        eligible = list(range(config.trailing_window_days, len(values) - horizon))
        event_set = set(top_events) | set(bottom_events)
        pool = [i for i in eligible if i not in event_set]
        sample_size = min(config.random_baseline_samples, len(pool))
        for idx in sample_rng.sample(pool, k=sample_size) if sample_size else []:
            window = _trailing_window(values, idx, config.trailing_window_days)
            median = statistics.median(window)
            score = _convergence_toward_median(values[idx], values[idx + horizon], median)
            if score is not None:
                baseline_scores.append(score)

        event_mean = statistics.mean(event_scores) if event_scores else 0.0
        baseline_mean = statistics.mean(baseline_scores) if baseline_scores else 0.0
        edge = event_mean - baseline_mean
        bootstrap_rng = random.Random(config.rng_seed + horizon + 10_000)
        p_value = _bootstrap_mean_diff_p_value(event_scores, baseline_scores, bootstrap_rng)
        max_event_share = _max_single_event_share(event_scores)
        concentration_ok = max_event_share <= DEFAULT_MAX_SINGLE_EVENT_SHARE
        h1_pass = (
            event_count >= DEFAULT_MIN_EVENTS
            and edge >= DEFAULT_MIN_FRACTIONAL_EDGE
            and p_value < DEFAULT_SIGNIFICANCE_ALPHA
            and concentration_ok
        )
        results.append(
            HorizonResult(
                horizon=horizon,
                event_count=event_count,
                eligible_days=eligible_days,
                event_fraction=event_fraction,
                event_mean_convergence=event_mean,
                baseline_mean_convergence=baseline_mean,
                edge_vs_baseline=edge,
                p_value=p_value,
                max_single_event_share=max_event_share,
                concentration_ok=concentration_ok,
                h1_pass=h1_pass,
            )
        )
    return tuple(results)


def audit_data(
    audits: Sequence[NameAudit],
    config: ProbeConfig,
) -> DataAudit:
    usable = sum(1 for audit in audits if audit.usable)
    blocked = usable < config.min_names
    reason = (
        f"only {usable} names have usable joined mNAV series (need >= {config.min_names})"
        if blocked
        else None
    )
    return DataAudit(
        total_names=len(audits),
        usable_names=usable,
        per_name=tuple(audits),
        blocked=blocked,
        blocked_reason=reason,
    )


def decide_verdict(
    audit: DataAudit,
    name_results: Sequence[NameEdgeResult],
    config: ProbeConfig,
) -> tuple[str, str, tuple[str, ...]]:
    if audit.blocked:
        return (BLOCKED_ON_DATA, BLOCKED_ON_DATA, (audit.blocked_reason or "data gate failed",))

    passing_names = [result for result in name_results if result.h1_pass]
    h1_ok = len(passing_names) >= 1
    h2_ok = len(passing_names) >= config.min_names_edge

    if h1_ok and h2_ok:
        tickers = ", ".join(sorted({result.ticker for result in passing_names}))
        return (
            "OK",
            HAS_PULSE,
            (
                f"mNAV mean-reversion clears random baseline on {len(passing_names)} names "
                f"({tickers})",
            ),
        )
    if h1_ok and not h2_ok:
        tickers = ", ".join(sorted({result.ticker for result in passing_names}))
        return (
            "OK",
            WEAK_EDGE,
            (
                f"H1 passes on {len(passing_names)} name(s) ({tickers}) but "
                f"needs >= {config.min_names_edge} for H2",
            ),
        )
    return ("OK", NO_PULSE, ("no name shows mNAV extreme mean-reversion above random baseline",))


def fetch_equity_closes(ticker: str, start: str, end: str) -> dict[date, float]:
    frame = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
    closes: dict[date, float] = {}
    for idx, row in frame.iterrows():
        day = idx.date() if hasattr(idx, "date") else idx.to_pydatetime().date()
        closes[day] = float(row["Close"])
    return closes


async def fetch_crypto_closes(symbol: str, start: str, end: str) -> dict[date, float]:
    async with BinanceHistoricalClient() as client:
        rows = await download_klines(client, symbol, "1d", start, end)
    closes: dict[date, float] = {}
    for row in rows:
        ts = row["time"]
        day = (
            ts.date()
            if isinstance(ts, datetime)
            else datetime.fromtimestamp(float(ts), tz=UTC).date()
        )
        closes[day] = float(row["close_price"])
    return closes


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.mnav_premium")

    disclosures = load_disclosures(config.seed_csv)
    if config.tickers:
        allowed = set(config.tickers)
        disclosures = [row for row in disclosures if row.ticker in allowed]

    by_ticker: dict[str, list[DisclosureRow]] = {}
    for row in disclosures:
        by_ticker.setdefault(row.ticker, []).append(row)

    start_day = _parse_date(config.start)
    end_day = _parse_date(config.end)

    audits: list[NameAudit] = []
    name_results: list[NameEdgeResult] = []

    for ticker, ticker_rows in sorted(by_ticker.items()):
        binance_symbol = ticker_rows[0].binance_symbol
        equity_closes = fetch_equity_closes(ticker, config.start, config.end)
        crypto_closes = await fetch_crypto_closes(binance_symbol, config.start, config.end)

        trading_days = sorted(set(equity_closes) & set(crypto_closes))
        trading_days = [day for day in trading_days if start_day <= day <= end_day]
        filled = forward_fill_disclosures(ticker_rows, trading_days)
        mnav_series = build_mnav_series(trading_days, equity_closes, crypto_closes, filled)

        span_days = 0.0
        if len(mnav_series) >= 2:
            span_days = (mnav_series[-1][0] - mnav_series[0][0]).days

        gap_days = max_disclosure_gap_days(ticker_rows, end_day)
        usable = len(mnav_series) >= config.min_trading_days
        audits.append(
            NameAudit(
                ticker=ticker,
                rows=len(mnav_series),
                span_days=span_days,
                equity_source="yfinance",
                max_disclosure_gap_days=gap_days,
                stale_disclosure=gap_days > 45,
                usable=usable,
            )
        )

        if not usable:
            logger.warning(
                "%s: only %d mNAV rows (need %d)", ticker, len(mnav_series), config.min_trading_days
            )
            name_results.append(
                NameEdgeResult(
                    ticker=ticker, trading_days=len(mnav_series), horizon_results=(), h1_pass=False
                )
            )
            continue

        horizon_results = analyze_mean_reversion(mnav_series, config)
        h1_pass = bool(horizon_results) and all(result.h1_pass for result in horizon_results)
        for result in horizon_results:
            if not (
                EXPECTED_EVENT_FRACTION_LO <= result.event_fraction <= EXPECTED_EVENT_FRACTION_HI
            ):
                logger.warning(
                    "%s H=%d: event_fraction=%.1f%% outside expected %.0f-%.0f%%",
                    ticker,
                    result.horizon,
                    result.event_fraction * 100.0,
                    EXPECTED_EVENT_FRACTION_LO * 100.0,
                    EXPECTED_EVENT_FRACTION_HI * 100.0,
                )
            logger.info(
                "%s H=%d: event_fraction=%.1f%%, events=%d/%d, edge=%+.4f, "
                "baseline=%+.4f, p=%.4f, max_event_share=%.1f%%, H1=%s",
                ticker,
                result.horizon,
                result.event_fraction * 100.0,
                result.event_count,
                result.eligible_days,
                result.edge_vs_baseline,
                result.baseline_mean_convergence,
                result.p_value,
                result.max_single_event_share * 100.0,
                result.h1_pass,
            )
        logger.info(
            "%s: %d mNAV days, H1=%s (all horizons required)", ticker, len(mnav_series), h1_pass
        )
        name_results.append(
            NameEdgeResult(
                ticker=ticker,
                trading_days=len(mnav_series),
                horizon_results=horizon_results,
                h1_pass=h1_pass,
            )
        )

    audit = audit_data(audits, config)
    status, verdict, reasons = decide_verdict(audit, name_results, config)
    return ProbeReport(
        config={
            "tickers": list(config.tickers) if config.tickers else sorted(by_ticker),
            "start": config.start,
            "end": config.end,
            "trailing_window_days": config.trailing_window_days,
            "extreme_pct": config.extreme_pct,
            "horizons": list(config.horizons),
            "min_names": config.min_names,
            "min_names_edge": config.min_names_edge,
            "seed_csv": str(config.seed_csv),
        },
        data_audit=audit,
        name_results=tuple(name_results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = ["# mNAV Premium-Reversion Probe — Report", ""]
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_mnav_premium_reversion.py`")
    lines.append("**Framing:** relative value (equity mNAV vs crypto NAV), not price forecast.")
    lines.append("")
    audit = report.data_audit
    lines.append("## STEP 0 — Data feasibility")
    lines.append(f"- Names: {audit.total_names}; usable: {audit.usable_names}")
    for item in audit.per_name:
        stale = " **stale-disclosure**" if item.stale_disclosure else ""
        lines.append(
            f"- {item.ticker}: {item.rows} rows, span {item.span_days:.0f}d, "
            f"equity={item.equity_source}, max gap {item.max_disclosure_gap_days}d{stale}"
        )
    lines.append(
        f"- Blocked: {audit.blocked}"
        + (f" — {audit.blocked_reason}" if audit.blocked_reason else "")
    )
    lines.append("")
    lines.append("## STEP 1 — Per-name mean-reversion")
    lines.append("")
    lines.append(
        "| Ticker | Days | H1 | H=10 edge (p) | H=21 edge (p) | Events (10/21) | Event % |"
    )
    lines.append(
        "|--------|------|----|---------------|---------------|----------------|---------|"
    )
    for result in report.name_results:
        by_h = {item.horizon: item for item in result.horizon_results}
        h10 = by_h.get(10)
        h21 = by_h.get(21)
        edge10 = f"{h10.edge_vs_baseline:+.4f} ({h10.p_value:.3f})" if h10 else "n/a"
        edge21 = f"{h21.edge_vs_baseline:+.4f} ({h21.p_value:.3f})" if h21 else "n/a"
        events = f"{h10.event_count if h10 else 0}/{h21.event_count if h21 else 0}"
        frac = (
            f"{h10.event_fraction * 100:.0f}/{h21.event_fraction * 100:.0f}"
            if h10 and h21
            else "n/a"
        )
        lines.append(
            f"| {result.ticker} | {result.trading_days} | "
            f"{'Y' if result.h1_pass else 'n'} | {edge10} | {edge21} | {events} | {frac} |"
        )
    lines.append("")
    lines.append("## Reasons")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols", nargs="+", default=[], help="Ticker filter (alias: treasury names)"
    )
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--trailing-window-days", type=int, default=DEFAULT_TRAILING_WINDOW_DAYS)
    parser.add_argument("--extreme-pct", type=float, default=DEFAULT_EXTREME_PCT)
    parser.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=list(DEFAULT_HORIZONS),
    )
    parser.add_argument("--min-names", type=int, default=DEFAULT_MIN_NAMES)
    parser.add_argument("--min-names-edge", type=int, default=DEFAULT_MIN_NAMES_EDGE)
    parser.add_argument(
        "--output-dir",
        default=str(Path("research/rbi_loop/mnav-premium-reversion-v0")),
    )
    parser.add_argument("--seed-csv", default=str(DEFAULT_SEED_CSV))
    parser.add_argument("--rng-seed", type=int, default=42)
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        tickers=tuple(args.symbols),
        start=args.start,
        end=args.end,
        trailing_window_days=args.trailing_window_days,
        extreme_pct=args.extreme_pct,
        horizons=tuple(args.horizons),
        min_names=args.min_names,
        min_names_edge=args.min_names_edge,
        min_trading_days=DEFAULT_MIN_TRADING_DAYS,
        random_baseline_samples=DEFAULT_RANDOM_BASELINE_SAMPLES,
        seed_csv=Path(args.seed_csv),
        rng_seed=args.rng_seed,
    )
    report = await run_probe(config)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": report.config,
        "data_audit": asdict(report.data_audit),
        "name_results": [
            {
                **asdict(result),
                "horizon_results": [asdict(item) for item in result.horizon_results],
            }
            for result in report.name_results
        ],
        "status": report.status,
        "verdict": report.verdict,
        "reasons": list(report.reasons),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "probe_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report_md = render_report(report)
    (out_dir / "probe_report.md").write_text(report_md + "\n", encoding="utf-8")

    print(report_md)
    print(f"\nVERDICT: {report.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
