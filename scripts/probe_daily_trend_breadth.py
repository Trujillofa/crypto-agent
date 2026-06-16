#!/usr/bin/env python3
"""Cheap feasibility probe for daily-trend breadth (portfolio SMA50 long-only).

Read-only: ohlcv only. Tests whether expanding the validated per-symbol SMA50
long-only rule across a liquid USDT universe lowers profit concentration and
raises effective trade count vs the 3-symbol Gate 2 failure — without changing
the rule or adding knobs.

See docs/specs/daily-trend-breadth-probe-v0.md and research-reset-2026-06-06.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_higher_tf_trend_following import (
    DailyBar,
    build_db_config,
    resample_daily,
)
from src.db import close_pool, init_pool
from src.utils.logger import configure_logger, get_logger

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

COVERAGE_QUERY = """
    SELECT
        symbol,
        COUNT(*) AS bars,
        MIN(time) AS first_ts,
        MAX(time) AS last_ts,
        SUM(volume * close_price) AS quote_volume
    FROM ohlcv
    WHERE timeframe = $1
      AND symbol LIKE '%USDT'
    GROUP BY symbol
    ORDER BY quote_volume DESC NULLS LAST
"""

TRADING_DAYS_PER_YEAR = 365
SMA_WINDOW = 50
MIN_UNIVERSE_SYMBOLS = 15
TARGET_UNIVERSE_SYMBOLS = 20
MIN_HISTORY_DAYS = 700
WFO_OOS_MONTHS = 2
MIN_STATE_CHANGES_PER_OOS = 20
MAX_CONCENTRATION_PCT = 50.0
MAJORS_ONLY = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
BLOCKED_STATUS = "BLOCKED_ON_INGESTION"


@dataclass(frozen=True)
class ProbeConfig:
    source_timeframe: str
    start: str
    end: str
    sma_window: int
    one_way_fee_pct: float
    min_universe_symbols: int
    target_universe_symbols: int
    min_history_days: int
    wfo_oos_months: int
    min_state_changes_per_oos: int
    max_concentration_pct: float
    vol_lookback_days: int


@dataclass(frozen=True)
class CoverageRow:
    symbol: str
    bars: int
    first_ts: datetime
    last_ts: datetime
    span_days: int
    quote_volume: float


@dataclass(frozen=True)
class CoverageAudit:
    rows: tuple[CoverageRow, ...]
    eligible: tuple[CoverageRow, ...]
    universe: tuple[str, ...]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class PortfolioMetrics:
    n_days: int
    n_symbols: int
    total_state_changes: int
    state_changes_per_oos_window: float
    max_symbol_pnl_share_pct: float
    mean_pairwise_signal_corr: float
    strat_total_return_pct: float
    bh_total_return_pct: float
    strat_sharpe: float
    bh_sharpe: float
    strat_max_dd_pct: float
    bh_max_dd_pct: float
    vol_target_total_return_pct: float
    vol_target_sharpe: float
    vol_target_max_dd_pct: float
    per_symbol_pnl_pct: tuple[tuple[str, float], ...]
    per_symbol_switches: tuple[tuple[str, int], ...]

    @property
    def concentration_passes(self) -> bool:
        return self.max_symbol_pnl_share_pct < MAX_CONCENTRATION_PCT

    @property
    def trade_count_passes(self) -> bool:
        return self.state_changes_per_oos_window >= MIN_STATE_CHANGES_PER_OOS

    @property
    def risk_adjusted_passes(self) -> bool:
        return self.strat_sharpe >= self.bh_sharpe and self.strat_max_dd_pct < self.bh_max_dd_pct


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    coverage: CoverageAudit
    metrics: PortfolioMetrics | None
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _mean(values: Sequence[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def _simple_moving_average(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        out.append(running / window if index >= window - 1 else None)
    return out


def _sharpe(daily_returns: Sequence[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    stdev = statistics.pstdev(daily_returns)
    if stdev <= 0:
        return 0.0
    return _mean(daily_returns) / stdev * math.sqrt(TRADING_DAYS_PER_YEAR)


def _max_drawdown_pct(daily_returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in daily_returns:
        equity *= 1.0 + ret
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak
            max_dd = max(max_dd, drawdown)
    return max_dd * 100.0


def _total_return_pct(daily_returns: Sequence[float]) -> float:
    equity = 1.0
    for ret in daily_returns:
        equity *= 1.0 + ret
    return (equity - 1.0) * 100.0


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2 or len(xs) != len(ys):
        return 0.0
    mx = _mean(xs)
    my = _mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x <= 0 or den_y <= 0:
        return 0.0
    return num / (den_x * den_y)


def _max_symbol_pnl_share(per_symbol_pnl: Sequence[tuple[str, float]]) -> float:
    positive = [max(pnl, 0.0) for _, pnl in per_symbol_pnl]
    total = sum(positive)
    if total <= 0:
        return 100.0
    return max(positive) / total * 100.0


def _mean_pairwise_signal_correlation(signals: Mapping[str, Sequence[int]]) -> float:
    symbols = sorted(signals)
    if len(symbols) < 2:
        return 0.0
    correlations: list[float] = []
    for left_index, left in enumerate(symbols[:-1]):
        left_sig = [float(v) for v in signals[left]]
        for right in symbols[left_index + 1 :]:
            right_sig = [float(v) for v in signals[right]]
            correlations.append(_pearson(left_sig, right_sig))
    return _mean(correlations) if correlations else 0.0


def build_coverage_audit(
    rows: Sequence[Mapping[str, object]],
    *,
    min_history_days: int,
    min_universe_symbols: int,
    target_universe_symbols: int,
) -> CoverageAudit:
    materialized: list[CoverageRow] = []
    for row in rows:
        first_ts = row["first_ts"]
        last_ts = row["last_ts"]
        if not isinstance(first_ts, datetime) or not isinstance(last_ts, datetime):
            continue
        span_days = (last_ts - first_ts).days
        materialized.append(
            CoverageRow(
                symbol=str(row["symbol"]),
                bars=int(row["bars"]),
                first_ts=first_ts,
                last_ts=last_ts,
                span_days=span_days,
                quote_volume=float(row["quote_volume"] or 0.0),
            )
        )

    eligible = tuple(
        sorted(
            (row for row in materialized if row.span_days >= min_history_days),
            key=lambda row: row.quote_volume,
            reverse=True,
        )
    )
    universe_rows = eligible[:target_universe_symbols]
    universe = tuple(row.symbol for row in universe_rows)

    blocked = False
    blocked_reason: str | None = None
    if len(eligible) < min_universe_symbols:
        blocked = True
        blocked_reason = (
            f"only {len(eligible)} USDT pairs have >={min_history_days}d of 1h history; "
            f"need >={min_universe_symbols}"
        )
    elif universe and set(universe).issubset(MAJORS_ONLY):
        blocked = True
        blocked_reason = (
            "universe collapses to BTC/ETH/SOL only — insufficient breadth in prod ohlcv"
        )

    return CoverageAudit(
        rows=tuple(materialized),
        eligible=eligible,
        universe=universe,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )


def _symbol_positions_and_returns(
    bars: Sequence[DailyBar],
    sma_window: int,
    fee_pct: float,
) -> tuple[list[datetime], list[int], list[float], list[float], int, float]:
    """Return days, positions, net returns, gross returns, switches, total return %."""
    closes = [bar.close_price for bar in bars]
    days = [bar.day for bar in bars]
    sma = _simple_moving_average(closes, sma_window)
    first = sma_window
    if first >= len(closes) - 1:
        return [], [], [], [], 0, 0.0

    fee = fee_pct / 100.0
    out_days: list[datetime] = []
    positions: list[int] = []
    net_returns: list[float] = []
    gross_returns: list[float] = []
    prev_position = 0
    switches = 0

    for t in range(first, len(closes)):
        prior_sma = sma[t - 1]
        if prior_sma is None:
            continue
        position = 1 if closes[t - 1] > prior_sma else 0
        daily_ret = closes[t] / closes[t - 1] - 1.0
        cost = fee if position != prev_position else 0.0
        out_days.append(days[t])
        positions.append(position)
        gross_returns.append(daily_ret)
        net_returns.append(position * daily_ret - cost)
        if position != prev_position:
            switches += 1
        prev_position = position

    return out_days, positions, net_returns, gross_returns, switches, _total_return_pct(net_returns)


def _weights_equal(long_symbols: Sequence[str]) -> dict[str, float]:
    if not long_symbols:
        return {}
    weight = 1.0 / len(long_symbols)
    return dict.fromkeys(long_symbols, weight)


def _weights_vol_target(
    long_symbols: Sequence[str],
    trailing_vols: Mapping[str, float],
) -> dict[str, float]:
    if not long_symbols:
        return {}
    inv_vols = {symbol: 1.0 / max(trailing_vols.get(symbol, 0.0), 1e-8) for symbol in long_symbols}
    total = sum(inv_vols.values())
    if total <= 0:
        return _weights_equal(long_symbols)
    return {symbol: inv_vols[symbol] / total for symbol in long_symbols}


def _portfolio_day_return(
    long_symbols: Sequence[str],
    gross_returns: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    if not long_symbols:
        return 0.0
    return sum(weights[symbol] * gross_returns[symbol] for symbol in long_symbols)


def _fee_cost(
    symbols: Sequence[str],
    prev_positions: Mapping[str, int],
    positions: Mapping[str, int],
    prev_weights: Mapping[str, float],
    new_weights: Mapping[str, float],
    fee: float,
) -> float:
    cost = 0.0
    for symbol in symbols:
        if positions[symbol] == prev_positions[symbol]:
            continue
        old_weight = prev_weights.get(symbol, 0.0)
        new_weight = new_weights.get(symbol, 0.0)
        cost += fee * abs(new_weight - old_weight)
    return cost


def evaluate_portfolio_breadth(
    symbol_bars: Mapping[str, Sequence[DailyBar]],
    *,
    sma_window: int,
    fee_pct: float,
    vol_lookback_days: int,
    wfo_oos_months: int,
) -> PortfolioMetrics | None:
    """Simulate equal-weight and vol-target breadth portfolios on aligned days."""
    per_symbol: dict[
        str, tuple[list[datetime], list[int], list[float], list[float], int, float]
    ] = {}
    for symbol, bars in symbol_bars.items():
        days, positions, net_returns, gross_returns, switches, total_ret = (
            _symbol_positions_and_returns(bars, sma_window, fee_pct)
        )
        if not days:
            continue
        per_symbol[symbol] = (
            days,
            positions,
            net_returns,
            gross_returns,
            switches,
            total_ret,
        )

    if len(per_symbol) < 2:
        return None

    common_days = sorted(set.intersection(*[set(days) for days, *_ in per_symbol.values()]))
    if len(common_days) < sma_window + 5:
        return None

    symbols = sorted(per_symbol)
    fee = fee_pct / 100.0

    aligned_positions: dict[str, list[int]] = {}
    aligned_gross: dict[str, list[float]] = {}
    per_symbol_pnl: list[tuple[str, float]] = []
    per_symbol_switches: list[tuple[str, int]] = []
    total_state_changes = 0

    for symbol in symbols:
        days, positions, _net, gross, switches, total_ret = per_symbol[symbol]
        lookup_pos = dict(zip(days, positions, strict=True))
        lookup_gross = dict(zip(days, gross, strict=True))
        aligned_positions[symbol] = [lookup_pos[day] for day in common_days]
        aligned_gross[symbol] = [lookup_gross[day] for day in common_days]
        per_symbol_pnl.append((symbol, total_ret))
        per_symbol_switches.append((symbol, switches))
        total_state_changes += switches

    strat_returns: list[float] = []
    bh_returns: list[float] = []
    vol_returns: list[float] = []
    gross_history: dict[str, list[float]] = {symbol: [] for symbol in symbols}
    prev_positions = dict.fromkeys(symbols, 0)
    prev_equal_weights = dict.fromkeys(symbols, 0.0)
    prev_vol_weights = dict.fromkeys(symbols, 0.0)

    for day_index, _day in enumerate(common_days):
        positions_today = {symbol: aligned_positions[symbol][day_index] for symbol in symbols}
        gross_today = {symbol: aligned_gross[symbol][day_index] for symbol in symbols}
        long_symbols = [symbol for symbol in symbols if positions_today[symbol] == 1]

        equal_weights = _weights_equal(long_symbols)
        trailing_vols = {
            symbol: statistics.pstdev(gross_history[symbol][-vol_lookback_days:])
            if len(gross_history[symbol]) >= 2
            else 0.0
            for symbol in symbols
        }
        vol_weights = _weights_vol_target(long_symbols, trailing_vols)

        equal_gross = _portfolio_day_return(long_symbols, gross_today, equal_weights)
        equal_fee = _fee_cost(
            symbols,
            prev_positions,
            positions_today,
            prev_equal_weights,
            equal_weights,
            fee,
        )
        strat_returns.append(equal_gross - equal_fee)

        vol_gross = _portfolio_day_return(long_symbols, gross_today, vol_weights)
        vol_fee = _fee_cost(
            symbols,
            prev_positions,
            positions_today,
            prev_vol_weights,
            vol_weights,
            fee,
        )
        vol_returns.append(vol_gross - vol_fee)

        bh_returns.append(_mean(gross_today.values()))

        for symbol in symbols:
            gross_history[symbol].append(gross_today[symbol])
        prev_positions = positions_today
        prev_equal_weights = {symbol: equal_weights.get(symbol, 0.0) for symbol in symbols}
        prev_vol_weights = {symbol: vol_weights.get(symbol, 0.0) for symbol in symbols}

    oos_window_days = max(wfo_oos_months * 30, 1)
    state_changes_per_oos = total_state_changes / max(len(common_days) / oos_window_days, 1.0)

    return PortfolioMetrics(
        n_days=len(common_days),
        n_symbols=len(symbols),
        total_state_changes=total_state_changes,
        state_changes_per_oos_window=state_changes_per_oos,
        max_symbol_pnl_share_pct=_max_symbol_pnl_share(per_symbol_pnl),
        mean_pairwise_signal_corr=_mean_pairwise_signal_correlation(aligned_positions),
        strat_total_return_pct=_total_return_pct(strat_returns),
        bh_total_return_pct=_total_return_pct(bh_returns),
        strat_sharpe=_sharpe(strat_returns),
        bh_sharpe=_sharpe(bh_returns),
        strat_max_dd_pct=_max_drawdown_pct(strat_returns),
        bh_max_dd_pct=_max_drawdown_pct(bh_returns),
        vol_target_total_return_pct=_total_return_pct(vol_returns),
        vol_target_sharpe=_sharpe(vol_returns),
        vol_target_max_dd_pct=_max_drawdown_pct(vol_returns),
        per_symbol_pnl_pct=tuple(per_symbol_pnl),
        per_symbol_switches=tuple(per_symbol_switches),
    )


def decide_verdict(
    metrics: PortfolioMetrics | None,
    *,
    blocked: bool,
) -> tuple[str, str, tuple[str, ...]]:
    if blocked:
        return (
            BLOCKED_STATUS,
            "NO_PULSE",
            ("blocked on ingestion — thin prod ohlcv universe; pulse metrics not run",),
        )
    if metrics is None:
        return ("ERROR", "NO_PULSE", ("portfolio metrics unavailable",))

    reasons: list[str] = []
    if not metrics.concentration_passes:
        reasons.append(
            f"concentration {metrics.max_symbol_pnl_share_pct:.1f}% >= {MAX_CONCENTRATION_PCT:.0f}%"
        )
    if not metrics.trade_count_passes:
        reasons.append(
            f"state changes per OOS window {metrics.state_changes_per_oos_window:.1f} "
            f"< {MIN_STATE_CHANGES_PER_OOS}"
        )
    if not metrics.risk_adjusted_passes:
        reasons.append(
            f"risk-adjusted edge missing (Sharpe {metrics.strat_sharpe:.2f} vs "
            f"{metrics.bh_sharpe:.2f}, DD {metrics.strat_max_dd_pct:.1f}% vs "
            f"{metrics.bh_max_dd_pct:.1f}%)"
        )

    if metrics.concentration_passes and metrics.trade_count_passes and metrics.risk_adjusted_passes:
        return ("OK", "HAS_PULSE", tuple(reasons))

    if metrics.risk_adjusted_passes:
        return ("OK", "WEAK_EDGE", tuple(reasons))

    return ("OK", "NO_PULSE", tuple(reasons))


def default_config() -> ProbeConfig:
    return ProbeConfig(
        source_timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        sma_window=SMA_WINDOW,
        one_way_fee_pct=0.04,
        min_universe_symbols=MIN_UNIVERSE_SYMBOLS,
        target_universe_symbols=TARGET_UNIVERSE_SYMBOLS,
        min_history_days=MIN_HISTORY_DAYS,
        wfo_oos_months=WFO_OOS_MONTHS,
        min_state_changes_per_oos=MIN_STATE_CHANGES_PER_OOS,
        max_concentration_pct=MAX_CONCENTRATION_PCT,
        vol_lookback_days=20,
    )


async def run_coverage_audit(config: ProbeConfig) -> CoverageAudit:
    pool = await init_pool(build_db_config())
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(COVERAGE_QUERY, config.source_timeframe)
        return build_coverage_audit(
            rows,
            min_history_days=config.min_history_days,
            min_universe_symbols=config.min_universe_symbols,
            target_universe_symbols=config.target_universe_symbols,
        )
    finally:
        await close_pool()


async def load_symbol_bars(
    symbols: Sequence[str],
    config: ProbeConfig,
) -> dict[str, list[DailyBar]]:
    pool = await init_pool(build_db_config())
    bars_by_symbol: dict[str, list[DailyBar]] = {}
    try:
        for symbol in symbols:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    PROBE_QUERY,
                    symbol,
                    config.source_timeframe,
                    datetime.fromisoformat(config.start),
                    datetime.fromisoformat(config.end),
                )
            bars_by_symbol[symbol] = resample_daily(rows)
    finally:
        await close_pool()
    return bars_by_symbol


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.daily_trend_breadth")

    coverage = await run_coverage_audit(config)
    logger.info(
        "coverage audit: %d USDT pairs, %d eligible (>=%dd), universe=%s, blocked=%s",
        len(coverage.rows),
        len(coverage.eligible),
        config.min_history_days,
        ",".join(coverage.universe) or "(none)",
        coverage.blocked,
    )
    if coverage.blocked:
        logger.warning("blocked on ingestion: %s", coverage.blocked_reason)
        status, verdict, reasons = decide_verdict(None, blocked=True)
        return ProbeReport(
            config=config,
            coverage=coverage,
            metrics=None,
            status=status,
            verdict=verdict,
            reasons=reasons,
        )

    bars_by_symbol = await load_symbol_bars(coverage.universe, config)
    metrics = evaluate_portfolio_breadth(
        bars_by_symbol,
        sma_window=config.sma_window,
        fee_pct=config.one_way_fee_pct,
        vol_lookback_days=config.vol_lookback_days,
        wfo_oos_months=config.wfo_oos_months,
    )
    if metrics is None:
        logger.error("portfolio metrics could not be computed")
    else:
        logger.info(
            "portfolio: symbols=%d conc=%.1f%% trades/oos=%.1f sharpe=%.2f/%.2f dd=%.1f/%.1f corr=%.2f",
            metrics.n_symbols,
            metrics.max_symbol_pnl_share_pct,
            metrics.state_changes_per_oos_window,
            metrics.strat_sharpe,
            metrics.bh_sharpe,
            metrics.strat_max_dd_pct,
            metrics.bh_max_dd_pct,
            metrics.mean_pairwise_signal_corr,
        )

    status, verdict, reasons = decide_verdict(metrics, blocked=False)
    return ProbeReport(
        config=config,
        coverage=coverage,
        metrics=metrics,
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append("# Daily Trend-Following Breadth Probe — Report")
    lines.append("")
    lines.append(f"**Verdict:** **{report.verdict}**")
    if report.status == BLOCKED_STATUS:
        lines.append(f"**Status:** **{report.status}**")
    lines.append("**Script:** `scripts/probe_daily_trend_breadth.py`")
    lines.append(
        "**Spec:** [daily-trend-breadth-probe-v0.md](../specs/daily-trend-breadth-probe-v0.md)"
    )
    lines.append("")
    lines.append("## Coverage audit")
    lines.append(
        f"- USDT pairs with 1h rows: **{len(report.coverage.rows)}** "
        f"(eligible >={report.config.min_history_days}d: **{len(report.coverage.eligible)}**)"
    )
    lines.append(
        f"- Target universe (top {report.config.target_universe_symbols} by liquidity): "
        + (", ".join(report.coverage.universe) if report.coverage.universe else "(none)")
    )
    if report.coverage.blocked:
        lines.append(f"- **Blocked:** {report.coverage.blocked_reason}")
    lines.append("")
    if report.coverage.eligible:
        lines.append("| Symbol | Bars | Span (d) | First | Quote vol |")
        lines.append("|--------|------|----------|-------|-----------|")
        for row in report.coverage.eligible:
            lines.append(
                f"| {row.symbol} | {row.bars} | {row.span_days} | "
                f"{row.first_ts.date()} | {row.quote_volume:.2e} |"
            )
        lines.append("")

    if report.metrics is None:
        lines.append("## Pulse metrics")
        lines.append("")
        lines.append("Not run — coverage gate failed.")
        if report.reasons:
            lines.append("")
            for reason in report.reasons:
                lines.append(f"- {reason}")
        return "\n".join(lines)

    metrics = report.metrics
    lines.append("## Config")
    lines.append(f"- SMA window: {report.config.sma_window} (frozen)")
    lines.append(f"- Window: {report.config.start} → {report.config.end}")
    lines.append(f"- One-way fee: {report.config.one_way_fee_pct}%")
    lines.append(f"- Universe size: {metrics.n_symbols}")
    lines.append("")
    lines.append("## Pulse metrics (equal-weight portfolio)")
    lines.append("")
    lines.append("| Metric | Value | Gate | Pass |")
    lines.append("|--------|-------|------|------|")
    lines.append(
        f"| Max single-symbol PnL share | {metrics.max_symbol_pnl_share_pct:.1f}% | "
        f"< {report.config.max_concentration_pct:.0f}% | {metrics.concentration_passes} |"
    )
    lines.append(
        f"| State changes / OOS window ({report.config.wfo_oos_months}mo) | "
        f"{metrics.state_changes_per_oos_window:.1f} | "
        f">= {report.config.min_state_changes_per_oos} | {metrics.trade_count_passes} |"
    )
    lines.append(
        f"| Portfolio Sharpe | {metrics.strat_sharpe:.2f} | >= BH {metrics.bh_sharpe:.2f} | "
        f"{metrics.strat_sharpe >= metrics.bh_sharpe} |"
    )
    lines.append(
        f"| Portfolio max DD | {metrics.strat_max_dd_pct:.1f}% | < BH "
        f"{metrics.bh_max_dd_pct:.1f}% | {metrics.strat_max_dd_pct < metrics.bh_max_dd_pct} |"
    )
    lines.append(
        f"| Mean pairwise signal correlation | {metrics.mean_pairwise_signal_corr:.3f} | "
        "diagnostic | n/a |"
    )
    lines.append("")
    lines.append("## Vol-target variant (secondary, not a gate)")
    lines.append(
        f"- Return {metrics.vol_target_total_return_pct:.1f}% | Sharpe "
        f"{metrics.vol_target_sharpe:.2f} | Max DD {metrics.vol_target_max_dd_pct:.1f}%"
    )
    lines.append("")
    lines.append("## Per-symbol standalone SMA50 (concentration inputs)")
    lines.append("")
    lines.append("| Symbol | Return % | Switches |")
    lines.append("|--------|----------|----------|")
    switch_lookup = dict(metrics.per_symbol_switches)
    for symbol, pnl in metrics.per_symbol_pnl_pct:
        lines.append(f"| {symbol} | {pnl:.1f} | {switch_lookup.get(symbol, 0)} |")
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
        "coverage": {
            "blocked": report.coverage.blocked,
            "blocked_reason": report.coverage.blocked_reason,
            "eligible_count": len(report.coverage.eligible),
            "universe": list(report.coverage.universe),
            "eligible": [
                {
                    "symbol": row.symbol,
                    "bars": row.bars,
                    "span_days": row.span_days,
                    "first_ts": row.first_ts.isoformat(),
                    "quote_volume": row.quote_volume,
                }
                for row in report.coverage.eligible
            ],
        },
    }
    if report.metrics is not None:
        metrics = report.metrics
        payload["metrics"] = {
            "max_symbol_pnl_share_pct": round(metrics.max_symbol_pnl_share_pct, 2),
            "total_state_changes": metrics.total_state_changes,
            "state_changes_per_oos_window": round(metrics.state_changes_per_oos_window, 2),
            "mean_pairwise_signal_corr": round(metrics.mean_pairwise_signal_corr, 3),
            "strat_total_return_pct": round(metrics.strat_total_return_pct, 2),
            "bh_total_return_pct": round(metrics.bh_total_return_pct, 2),
            "strat_sharpe": round(metrics.strat_sharpe, 3),
            "bh_sharpe": round(metrics.bh_sharpe, 3),
            "strat_max_dd_pct": round(metrics.strat_max_dd_pct, 2),
            "bh_max_dd_pct": round(metrics.bh_max_dd_pct, 2),
            "vol_target_total_return_pct": round(metrics.vol_target_total_return_pct, 2),
            "vol_target_sharpe": round(metrics.vol_target_sharpe, 3),
            "vol_target_max_dd_pct": round(metrics.vol_target_max_dd_pct, 2),
            "concentration_passes": metrics.concentration_passes,
            "trade_count_passes": metrics.trade_count_passes,
            "risk_adjusted_passes": metrics.risk_adjusted_passes,
            "per_symbol_pnl_pct": [
                {"symbol": symbol, "return_pct": round(pnl, 2)}
                for symbol, pnl in metrics.per_symbol_pnl_pct
            ],
        }
    return payload


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = await run_probe(default_config())

    if args.json:
        print(json.dumps(report_to_json(report), indent=2, default=str))
    else:
        print(render_report(report))

    if report.status == BLOCKED_STATUS:
        return 2
    return 0 if report.verdict == "HAS_PULSE" else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
