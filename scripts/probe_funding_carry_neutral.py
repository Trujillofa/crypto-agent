#!/usr/bin/env python3
"""Cheap feasibility probe for delta-neutral funding carry (Gate 1).

Thesis (yield, NOT direction): a delta-neutral position — long spot + short USDT-M perp —
collects the perp funding stream while staying market-neutral. PnL is funding received minus
holding costs, independent of price direction. This is the first lane that changes the
*objective function* away from forecasting price (see docs/specs/funding-carry-neutral-probe-v0.md).

Sign convention (Binance): fundingRate > 0 ⇒ longs pay shorts. A delta-neutral carry holds the
perp SHORT, so per-tick carry = +fundingRate (you receive when funding is positive, pay when
negative). Cumulative gross carry over a continuous hold = Σ fundingRate.

STEP 0 (mandatory gate): data-feasibility audit on the public Binance futures funding history.
If too few funding ticks per symbol → BLOCKED_ON_DATA.

STEP 1: per-symbol carry metrics, pooled into a verdict.
  H1 (carry clears a hurdle): annualized mean funding, net of a conservative annual cost drag,
      exceeds MIN_NET_CARRY_PCT on >= MIN_SYMBOLS symbols.
  H2 (carry is harvestable, not a coin-flip): negative-funding fraction is bounded AND cumulative
      net carry stays positive over the window on >= MIN_SYMBOLS symbols.
  HAS_PULSE := H1 and H2.  WEAK_EDGE := exactly one.  NO_PULSE := neither.

Read-only. Fetches public Binance futures funding (no API key, no DB writes, no execution).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.logger import configure_logger, get_logger

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
FUNDINGS_PER_DAY = 3  # Binance funds every 8h
FUNDINGS_PER_YEAR = FUNDINGS_PER_DAY * 365

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE = "HAS_PULSE"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE = "NO_PULSE"

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_START = "2024-01-01T00:00:00"
DEFAULT_END = "2026-06-01T00:00:00"
# ~12 months of 8h ticks ≈ 1095. Require at least ~9 months of coverage to test carry.
MIN_FUNDING_TICKS = 800
MIN_SYMBOLS = 2
# Hurdle the net annualized carry must clear to justify capital + unbuilt two-leg execution.
MIN_NET_CARRY_PCT = 3.0
# Conservative ongoing annual drag proxy: rebalancing slippage + spot-perp basis/roll +
# capital/borrow cost. A floor, not a model. Round-trip entry/exit fees are amortized to ~0
# over a continuous hold and folded into the one-time fee below.
ANNUAL_COST_DRAG_PCT = 2.0
ONE_TIME_ROUNDTRIP_FEE_PCT = 0.16  # 4 taker fills (~0.04% each) on both legs, once
# Carry stops being clean income if it is too often negative.
MAX_NEG_FRACTION_PCT = 45.0


@dataclass(frozen=True)
class FundingTick:
    time: datetime
    rate: float


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    start: str
    end: str
    min_funding_ticks: int
    min_symbols: int
    min_net_carry_pct: float
    annual_cost_drag_pct: float
    one_time_fee_pct: float
    max_neg_fraction_pct: float


@dataclass(frozen=True)
class SymbolCarry:
    symbol: str
    ticks: int
    span_days: float
    mean_rate_per_8h: float
    annualized_carry_pct: float
    net_annualized_carry_pct: float
    neg_fraction_pct: float
    max_consecutive_negative: int
    cumulative_gross_carry_pct: float
    cumulative_net_carry_pct: float
    h1_hurdle_pass: bool
    h2_harvestable_pass: bool


@dataclass(frozen=True)
class DataAudit:
    total_symbols: int
    usable_symbols: int
    per_symbol_ticks: dict[str, int]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class ProbeReport:
    config: dict[str, object]
    data_audit: DataAudit
    symbol_results: tuple[SymbolCarry, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _parse_dt(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def fetch_funding_history(
    session: aiohttp.ClientSession, symbol: str, start: datetime, end: datetime
) -> list[FundingTick]:
    """Paginate the public Binance futures funding endpoint (no key required)."""
    logger = get_logger("probe.funding_carry")
    out: list[FundingTick] = []
    cursor_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor_ms < end_ms:
        params = {"symbol": symbol, "startTime": cursor_ms, "endTime": end_ms, "limit": 1000}
        async with session.get(FUNDING_URL, params=params) as response:
            response.raise_for_status()
            payload = await response.json()
        if not isinstance(payload, list) or not payload:
            break
        for entry in payload:
            out.append(
                FundingTick(
                    time=datetime.fromtimestamp(int(entry["fundingTime"]) / 1000, tz=UTC),
                    rate=float(entry["fundingRate"]),
                )
            )
        last_ms = int(payload[-1]["fundingTime"])
        if last_ms <= cursor_ms:
            break
        cursor_ms = last_ms + 1
        logger.info("%s: %d funding ticks so far", symbol, len(out))
        await asyncio.sleep(0.1)
    return out


def _max_consecutive_negative(rates: Sequence[float]) -> int:
    best = 0
    run = 0
    for rate in rates:
        if rate < 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def compute_symbol_carry(
    symbol: str, ticks: Sequence[FundingTick], config: ProbeConfig
) -> SymbolCarry:
    rates = [tick.rate for tick in ticks]
    n = len(rates)
    span_days = (ticks[-1].time - ticks[0].time).total_seconds() / 86400.0 if n >= 2 else 0.0
    mean_rate = statistics.mean(rates) if rates else 0.0
    annualized = mean_rate * FUNDINGS_PER_YEAR * 100.0
    net_annualized = annualized - config.annual_cost_drag_pct
    neg_fraction = (sum(1 for r in rates if r < 0) / n * 100.0) if n else 0.0
    cumulative_gross = sum(rates) * 100.0
    cumulative_net = (
        cumulative_gross
        - config.one_time_fee_pct
        - (config.annual_cost_drag_pct * span_days / 365.0)
    )

    h1 = net_annualized > config.min_net_carry_pct
    h2 = neg_fraction <= config.max_neg_fraction_pct and cumulative_net > 0.0

    return SymbolCarry(
        symbol=symbol,
        ticks=n,
        span_days=span_days,
        mean_rate_per_8h=mean_rate,
        annualized_carry_pct=annualized,
        net_annualized_carry_pct=net_annualized,
        neg_fraction_pct=neg_fraction,
        max_consecutive_negative=_max_consecutive_negative(rates),
        cumulative_gross_carry_pct=cumulative_gross,
        cumulative_net_carry_pct=cumulative_net,
        h1_hurdle_pass=h1,
        h2_harvestable_pass=h2,
    )


def audit_data(results: Sequence[SymbolCarry], config: ProbeConfig) -> DataAudit:
    per_symbol = {r.symbol: r.ticks for r in results}
    usable = sum(1 for r in results if r.ticks >= config.min_funding_ticks)
    blocked = usable < config.min_symbols
    reason = (
        f"only {usable} symbols have >= {config.min_funding_ticks} funding ticks "
        f"(need >= {config.min_symbols})"
        if blocked
        else None
    )
    return DataAudit(
        total_symbols=len(results),
        usable_symbols=usable,
        per_symbol_ticks=per_symbol,
        blocked=blocked,
        blocked_reason=reason,
    )


def decide_verdict(
    audit: DataAudit, results: Sequence[SymbolCarry], config: ProbeConfig
) -> tuple[str, str, tuple[str, ...]]:
    if audit.blocked:
        return (BLOCKED_ON_DATA, BLOCKED_ON_DATA, (audit.blocked_reason or "data gate failed",))

    usable = [r for r in results if r.ticks >= config.min_funding_ticks]
    h1_count = sum(1 for r in usable if r.h1_hurdle_pass)
    h2_count = sum(1 for r in usable if r.h2_harvestable_pass)
    reasons: list[str] = []

    h1_ok = h1_count >= config.min_symbols
    h2_ok = h2_count >= config.min_symbols
    if h1_ok and h2_ok:
        return (
            "OK",
            HAS_PULSE,
            (
                f"net annualized carry clears {config.min_net_carry_pct}% on {h1_count} symbols "
                f"and is harvestable (neg-fraction bounded, cumulative net > 0) on {h2_count}",
            ),
        )
    if h1_ok != h2_ok:
        which = "hurdle (H1)" if h1_ok else "harvestability (H2)"
        reasons.append(
            f"only {which} passed on >= {config.min_symbols} symbols "
            f"(H1={h1_count}, H2={h2_count}) — carry exists but is not cleanly bankable"
        )
        return ("OK", WEAK_EDGE, tuple(reasons))
    reasons.append(
        f"neither gate passed on >= {config.min_symbols} symbols (H1={h1_count}, H2={h2_count})"
    )
    return ("OK", NO_PULSE, tuple(reasons))


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.funding_carry")
    start = _parse_dt(config.start)
    end = _parse_dt(config.end)

    results: list[SymbolCarry] = []
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30, connect=10),
        headers={"Accept": "application/json"},
    ) as session:
        for symbol in config.symbols:
            try:
                ticks = await fetch_funding_history(session, symbol, start, end)
            except Exception as exc:  # noqa: BLE001 — tolerate per-symbol fetch failure
                logger.warning("%s: funding fetch failed (%s)", symbol, type(exc).__name__)
                ticks = []
            if not ticks:
                results.append(
                    SymbolCarry(
                        symbol=symbol,
                        ticks=0,
                        span_days=0.0,
                        mean_rate_per_8h=0.0,
                        annualized_carry_pct=0.0,
                        net_annualized_carry_pct=0.0,
                        neg_fraction_pct=0.0,
                        max_consecutive_negative=0,
                        cumulative_gross_carry_pct=0.0,
                        cumulative_net_carry_pct=0.0,
                        h1_hurdle_pass=False,
                        h2_harvestable_pass=False,
                    )
                )
                continue
            carry = compute_symbol_carry(symbol, ticks, config)
            logger.info(
                "%s: %d ticks, ann carry %.2f%% (net %.2f%%), neg %.1f%%, cum net %.2f%%",
                symbol,
                carry.ticks,
                carry.annualized_carry_pct,
                carry.net_annualized_carry_pct,
                carry.neg_fraction_pct,
                carry.cumulative_net_carry_pct,
            )
            results.append(carry)

    audit = audit_data(results, config)
    status, verdict, reasons = decide_verdict(audit, results, config)
    return ProbeReport(
        config={
            "symbols": list(config.symbols),
            "start": config.start,
            "end": config.end,
            "min_net_carry_pct": config.min_net_carry_pct,
            "annual_cost_drag_pct": config.annual_cost_drag_pct,
            "max_neg_fraction_pct": config.max_neg_fraction_pct,
        },
        data_audit=audit,
        symbol_results=tuple(results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = ["# Delta-Neutral Funding Carry Probe — Report", ""]
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_funding_carry_neutral.py`")
    lines.append("**Framing:** market-neutral yield (long spot + short perp), not direction.")
    lines.append("")
    a = report.data_audit
    lines.append("## STEP 0 — Data feasibility")
    lines.append(f"- Symbols: {a.total_symbols}; usable: {a.usable_symbols}")
    lines.append(f"- Ticks per symbol: {a.per_symbol_ticks}")
    lines.append(f"- Blocked: {a.blocked}{f' — {a.blocked_reason}' if a.blocked_reason else ''}")
    lines.append("")
    lines.append("## STEP 1 — Per-symbol carry")
    lines.append("")
    lines.append(
        "| Symbol | Ticks | Ann carry % | Net ann % | Neg % | Max neg run | Cum net % | H1 | H2 |"
    )
    lines.append(
        "|--------|-------|-------------|-----------|-------|-------------|-----------|----|----|"
    )
    for r in report.symbol_results:
        lines.append(
            f"| {r.symbol} | {r.ticks} | {r.annualized_carry_pct:+.2f} | "
            f"{r.net_annualized_carry_pct:+.2f} | {r.neg_fraction_pct:.1f} | "
            f"{r.max_consecutive_negative} | {r.cumulative_net_carry_pct:+.2f} | "
            f"{'Y' if r.h1_hurdle_pass else 'n'} | {'Y' if r.h2_harvestable_pass else 'n'} |"
        )
    lines.append("")
    lines.append("## Reasons")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--min-funding-ticks", type=int, default=MIN_FUNDING_TICKS)
    parser.add_argument("--min-symbols", type=int, default=MIN_SYMBOLS)
    parser.add_argument("--min-net-carry-pct", type=float, default=MIN_NET_CARRY_PCT)
    parser.add_argument("--annual-cost-drag-pct", type=float, default=ANNUAL_COST_DRAG_PCT)
    parser.add_argument("--one-time-fee-pct", type=float, default=ONE_TIME_ROUNDTRIP_FEE_PCT)
    parser.add_argument("--max-neg-fraction-pct", type=float, default=MAX_NEG_FRACTION_PCT)
    parser.add_argument(
        "--output-dir", default=str(Path("research/rbi_loop/funding-carry-neutral-v0"))
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        symbols=tuple(args.symbols),
        start=args.start,
        end=args.end,
        min_funding_ticks=args.min_funding_ticks,
        min_symbols=args.min_symbols,
        min_net_carry_pct=args.min_net_carry_pct,
        annual_cost_drag_pct=args.annual_cost_drag_pct,
        one_time_fee_pct=args.one_time_fee_pct,
        max_neg_fraction_pct=args.max_neg_fraction_pct,
    )
    report = await run_probe(config)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": report.config,
        "data_audit": asdict(report.data_audit),
        "symbol_results": [asdict(r) for r in report.symbol_results],
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
