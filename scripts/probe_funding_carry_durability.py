#!/usr/bin/env python3
"""Carry durability probe v1 — refine the v0 HAS_PULSE into a bank-or-build decision (read-only).

v0 (`probe_funding_carry_neutral.py`) showed funding carry exists on the raw notional stream.
That is NOT the decision number. This v1 answers the four questions that actually decide whether
to spend engineering on a paired spot+perp execution build:

  1. Yield on CAPITAL, not notional — cash-and-carry ties up spot (N) plus perp margin buffer,
     so capital ≈ N × (1 + buffer). Carry on notional overstates the return on deployed capital.
  2. Excess over RISK-FREE — the capital could earn ~T-bill/stablecoin yield with zero engineering
     and zero tail risk. The carry only justifies a build by its *excess* over that, not vs zero.
  3. Forward / OOS split — carry is the most-arbitraged crypto trade; the 2024-26 sample is fat
     with bull funding. Does a forward sub-period still clear the hurdle, or has it compressed?
  4. Worst-window drawdown + margin stress — the deepest negative-funding bleed, and the worst
     adverse perp up-move (the short leg's unrealized loss), which sets the margin buffer the
     capital base must carry — feeding back into (1).

Verdict: PROCEED_TO_BUILD / MARGINAL / BANK / BLOCKED_ON_DATA. This probe does NOT authorize a
build by itself; PROCEED_TO_BUILD means "the execution-feasibility audit is now justified."

Read-only. Public Binance futures funding + spot klines. No key, no DB writes, no execution.
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

from scripts.download_historical import BinanceHistoricalClient, download_klines
from scripts.probe_funding_carry_neutral import (
    FUNDINGS_PER_YEAR,
    FundingTick,
    _parse_dt,
    fetch_funding_history,
)
from src.utils.logger import configure_logger, get_logger

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
PROCEED_TO_BUILD = "PROCEED_TO_BUILD"
MARGINAL = "MARGINAL"
BANK = "BANK"

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_START = "2024-01-01T00:00:00"
DEFAULT_SPLIT = "2025-06-01T00:00:00"  # ~17mo train / ~12mo forward
DEFAULT_END = "2026-06-01T00:00:00"

MIN_FUNDING_TICKS = 800
MIN_SYMBOLS = 2
RISK_FREE_PCT = 4.5  # T-bill / stablecoin opportunity cost of the deployed capital
ANNUAL_COST_DRAG_PCT = 2.0  # rebalancing/basis/slippage proxy (same as v0)
ONE_TIME_FEE_PCT = 0.16
BASE_MARGIN_BUFFER_FRAC = 0.25  # minimum perp margin buffer as a fraction of notional
MARGIN_STRESS_HOURS = 72  # window for the worst adverse perp up-move
# Gates
MIN_EXCESS_PCT = 1.0  # full-period excess over risk-free must clear this (on capital)
MAX_CARRY_DRAWDOWN_PCT = 6.0  # deepest drawdown of the cumulative net-carry curve


@dataclass(frozen=True)
class PeriodCarry:
    label: str
    ticks: int
    span_days: float
    gross_annualized_pct: float
    net_annualized_pct: float
    neg_fraction_pct: float
    max_consecutive_negative: int
    max_carry_drawdown_pct: float


@dataclass(frozen=True)
class SymbolDurability:
    symbol: str
    full: PeriodCarry
    train: PeriodCarry
    forward: PeriodCarry
    worst_adverse_up_pct: float  # worst MARGIN_STRESS_HOURS perp up-move
    implied_buffer_frac: float  # max(base buffer, worst adverse move)
    capital_factor: float  # 1 + implied buffer
    net_yield_on_capital_pct: float  # full-period net annualized / capital factor
    excess_over_risk_free_pct: float
    forward_excess_over_risk_free_pct: float
    g1_excess_pass: bool
    g2_forward_durable_pass: bool
    g3_drawdown_pass: bool


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
    symbol_results: tuple[SymbolDurability, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _max_consecutive_negative(rates: Sequence[float]) -> int:
    best = run = 0
    for rate in rates:
        run = run + 1 if rate < 0 else 0
        best = max(best, run)
    return best


def _max_drawdown_pct(rates: Sequence[float], *, per_tick_drag: float) -> float:
    """Deepest drawdown (in %) of the cumulative net-carry equity curve."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for rate in rates:
        equity += (rate * 100.0) - per_tick_drag
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _period_carry(label: str, ticks: Sequence[FundingTick], *, drag: float) -> PeriodCarry:
    rates = [t.rate for t in ticks]
    n = len(rates)
    span_days = (ticks[-1].time - ticks[0].time).total_seconds() / 86400.0 if n >= 2 else 0.0
    mean_rate = statistics.mean(rates) if rates else 0.0
    gross_ann = mean_rate * FUNDINGS_PER_YEAR * 100.0
    per_tick_drag = drag / FUNDINGS_PER_YEAR
    return PeriodCarry(
        label=label,
        ticks=n,
        span_days=span_days,
        gross_annualized_pct=gross_ann,
        net_annualized_pct=gross_ann - drag,
        neg_fraction_pct=(sum(1 for r in rates if r < 0) / n * 100.0) if n else 0.0,
        max_consecutive_negative=_max_consecutive_negative(rates),
        max_carry_drawdown_pct=_max_drawdown_pct(rates, per_tick_drag=per_tick_drag),
    )


def _worst_adverse_up_pct(closes: Sequence[float], horizon_bars: int) -> float:
    """Largest forward up-move over any horizon window (the short leg's max unrealized loss)."""
    worst = 0.0
    for i in range(len(closes) - horizon_bars):
        entry = closes[i]
        if entry <= 0:
            continue
        move = (closes[i + horizon_bars] / entry - 1.0) * 100.0
        worst = max(worst, move)
    return worst


def build_symbol_durability(
    symbol: str,
    ticks: Sequence[FundingTick],
    closes: Sequence[float],
    *,
    split: datetime,
    config: ProbeConfig,
) -> SymbolDurability:
    full = _period_carry("full", ticks, drag=config.annual_cost_drag_pct)
    train_ticks = [t for t in ticks if t.time < split]
    fwd_ticks = [t for t in ticks if t.time >= split]
    train = _period_carry("train", train_ticks, drag=config.annual_cost_drag_pct)
    forward = _period_carry("forward", fwd_ticks, drag=config.annual_cost_drag_pct)

    worst_up = _worst_adverse_up_pct(closes, config.margin_stress_hours) if closes else 0.0
    implied_buffer = max(config.base_margin_buffer_frac, worst_up / 100.0)
    capital_factor = 1.0 + implied_buffer
    net_yield_on_capital = full.net_annualized_pct / capital_factor
    excess = net_yield_on_capital - config.risk_free_pct
    fwd_yield_on_capital = forward.net_annualized_pct / capital_factor
    fwd_excess = fwd_yield_on_capital - config.risk_free_pct

    return SymbolDurability(
        symbol=symbol,
        full=full,
        train=train,
        forward=forward,
        worst_adverse_up_pct=worst_up,
        implied_buffer_frac=implied_buffer,
        capital_factor=capital_factor,
        net_yield_on_capital_pct=net_yield_on_capital,
        excess_over_risk_free_pct=excess,
        forward_excess_over_risk_free_pct=fwd_excess,
        g1_excess_pass=excess > config.min_excess_pct,
        g2_forward_durable_pass=fwd_excess > 0.0,
        g3_drawdown_pass=full.max_carry_drawdown_pct <= config.max_carry_drawdown_pct,
    )


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    start: str
    split: str
    end: str
    min_funding_ticks: int
    min_symbols: int
    risk_free_pct: float
    annual_cost_drag_pct: float
    one_time_fee_pct: float
    base_margin_buffer_frac: float
    margin_stress_hours: int
    min_excess_pct: float
    max_carry_drawdown_pct: float


def audit_data(results: Sequence[SymbolDurability], config: ProbeConfig) -> DataAudit:
    per_symbol = {r.symbol: r.full.ticks for r in results}
    usable = sum(1 for r in results if r.full.ticks >= config.min_funding_ticks)
    blocked = usable < config.min_symbols
    return DataAudit(
        total_symbols=len(results),
        usable_symbols=usable,
        per_symbol_ticks=per_symbol,
        blocked=blocked,
        blocked_reason=(
            f"only {usable} symbols have >= {config.min_funding_ticks} ticks" if blocked else None
        ),
    )


def decide_verdict(
    audit: DataAudit, results: Sequence[SymbolDurability], config: ProbeConfig
) -> tuple[str, str, tuple[str, ...]]:
    if audit.blocked:
        return (BLOCKED_ON_DATA, BLOCKED_ON_DATA, (audit.blocked_reason or "data gate failed",))

    usable = [r for r in results if r.full.ticks >= config.min_funding_ticks]
    g1 = sum(1 for r in usable if r.g1_excess_pass)
    g2 = sum(1 for r in usable if r.g2_forward_durable_pass)
    g3 = sum(1 for r in usable if r.g3_drawdown_pass)
    reasons: list[str] = [
        f"excess>risk-free+{config.min_excess_pct}% on {g1}/{len(usable)}; "
        f"forward-excess>0 on {g2}/{len(usable)}; drawdown ok on {g3}/{len(usable)}",
    ]
    need = config.min_symbols
    if g1 >= need and g2 >= need and g3 >= need:
        return ("OK", PROCEED_TO_BUILD, tuple(reasons))
    if g1 >= need and (g2 >= need or g3 >= need):
        reasons.append("excess real but forward-compression or worst-window flags one gate")
        return ("OK", MARGINAL, tuple(reasons))
    reasons.append("excess over risk-free does not survive capital base / forward split")
    return ("OK", BANK, tuple(reasons))


async def _load_symbol(
    session: aiohttp.ClientSession,
    client: BinanceHistoricalClient,
    symbol: str,
    config: ProbeConfig,
) -> SymbolDurability:
    start, end = _parse_dt(config.start), _parse_dt(config.end)
    ticks = await fetch_funding_history(session, symbol, start, end)
    price_rows = await download_klines(client, symbol, "1h", config.start, config.end)
    closes = [float(r["close_price"]) for r in price_rows if float(r["close_price"]) > 0]
    if not ticks:
        empty = PeriodCarry("full", 0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)
        return SymbolDurability(
            symbol,
            empty,
            empty,
            empty,
            0.0,
            config.base_margin_buffer_frac,
            1 + config.base_margin_buffer_frac,
            0.0,
            -config.risk_free_pct,
            -config.risk_free_pct,
            False,
            False,
            False,
        )
    return build_symbol_durability(
        symbol, ticks, closes, split=_parse_dt(config.split), config=config
    )


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.funding_carry_durability")
    results: list[SymbolDurability] = []
    async with (
        aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            headers={"Accept": "application/json"},
        ) as session,
        BinanceHistoricalClient() as client,
    ):
        for symbol in config.symbols:
            res = await _load_symbol(session, client, symbol, config)
            logger.info(
                "%s: net/capital %.2f%% excess %.2f%% (fwd %.2f%%) worst+%.1f%%/%dh dd %.2f%%",
                symbol,
                res.net_yield_on_capital_pct,
                res.excess_over_risk_free_pct,
                res.forward_excess_over_risk_free_pct,
                res.worst_adverse_up_pct,
                config.margin_stress_hours,
                res.full.max_carry_drawdown_pct,
            )
            results.append(res)

    audit = audit_data(results, config)
    status, verdict, reasons = decide_verdict(audit, results, config)
    return ProbeReport(
        config={
            "symbols": list(config.symbols),
            "start": config.start,
            "split": config.split,
            "end": config.end,
            "risk_free_pct": config.risk_free_pct,
            "annual_cost_drag_pct": config.annual_cost_drag_pct,
            "base_margin_buffer_frac": config.base_margin_buffer_frac,
            "min_excess_pct": config.min_excess_pct,
            "max_carry_drawdown_pct": config.max_carry_drawdown_pct,
        },
        data_audit=audit,
        symbol_results=tuple(results),
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines = ["# Funding Carry Durability Probe v1 — Report", ""]
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_funding_carry_durability.py`")
    lines.append("**Question:** is the carry's *excess over risk-free* real, forward-durable, and")
    lines.append("survivable — i.e. does it justify a paired spot+perp execution build?")
    lines.append("")
    a = report.data_audit
    lines.append(
        f"- Symbols usable: {a.usable_symbols}/{a.total_symbols}; ticks {a.per_symbol_ticks}"
    )
    lines.append(
        f"- Risk-free benchmark: {report.config['risk_free_pct']}%; "
        f"cost drag {report.config['annual_cost_drag_pct']}%/yr; "
        f"min excess gate {report.config['min_excess_pct']}%"
    )
    lines.append("")
    lines.append(
        "| Symbol | Net/notional % | Cap factor | Net/capital % | Excess vs RF % | Fwd excess % | Worst +move | Carry DD % | G1 | G2 | G3 |"
    )
    lines.append(
        "|--------|----------------|------------|---------------|----------------|--------------|-------------|------------|----|----|----|"
    )
    for r in report.symbol_results:
        lines.append(
            f"| {r.symbol} | {r.full.net_annualized_pct:+.2f} | {r.capital_factor:.2f} | "
            f"{r.net_yield_on_capital_pct:+.2f} | {r.excess_over_risk_free_pct:+.2f} | "
            f"{r.forward_excess_over_risk_free_pct:+.2f} | {r.worst_adverse_up_pct:.1f}% | "
            f"{r.full.max_carry_drawdown_pct:.2f} | {'Y' if r.g1_excess_pass else 'n'} | "
            f"{'Y' if r.g2_forward_durable_pass else 'n'} | {'Y' if r.g3_drawdown_pass else 'n'} |"
        )
    lines.append("")
    lines.append("Periods (net annualized carry on notional):")
    for r in report.symbol_results:
        lines.append(
            f"- {r.symbol}: train {r.train.net_annualized_pct:+.2f}% "
            f"→ forward {r.forward.net_annualized_pct:+.2f}% "
            f"(neg {r.full.neg_fraction_pct:.1f}%, max neg run {r.full.max_consecutive_negative})"
        )
    lines.append("")
    lines.append("## Reasons")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--split", default=DEFAULT_SPLIT)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--min-funding-ticks", type=int, default=MIN_FUNDING_TICKS)
    p.add_argument("--min-symbols", type=int, default=MIN_SYMBOLS)
    p.add_argument("--risk-free-pct", type=float, default=RISK_FREE_PCT)
    p.add_argument("--annual-cost-drag-pct", type=float, default=ANNUAL_COST_DRAG_PCT)
    p.add_argument("--one-time-fee-pct", type=float, default=ONE_TIME_FEE_PCT)
    p.add_argument("--base-margin-buffer-frac", type=float, default=BASE_MARGIN_BUFFER_FRAC)
    p.add_argument("--margin-stress-hours", type=int, default=MARGIN_STRESS_HOURS)
    p.add_argument("--min-excess-pct", type=float, default=MIN_EXCESS_PCT)
    p.add_argument("--max-carry-drawdown-pct", type=float, default=MAX_CARRY_DRAWDOWN_PCT)
    p.add_argument("--output-dir", default=str(Path("research/rbi_loop/funding-carry-neutral-v0")))
    return p.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        symbols=tuple(args.symbols),
        start=args.start,
        split=args.split,
        end=args.end,
        min_funding_ticks=args.min_funding_ticks,
        min_symbols=args.min_symbols,
        risk_free_pct=args.risk_free_pct,
        annual_cost_drag_pct=args.annual_cost_drag_pct,
        one_time_fee_pct=args.one_time_fee_pct,
        base_margin_buffer_frac=args.base_margin_buffer_frac,
        margin_stress_hours=args.margin_stress_hours,
        min_excess_pct=args.min_excess_pct,
        max_carry_drawdown_pct=args.max_carry_drawdown_pct,
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
    (out_dir / "durability_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report_md = render_report(report)
    (out_dir / "durability_report.md").write_text(report_md + "\n", encoding="utf-8")

    print(report_md)
    print(f"\nVERDICT: {report.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
