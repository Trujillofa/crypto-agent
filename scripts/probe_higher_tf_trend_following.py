#!/usr/bin/env python3
"""Cheap feasibility probe for higher-timeframe, long-only trend following.

Read-only: ohlcv only. Tests whether a daily-trend long-only filter
("hold long while close > SMA(window), flat otherwise") beats buy-and-hold on a
*risk-adjusted* basis (Sharpe up, max drawdown down) across BTC/ETH/SOL.

This is the *opposite* surface from the closed mean-reversion / fade lanes. The only
behaviour that ever produced real PnL here was long exposure in an uptrend; this probe
asks whether a simple higher-TF trend filter captures that systematically.

See docs/specs/higher-tf-trend-following-probe-v0.md and research-reset-2026-06-06.md.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import statistics
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

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

TRADING_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    source_timeframe: str
    start: str
    end: str
    ma_windows: tuple[int, ...]
    one_way_fee_pct: float
    min_symbol_majority: float
    min_total_return_pct: float


@dataclass(frozen=True)
class DailyBar:
    day: datetime
    close_price: float


@dataclass(frozen=True)
class StrategyStats:
    window: int
    n_days: int
    n_switches: int
    time_in_market_pct: float
    strat_total_return_pct: float
    bh_total_return_pct: float
    strat_sharpe: float
    bh_sharpe: float
    strat_max_dd_pct: float
    bh_max_dd_pct: float

    @property
    def passes(self) -> bool:
        return (
            self.strat_sharpe >= self.bh_sharpe
            and self.strat_max_dd_pct < self.bh_max_dd_pct
            and self.strat_total_return_pct > 0.0
        )


@dataclass(frozen=True)
class SymbolResult:
    symbol: str
    n_daily_bars: int
    stats: tuple[StrategyStats, ...]


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    symbols: tuple[SymbolResult, ...]
    verdict: str
    passing_windows: tuple[int, ...]


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


def resample_daily(rows: Sequence[Mapping[str, object]]) -> list[DailyBar]:
    """Collapse intraday rows into UTC daily close bars (last close of each day)."""
    by_day: OrderedDict[datetime, float] = OrderedDict()
    for row in rows:
        ts = row["time"]
        if not isinstance(ts, datetime):
            continue
        day = ts.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        by_day[day] = float(row["close_price"])  # rows are time-ascending → last wins
    return [DailyBar(day=day, close_price=close) for day, close in by_day.items()]


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


def evaluate_window(bars: Sequence[DailyBar], window: int, fee_pct: float) -> StrategyStats | None:
    closes = [bar.close_price for bar in bars]
    sma = _simple_moving_average(closes, window)

    # First index where both SMA and a prior position decision exist.
    first = window  # need sma[t] (t>=window-1) and a position from t-1, and a return t-1->t
    if first >= len(closes) - 1:
        return None

    fee = fee_pct / 100.0
    strat_returns: list[float] = []
    bh_returns: list[float] = []
    prev_position = 0
    in_market_days = 0
    switches = 0

    for t in range(first, len(closes)):
        prior_sma = sma[t - 1]
        if prior_sma is None:
            continue
        position = 1 if closes[t - 1] > prior_sma else 0
        daily_ret = closes[t] / closes[t - 1] - 1.0

        gross = position * daily_ret
        cost = fee if position != prev_position else 0.0
        strat_returns.append(gross - cost)
        bh_returns.append(daily_ret)

        if position != prev_position:
            switches += 1
        if position == 1:
            in_market_days += 1
        prev_position = position

    if not strat_returns:
        return None

    n = len(strat_returns)
    return StrategyStats(
        window=window,
        n_days=n,
        n_switches=switches,
        time_in_market_pct=in_market_days / n * 100.0,
        strat_total_return_pct=_total_return_pct(strat_returns),
        bh_total_return_pct=_total_return_pct(bh_returns),
        strat_sharpe=_sharpe(strat_returns),
        bh_sharpe=_sharpe(bh_returns),
        strat_max_dd_pct=_max_drawdown_pct(strat_returns),
        bh_max_dd_pct=_max_drawdown_pct(bh_returns),
    )


def decide_verdict(
    report_symbols: Sequence[SymbolResult], config: ProbeConfig
) -> tuple[str, tuple[int, ...]]:
    if not report_symbols:
        return "NO_PULSE", ()
    n_symbols = len(report_symbols)
    passing_windows: list[int] = []
    any_pass = False
    for window in config.ma_windows:
        passes = 0
        for sym in report_symbols:
            stat = next((s for s in sym.stats if s.window == window), None)
            if stat is not None and stat.passes:
                passes += 1
                any_pass = True
        if passes >= math.ceil(config.min_symbol_majority * n_symbols):
            passing_windows.append(window)
    if passing_windows:
        return "HAS_PULSE", tuple(passing_windows)
    if any_pass:
        return "WEAK_EDGE", ()
    return "NO_PULSE", ()


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.higher_tf_trend_following")
    pool = await init_pool(build_db_config())
    try:
        symbols: list[SymbolResult] = []
        for symbol in config.symbols:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    PROBE_QUERY,
                    symbol,
                    config.source_timeframe,
                    datetime.fromisoformat(config.start),
                    datetime.fromisoformat(config.end),
                )
            daily = resample_daily(rows)
            max_window = max(config.ma_windows)
            if len(daily) < max_window + 30:
                logger.warning("insufficient daily bars for %s (%d)", symbol, len(daily))
                continue

            stats = tuple(
                stat
                for window in config.ma_windows
                if (stat := evaluate_window(daily, window, config.one_way_fee_pct)) is not None
            )
            symbols.append(SymbolResult(symbol=symbol, n_daily_bars=len(daily), stats=stats))
            for stat in stats:
                logger.info(
                    "%s sma%d: ret=%.1f%% (bh %.1f%%) sharpe=%.2f (bh %.2f) dd=%.1f%% (bh %.1f%%) "
                    "switches=%d pass=%s",
                    symbol,
                    stat.window,
                    stat.strat_total_return_pct,
                    stat.bh_total_return_pct,
                    stat.strat_sharpe,
                    stat.bh_sharpe,
                    stat.strat_max_dd_pct,
                    stat.bh_max_dd_pct,
                    stat.n_switches,
                    stat.passes,
                )

        verdict, passing_windows = decide_verdict(symbols, config)
        return ProbeReport(
            config=config,
            symbols=tuple(symbols),
            verdict=verdict,
            passing_windows=passing_windows,
        )
    finally:
        await close_pool()


def default_config() -> ProbeConfig:
    return ProbeConfig(
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        source_timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        ma_windows=(50, 100, 200),
        one_way_fee_pct=0.04,
        min_symbol_majority=0.66,
        min_total_return_pct=0.0,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append("# Higher-TF Trend-Following Probe — Report")
    lines.append("")
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_higher_tf_trend_following.py`")
    lines.append(
        "**Spec:** [higher-tf-trend-following-probe-v0.md]"
        "(../specs/higher-tf-trend-following-probe-v0.md)"
    )
    lines.append("")
    lines.append("## Config")
    lines.append(f"- Symbols: {', '.join(report.config.symbols)}")
    lines.append(f"- Daily bars resampled from: {report.config.source_timeframe}")
    lines.append(f"- Window: {report.config.start} → {report.config.end}")
    lines.append(f"- SMA windows: {', '.join(str(w) for w in report.config.ma_windows)}")
    lines.append(f"- One-way fee: {report.config.one_way_fee_pct}%")
    lines.append("")
    lines.append("## Per symbol / window (strategy vs buy-and-hold)")
    lines.append("")
    lines.append(
        "| Symbol | SMA | Ret% | BH Ret% | Sharpe | BH Sharpe | MaxDD% | BH MaxDD% | "
        "Switches | In-mkt% | Pass |"
    )
    lines.append(
        "|--------|-----|------|---------|--------|-----------|--------|-----------|"
        "----------|---------|------|"
    )
    for sym in report.symbols:
        for stat in sym.stats:
            lines.append(
                f"| {sym.symbol} | {stat.window} | {stat.strat_total_return_pct:.1f} | "
                f"{stat.bh_total_return_pct:.1f} | {stat.strat_sharpe:.2f} | "
                f"{stat.bh_sharpe:.2f} | {stat.strat_max_dd_pct:.1f} | "
                f"{stat.bh_max_dd_pct:.1f} | {stat.n_switches} | "
                f"{stat.time_in_market_pct:.0f} | {stat.passes} |"
            )
    lines.append("")
    if report.passing_windows:
        lines.append(
            "**Passing windows (symbol-majority):** "
            + ", ".join(f"SMA{w}" for w in report.passing_windows)
        )
    else:
        lines.append("**Passing windows (symbol-majority):** (none)")
    lines.append("")
    lines.append(f"**Overall verdict:** {report.verdict}")
    lines.append("")
    lines.append("See research-reset-2026-06-06.md for banned surfaces and next-lane rules.")
    return "\n".join(lines)


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cfg = default_config()
    report = await run_probe(cfg)

    if args.json:
        import json

        payload = {
            "verdict": report.verdict,
            "passing_windows": list(report.passing_windows),
            "symbols": [
                {
                    "symbol": sym.symbol,
                    "daily_bars": sym.n_daily_bars,
                    "windows": [
                        {
                            "sma": stat.window,
                            "ret_pct": round(stat.strat_total_return_pct, 2),
                            "bh_ret_pct": round(stat.bh_total_return_pct, 2),
                            "sharpe": round(stat.strat_sharpe, 3),
                            "bh_sharpe": round(stat.bh_sharpe, 3),
                            "max_dd_pct": round(stat.strat_max_dd_pct, 2),
                            "bh_max_dd_pct": round(stat.bh_max_dd_pct, 2),
                            "switches": stat.n_switches,
                            "pass": stat.passes,
                        }
                        for stat in sym.stats
                    ],
                }
                for sym in report.symbols
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_report(report))

    return 0 if report.verdict == "HAS_PULSE" else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
