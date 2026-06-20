#!/usr/bin/env python3
"""Cheap feasibility probe for the token-unlock 72h shock short edge (Gate 1).

Thesis (directional SHORT, not mean-reversion): a Binance-listed token exhibits a
reliable *negative* forward return in the 72h window after a scheduled supply unlock,
distinct from a random same-length window and surviving a conservative cost haircut.
This is the first lane keyed to an exogenous, ex-ante-known *event calendar* rather
than another OHLCV-structure transform (see docs/reports/research-consolidation-2026-06-19.md).

STEP 0 (mandatory gate): data-feasibility audit. We re-fetch OHLCV *fresh* from Binance
for each unlock symbol — we do NOT trust the source paper's price columns (some of which
are reconstructed templates). If too few events have usable independent price data, the
verdict is BLOCKED_ON_DATA and no edge claim is made.

STEP 1: pooled short-edge test across events.
  H1 (raw short edge): fraction of events with negative 72h return >= threshold AND
      mean short PnL net of a conservative cost haircut > 0 AND beats a random-window baseline.
  H2 (BTC-relative robustness): the negative drift persists after subtracting BTC's
      same-window return (controls market beta).
  HAS_PULSE := H1 and H2.  WEAK_EDGE := exactly one.  NO_PULSE := neither.

Read-only. Fetches public Binance spot klines (no API key, no DB writes, no execution).
See docs/specs/token-unlock-72h-short-probe-v0.md.
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_historical import BinanceHistoricalClient, download_klines
from src.utils.logger import configure_logger, get_logger

DEFAULT_EVENTS_CSV = (
    Path(__file__).resolve().parent.parent / "data/token_unlocks/binance_unlock_events.csv"
)

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE = "HAS_PULSE"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE = "NO_PULSE"

# Gates / parameters (conservative; documented in the brief).
HORIZON_HOURS = 72
PRE_ENTRY_HOURS = 1  # enter at the first bar at/after the unlock instant
BASELINE_TRAIL_DAYS = 45  # trailing window from which random baseline windows are drawn
POST_BUFFER_HOURS = 6
MIN_USABLE_EVENTS = 25
NEG_FRACTION_THRESHOLD = 60.0  # % of events that must be negative
# Conservative round-trip cost floor for shorting an alt perp/spot over 72h:
# taker fees (~0.08%) + ~9 funding windows + slippage on thin alts. 1.0% is a floor,
# not a model — the gross effect (~ -17%) dwarfs it if the signal is real.
COST_HAIRCUT_PCT = 1.0
BASELINE_SEED = 42
BARS_PER_EVENT_LIMIT = 4000


@dataclass(frozen=True)
class UnlockEvent:
    token_symbol: str
    binance_symbol: str
    unlock_ts: datetime
    unlock_type: str
    recipient_category: str
    source: str


@dataclass(frozen=True)
class HourlyBar:
    time: datetime
    close_price: float


@dataclass(frozen=True)
class EventResult:
    token_symbol: str
    binance_symbol: str
    unlock_ts: str
    usable: bool
    skip_reason: str | None
    raw_return_pct: float | None
    btc_return_pct: float | None
    btc_relative_return_pct: float | None
    short_pnl_net_pct: float | None


@dataclass(frozen=True)
class ProbeConfig:
    events_csv: Path
    interval: str
    horizon_hours: int
    baseline_trail_days: int
    min_usable_events: int
    neg_fraction_threshold: float
    cost_haircut_pct: float
    baseline_seed: int


@dataclass(frozen=True)
class DataAudit:
    total_events: int
    usable_events: int
    skipped_no_data: int
    skipped_short_history: int
    sample_symbols: tuple[str, ...]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class EdgeMetrics:
    usable_events: int
    neg_fraction_raw_pct: float
    mean_raw_return_pct: float
    median_raw_return_pct: float
    mean_short_pnl_net_pct: float
    baseline_mean_raw_return_pct: float
    excess_short_vs_baseline_pct: float
    neg_fraction_relative_pct: float
    mean_btc_relative_return_pct: float
    h1_raw_short_pass: bool
    h2_btc_relative_pass: bool


@dataclass(frozen=True)
class ProbeReport:
    config: dict[str, object]
    data_audit: DataAudit
    edge: EdgeMetrics | None
    event_results: tuple[EventResult, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _parse_ts(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def load_events(csv_path: Path) -> tuple[UnlockEvent, ...]:
    events: list[UnlockEvent] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            events.append(
                UnlockEvent(
                    token_symbol=row["token_symbol"].strip(),
                    binance_symbol=row["binance_symbol"].strip(),
                    unlock_ts=_parse_ts(row["unlock_ts_utc"]),
                    unlock_type=row.get("unlock_type", "").strip(),
                    recipient_category=row.get("recipient_category", "").strip(),
                    source=row.get("source", "").strip(),
                )
            )
    return tuple(sorted(events, key=lambda e: e.unlock_ts))


def _to_bars(rows: Sequence[dict[str, object]]) -> list[HourlyBar]:
    bars: list[HourlyBar] = []
    for row in rows:
        ts = row["time"]
        close = float(row["close_price"])
        if isinstance(ts, datetime) and close > 0:
            bars.append(HourlyBar(time=ts.astimezone(UTC), close_price=close))
    return bars


def entry_index(bars: Sequence[HourlyBar], unlock_ts: datetime) -> int | None:
    """First bar whose open is at/after the unlock instant."""
    for index, bar in enumerate(bars):
        if bar.time >= unlock_ts:
            return index
    return None


def forward_return_pct(
    bars: Sequence[HourlyBar], entry_idx: int, horizon_bars: int
) -> float | None:
    exit_idx = entry_idx + horizon_bars
    if exit_idx >= len(bars):
        return None
    entry = bars[entry_idx].close_price
    if entry <= 0:
        return None
    return (bars[exit_idx].close_price / entry - 1.0) * 100.0


def baseline_mean_return(
    bars: Sequence[HourlyBar],
    *,
    entry_idx: int,
    horizon_bars: int,
    trail_bars: int,
    seed: int,
) -> float | None:
    """Mean forward return over random non-event windows in the trailing history."""
    lo = max(0, entry_idx - trail_bars)
    hi = entry_idx - horizon_bars
    if hi - lo < 5:
        return None
    rng = random.Random(seed + entry_idx)
    sample = rng.sample(range(lo, hi), min(10, hi - lo))
    rets = [r for i in sample if (r := forward_return_pct(bars, i, horizon_bars)) is not None]
    return statistics.mean(rets) if rets else None


async def evaluate_event(
    client: BinanceHistoricalClient,
    event: UnlockEvent,
    config: ProbeConfig,
    btc_cache: dict[str, list[HourlyBar]],
) -> EventResult:
    horizon_bars = config.horizon_hours  # 1 bar == 1h
    trail_bars = config.baseline_trail_days * 24
    window_start = event.unlock_ts - timedelta(days=config.baseline_trail_days)
    window_end = event.unlock_ts + timedelta(hours=config.horizon_hours + POST_BUFFER_HOURS)
    start_s = window_start.strftime("%Y-%m-%dT%H:%M:%S")
    end_s = window_end.strftime("%Y-%m-%dT%H:%M:%S")

    def skip(reason: str) -> EventResult:
        return EventResult(
            token_symbol=event.token_symbol,
            binance_symbol=event.binance_symbol,
            unlock_ts=event.unlock_ts.isoformat(),
            usable=False,
            skip_reason=reason,
            raw_return_pct=None,
            btc_return_pct=None,
            btc_relative_return_pct=None,
            short_pnl_net_pct=None,
        )

    try:
        token_rows = await download_klines(
            client, event.binance_symbol, config.interval, start_s, end_s
        )
    except Exception as exc:  # noqa: BLE001 — probe must tolerate per-symbol fetch failures
        return skip(f"fetch_error:{type(exc).__name__}")

    token_bars = _to_bars(token_rows)[:BARS_PER_EVENT_LIMIT]
    if not token_bars:
        return skip("no_binance_data")

    t_entry = entry_index(token_bars, event.unlock_ts)
    if t_entry is None:
        return skip("no_bar_after_unlock")
    raw_ret = forward_return_pct(token_bars, t_entry, horizon_bars)
    if raw_ret is None:
        return skip("insufficient_post_horizon")

    # BTC benchmark for the same window (cached per window-start day).
    btc_key = window_start.strftime("%Y-%m-%d")
    if btc_key not in btc_cache:
        try:
            btc_rows = await download_klines(client, "BTCUSDT", config.interval, start_s, end_s)
            btc_cache[btc_key] = _to_bars(btc_rows)
        except Exception:  # noqa: BLE001
            btc_cache[btc_key] = []
    btc_bars = btc_cache[btc_key]
    btc_ret: float | None = None
    if btc_bars:
        b_entry = entry_index(btc_bars, event.unlock_ts)
        if b_entry is not None:
            btc_ret = forward_return_pct(btc_bars, b_entry, horizon_bars)

    baseline = baseline_mean_return(
        token_bars,
        entry_idx=t_entry,
        horizon_bars=horizon_bars,
        trail_bars=trail_bars,
        seed=config.baseline_seed,
    )
    # Short PnL = inverse of price move, net of a conservative cost haircut.
    short_pnl_net = -raw_ret - config.cost_haircut_pct
    rel = (raw_ret - btc_ret) if btc_ret is not None else None

    result = EventResult(
        token_symbol=event.token_symbol,
        binance_symbol=event.binance_symbol,
        unlock_ts=event.unlock_ts.isoformat(),
        usable=True,
        skip_reason=None,
        raw_return_pct=raw_ret,
        btc_return_pct=btc_ret,
        btc_relative_return_pct=rel,
        short_pnl_net_pct=short_pnl_net,
    )
    # stash baseline on the side-channel cache for aggregation
    result_baselines.append(baseline)
    return result


# module-level accumulator for per-event baselines (kept simple for a one-shot probe)
result_baselines: list[float | None] = []


def compute_edge(results: Sequence[EventResult], config: ProbeConfig) -> EdgeMetrics:
    usable = [r for r in results if r.usable and r.raw_return_pct is not None]
    raws = [r.raw_return_pct for r in usable if r.raw_return_pct is not None]
    shorts = [r.short_pnl_net_pct for r in usable if r.short_pnl_net_pct is not None]
    rels = [r.btc_relative_return_pct for r in usable if r.btc_relative_return_pct is not None]
    baselines = [b for b in result_baselines if b is not None]

    neg_raw = sum(1 for x in raws if x < 0)
    neg_frac_raw = neg_raw / len(raws) * 100.0 if raws else 0.0
    mean_raw = statistics.mean(raws) if raws else 0.0
    median_raw = statistics.median(raws) if raws else 0.0
    mean_short = statistics.mean(shorts) if shorts else 0.0
    baseline_mean_raw = statistics.mean(baselines) if baselines else 0.0
    # short edge in excess of the random baseline short return
    excess_short = (-mean_raw) - (-baseline_mean_raw)

    neg_rel = sum(1 for x in rels if x < 0)
    neg_frac_rel = neg_rel / len(rels) * 100.0 if rels else 0.0
    mean_rel = statistics.mean(rels) if rels else 0.0

    h1 = (
        len(raws) >= config.min_usable_events
        and neg_frac_raw >= config.neg_fraction_threshold
        and mean_short > 0.0
        and excess_short > config.cost_haircut_pct
    )
    h2 = len(rels) > 0 and neg_frac_rel >= config.neg_fraction_threshold and mean_rel < 0.0

    return EdgeMetrics(
        usable_events=len(raws),
        neg_fraction_raw_pct=neg_frac_raw,
        mean_raw_return_pct=mean_raw,
        median_raw_return_pct=median_raw,
        mean_short_pnl_net_pct=mean_short,
        baseline_mean_raw_return_pct=baseline_mean_raw,
        excess_short_vs_baseline_pct=excess_short,
        neg_fraction_relative_pct=neg_frac_rel,
        mean_btc_relative_return_pct=mean_rel,
        h1_raw_short_pass=h1,
        h2_btc_relative_pass=h2,
    )


def audit_data(
    events: Sequence[UnlockEvent], results: Sequence[EventResult], config: ProbeConfig
) -> DataAudit:
    usable = sum(1 for r in results if r.usable)
    no_data = sum(1 for r in results if r.skip_reason in {"no_binance_data", "fetch_error"})
    short_hist = sum(
        1 for r in results if r.skip_reason in {"no_bar_after_unlock", "insufficient_post_horizon"}
    )
    blocked = usable < config.min_usable_events
    reason = (
        f"only {usable} events have usable independent Binance data (need >={config.min_usable_events})"
        if blocked
        else None
    )
    return DataAudit(
        total_events=len(events),
        usable_events=usable,
        skipped_no_data=no_data,
        skipped_short_history=short_hist,
        sample_symbols=tuple(e.binance_symbol for e in events[:5]),
        blocked=blocked,
        blocked_reason=reason,
    )


def decide_verdict(audit: DataAudit, edge: EdgeMetrics | None) -> tuple[str, str, tuple[str, ...]]:
    if audit.blocked or edge is None:
        return (
            BLOCKED_ON_DATA,
            BLOCKED_ON_DATA,
            (audit.blocked_reason or "data-feasibility gate failed — edge test not run",),
        )
    reasons: list[str] = []
    if edge.h1_raw_short_pass and edge.h2_btc_relative_pass:
        return (
            "OK",
            HAS_PULSE,
            (
                f"raw short edge passes ({edge.neg_fraction_raw_pct:.1f}% negative, "
                f"mean short net {edge.mean_short_pnl_net_pct:+.2f}%) and survives BTC-relative control "
                f"({edge.neg_fraction_relative_pct:.1f}% negative vs BTC)",
            ),
        )
    if edge.h1_raw_short_pass != edge.h2_btc_relative_pass:
        passed = "raw short (H1)" if edge.h1_raw_short_pass else "BTC-relative (H2)"
        reasons.append(
            f"only {passed} passed — market-beta contamination or thin sample; WEAK_EDGE"
        )
        return ("OK", WEAK_EDGE, tuple(reasons))
    reasons.append(
        f"neither gate passed (neg raw {edge.neg_fraction_raw_pct:.1f}%, "
        f"mean short net {edge.mean_short_pnl_net_pct:+.2f}%, excess {edge.excess_short_vs_baseline_pct:+.2f}%)"
    )
    return ("OK", NO_PULSE, tuple(reasons))


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.token_unlock_shock")
    result_baselines.clear()

    events = load_events(config.events_csv)
    logger.info("loaded %d unlock events from %s", len(events), config.events_csv)

    results: list[EventResult] = []
    btc_cache: dict[str, list[HourlyBar]] = {}
    async with BinanceHistoricalClient() as client:
        for event in events:
            res = await evaluate_event(client, event, config, btc_cache)
            status = "usable" if res.usable else f"skip({res.skip_reason})"
            logger.info(
                "%s @ %s: %s%s",
                event.binance_symbol,
                event.unlock_ts.date(),
                status,
                f" raw={res.raw_return_pct:+.2f}%" if res.raw_return_pct is not None else "",
            )
            results.append(res)

    audit = audit_data(events, results, config)
    edge = None if audit.blocked else compute_edge(results, config)
    status, verdict, reasons = decide_verdict(audit, edge)
    return ProbeReport(
        config={
            "events_csv": str(config.events_csv),
            "interval": config.interval,
            "horizon_hours": config.horizon_hours,
            "min_usable_events": config.min_usable_events,
            "neg_fraction_threshold": config.neg_fraction_threshold,
            "cost_haircut_pct": config.cost_haircut_pct,
        },
        data_audit=audit,
        edge=edge,
        event_results=tuple(results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = ["# Token-Unlock 72h Shock Short Probe — Report", ""]
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_token_unlock_shock.py`")
    lines.append("**Trade framing:** directional SHORT, enter first bar after unlock, hold 72h.")
    lines.append("")
    a = report.data_audit
    lines.append("## STEP 0 — Data feasibility")
    lines.append(f"- Total events: {a.total_events}")
    lines.append(f"- Usable (fresh Binance data): {a.usable_events}")
    lines.append(f"- Skipped (no data / fetch error): {a.skipped_no_data}")
    lines.append(f"- Skipped (short history): {a.skipped_short_history}")
    lines.append(f"- Blocked: {a.blocked}{f' — {a.blocked_reason}' if a.blocked_reason else ''}")
    lines.append("")
    if report.edge is not None:
        e = report.edge
        lines.append("## STEP 1 — Pooled short-edge metrics")
        lines.append(f"- Negative 72h (raw): {e.neg_fraction_raw_pct:.1f}% of {e.usable_events}")
        lines.append(
            f"- Mean raw 72h return: {e.mean_raw_return_pct:+.2f}% (median {e.median_raw_return_pct:+.2f}%)"
        )
        lines.append(
            f"- Mean short PnL net of {report.config['cost_haircut_pct']}% haircut: {e.mean_short_pnl_net_pct:+.2f}%"
        )
        lines.append(f"- Random-window baseline mean raw: {e.baseline_mean_raw_return_pct:+.2f}%")
        lines.append(f"- Excess short vs baseline: {e.excess_short_vs_baseline_pct:+.2f}%")
        lines.append(f"- Negative 72h (BTC-relative): {e.neg_fraction_relative_pct:.1f}%")
        lines.append(f"- Mean BTC-relative 72h: {e.mean_btc_relative_return_pct:+.2f}%")
        lines.append(f"- H1 raw short pass: **{e.h1_raw_short_pass}**")
        lines.append(f"- H2 BTC-relative pass: **{e.h2_btc_relative_pass}**")
        lines.append("")
    lines.append("## Reasons")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-csv", default=str(DEFAULT_EVENTS_CSV))
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--horizon-hours", type=int, default=HORIZON_HOURS)
    parser.add_argument("--min-usable-events", type=int, default=MIN_USABLE_EVENTS)
    parser.add_argument("--neg-fraction-threshold", type=float, default=NEG_FRACTION_THRESHOLD)
    parser.add_argument("--cost-haircut-pct", type=float, default=COST_HAIRCUT_PCT)
    parser.add_argument(
        "--output-dir",
        default=str(Path("research/rbi_loop/token-unlock-72h-short-v0")),
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        events_csv=Path(args.events_csv),
        interval=args.interval,
        horizon_hours=args.horizon_hours,
        baseline_trail_days=BASELINE_TRAIL_DAYS,
        min_usable_events=args.min_usable_events,
        neg_fraction_threshold=args.neg_fraction_threshold,
        cost_haircut_pct=args.cost_haircut_pct,
        baseline_seed=BASELINE_SEED,
    )
    report = await run_probe(config)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": report.config,
        "data_audit": asdict(report.data_audit),
        "edge": asdict(report.edge) if report.edge else None,
        "event_results": [asdict(r) for r in report.event_results],
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
