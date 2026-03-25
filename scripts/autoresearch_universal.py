#!/usr/bin/env python3
"""Universal autoresearch — runs on any symbol/timeframe.

Usage:
    python scripts/autoresearch_universal.py BTCUSDT 4h
    python scripts/autoresearch_universal.py BNBUSDT 4h
    python scripts/autoresearch_universal.py ETHUSDT 1h

Phases:
1. Individual strategy profiling (10 strategies x 4 configs each)
2. Exit parameter sweep on top 3 strategies
3. Strategy combos (pairs, triples) with best exit params
4. Output saved to docs/reports/autoresearch-{symbol}-{tf}-{date}.txt
"""

import asyncio
import itertools
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import load_settings
from src.strategy import (
    BollingerBounceStrategy,
    CCIBreakoutStrategy,
    MACDHistogramStrategy,
    MomentumStrategy,
    RSIReversalStrategy,
    SimpleMACrossoverStrategy,
    VWAPReversionStrategy,
)
from src.utils.logger import configure_logger

ALL_STRATEGIES = {
    "MA": (SimpleMACrossoverStrategy, {}),
    "MACD": (
        MACDHistogramStrategy,
        {"min_histogram_threshold": 0.0001, "use_atr_filter": True, "atr_min_pct": 0.003},
    ),
    "RSI": (
        RSIReversalStrategy,
        {"rsi_period": 7, "oversold_threshold": 35, "overbought_threshold": 65},
    ),
    "BB": (
        BollingerBounceStrategy,
        {"band_distance_threshold": 0.003, "rsi_oversold": 35, "rsi_overbought": 65},
    ),
    "CCI": (
        CCIBreakoutStrategy,
        {"cci_buy_threshold": 100, "cci_sell_threshold": -100, "atr_min_pct": 0.005},
    ),
    "VWAP": (
        VWAPReversionStrategy,
        {"vwap_atr_multiplier": 1.5, "rsi_oversold": 40, "rsi_overbought": 60},
    ),
    "Momentum": (MomentumStrategy, {}),
}

output_lines: list[str] = []


def out(line: str = ""):
    print(line)
    output_lines.append(line)


@dataclass
class Result:
    label: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    return_pct: float
    max_drawdown: float
    sharpe: float
    profit_factor: float

    @property
    def profitable(self) -> bool:
        return self.return_pct > 0 and self.trades >= 5

    def summary_line(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor < 100 else "inf"
        flag = " ***" if self.profitable else ""
        return (
            f"{self.label:<50} | {self.trades:>4} | {self.wins:>3}/{self.losses:>3} | "
            f"{self.win_rate:>5.1f}% | {self.return_pct:>+8.2f}% | {self.max_drawdown:>5.1f}% | "
            f"{self.sharpe:>+6.2f} | {pf:>5}{flag}"
        )


HEADER = f"{'Config':<50} | {'#':>4} | {'W':>3}/{'L':>3} | {'Win%':>5}  | {'Return%':>8} | {'MDD%':>5}  | {'Sharpe':>6} | {'PF':>5}"
SEP = "-" * 110


async def run_bt(reader, classes, configs, symbol, tf, start, end, label, **kw) -> Result:
    agg = {
        "buy_threshold": kw.pop("buy_threshold", 0.5),
        "sell_threshold": kw.pop("sell_threshold", -0.5),
        "min_agreement": kw.pop("min_agreement", 1),
        "min_confidence": kw.pop("min_confidence", 0.0),
    }

    config = BacktestConfig(
        symbol=symbol,
        timeframe=tf,
        start_date=start,
        end_date=end,
        initial_capital=10000.0,
        fee_rate=0.001,
        stop_loss_pct=kw.get("stop_loss_pct", 0.02),
        take_profit_pct=kw.get("take_profit_pct", 0.05),
        sl_atr_multiplier=kw.get("sl_atr", 2.0),
        tp_atr_multiplier=kw.get("tp_atr", 4.5),
        trailing_activate_atr=kw.get("trailing_activate", 1.5),
        trailing_offset_atr=kw.get("trailing_offset", 1.0),
        use_executor_exit_model=kw.get("use_executor_exit", False),
        apply_global_trend_filter=kw.get("trend_filter", True),
        allow_short=kw.get("allow_short", True),
        strategy_classes=classes,
        strategy_configs=configs,
        aggregator_config=agg,
    )

    engine = BacktestEngine(config, reader)
    r = await engine.run()
    wins = len([t for t in r.trades if t.pnl > 0])
    losses = len([t for t in r.trades if t.pnl <= 0])

    return Result(
        label=label,
        trades=r.total_trades,
        wins=wins,
        losses=losses,
        win_rate=r.win_rate,
        return_pct=r.total_return_pct,
        max_drawdown=r.max_drawdown * 100,
        sharpe=r.sharpe_ratio,
        profit_factor=r.profit_factor,
    )


async def phase1(reader, symbol, tf, start, end) -> list[Result]:
    out(f"\n{'=' * 110}")
    out(f"  PHASE 1: Individual Strategy Profiling — {symbol} {tf} ({start} to {end})")
    out(f"{'=' * 110}")
    out(HEADER)
    out(SEP)

    results = []
    for name, (cls, cfg) in ALL_STRATEGIES.items():
        for allow_short in [False, True]:
            for trend_filter in [True, False]:
                for use_exec in [True, False]:
                    s = "S" if allow_short else "L"
                    t = "TF" if trend_filter else "noTF"
                    e = "ATR" if use_exec else "Fix"
                    label = f"{name} [{s},{t},{e}]"
                    r = await run_bt(
                        reader,
                        [cls],
                        [cfg],
                        symbol,
                        tf,
                        start,
                        end,
                        label,
                        allow_short=allow_short,
                        trend_filter=trend_filter,
                        use_executor_exit=use_exec,
                    )
                    results.append(r)
                    out(r.summary_line())

    return results


async def phase2(reader, symbol, tf, start, end, top_strats) -> list[Result]:
    out(f"\n{'=' * 110}")
    out(f"  PHASE 2: Exit Sweep — {symbol} {tf} ({start} to {end})")
    out(f"{'=' * 110}")

    sl_atrs = [1.0, 1.5, 2.0, 2.5]
    tp_atrs = [2.0, 3.0, 4.5, 6.0]
    trails = [(1.0, 0.5), (1.5, 1.0), (2.0, 1.0), (2.5, 1.5)]

    results = []
    for name, cls, cfg, allow_short, trend_filter in top_strats:
        s = "S" if allow_short else "L"
        t = "TF" if trend_filter else "noTF"
        for sl in sl_atrs:
            for tp in tp_atrs:
                if tp <= sl:
                    continue
                for ta, to_ in trails:
                    label = f"{name}[{s},{t}] SL{sl}/TP{tp}/T{ta},{to_}"
                    r = await run_bt(
                        reader,
                        [cls],
                        [cfg],
                        symbol,
                        tf,
                        start,
                        end,
                        label,
                        allow_short=allow_short,
                        trend_filter=trend_filter,
                        use_executor_exit=True,
                        sl_atr=sl,
                        tp_atr=tp,
                        trailing_activate=ta,
                        trailing_offset=to_,
                    )
                    results.append(r)

    out("\n--- Top 20 Exit Configs by Sharpe ---")
    out(HEADER)
    out(SEP)
    for r in sorted(results, key=lambda x: x.sharpe if x.trades > 3 else -999, reverse=True)[:20]:
        out(r.summary_line())

    return results


async def phase3(reader, symbol, tf, start, end, best_exit, top_strats) -> list[Result]:
    out(f"\n{'=' * 110}")
    out(f"  PHASE 3: Strategy Combos — {symbol} {tf} ({start} to {end})")
    out(f"{'=' * 110}")
    out(HEADER)
    out(SEP)

    results = []
    for size in [2, 3]:
        if len(top_strats) < size:
            continue
        for combo in itertools.combinations(top_strats, size):
            names = "+".join(c[0] for c in combo)
            classes = [c[1] for c in combo]
            configs = [c[2] for c in combo]
            for allow_short in [False, True]:
                for min_agree in [1, 2]:
                    s = "S" if allow_short else "L"
                    label = f"{names} [{s},agree={min_agree}]"
                    r = await run_bt(
                        reader,
                        classes,
                        configs,
                        symbol,
                        tf,
                        start,
                        end,
                        label,
                        allow_short=allow_short,
                        trend_filter=True,
                        min_agreement=min_agree,
                        **best_exit,
                    )
                    results.append(r)
                    out(r.summary_line())

    return results


async def validation(reader, symbol, tf, start, end, all_results) -> list[Result]:
    out(f"\n{'=' * 110}")
    out(f"  VALIDATION: {symbol} {tf} ({start} to {end})")
    out(f"{'=' * 110}")
    out(HEADER)
    out(SEP)

    results = []
    # Re-run all individual strategies on validation
    for name, (cls, cfg) in ALL_STRATEGIES.items():
        for allow_short in [False, True]:
            for trend_filter in [True, False]:
                for use_exec in [True, False]:
                    s = "S" if allow_short else "L"
                    t = "TF" if trend_filter else "noTF"
                    e = "ATR" if use_exec else "Fix"
                    label = f"{name} [{s},{t},{e}]"
                    r = await run_bt(
                        reader,
                        [cls],
                        [cfg],
                        symbol,
                        tf,
                        start,
                        end,
                        label,
                        allow_short=allow_short,
                        trend_filter=trend_filter,
                        use_executor_exit=use_exec,
                    )
                    results.append(r)

    out("\n--- Validation Top 10 ---")
    out(HEADER)
    out(SEP)
    viable = sorted([r for r in results if r.trades >= 3], key=lambda x: x.sharpe, reverse=True)
    for r in viable[:10]:
        out(r.summary_line())

    return results


async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/autoresearch_universal.py SYMBOL TIMEFRAME")
        print("Example: python scripts/autoresearch_universal.py BTCUSDT 4h")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    tf = sys.argv[2]

    configure_logger("WARNING")
    settings = load_settings(Path("config/settings.yaml"))

    db_config = {
        "host": str(os.getenv("DB_HOST", settings.database.get("host", "localhost"))),
        "port": int(os.getenv("DB_PORT", int(settings.database.get("port", 5432)))),
        "name": str(os.getenv("DB_NAME", settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("DB_USER", settings.database.get("user", "trading"))),
        "password": str(os.getenv("DB_PASSWORD", settings.database.get("password", ""))),
    }
    await init_pool(db_config)

    train_start, train_end = "2024-01-01", "2024-12-31"
    val_start, val_end = "2025-01-01", "2025-06-30"

    out(f"Autoresearch: {symbol} {tf}")
    out(f"Training: {train_start} to {train_end}")
    out(f"Validation: {val_start} to {val_end}")

    try:
        reader = IndicatorReader(db_config)
        async with reader:
            # Phase 1
            p1 = await phase1(reader, symbol, tf, train_start, train_end)
            viable = sorted([r for r in p1 if r.trades >= 5], key=lambda x: x.sharpe, reverse=True)

            out("\n--- Phase 1 Winners (Top 10) ---")
            out(HEADER)
            out(SEP)
            for r in viable[:10]:
                out(r.summary_line())

            # Extract top strategies for phase 2
            top = []
            seen = set()
            for r in viable[:10]:
                parts = r.label.split(" [")
                name = parts[0]
                if name in seen:
                    continue
                seen.add(name)
                flags = parts[1].rstrip("]").split(",")
                allow_short = flags[0] == "S"
                trend_filter = flags[1] == "TF"
                cls, cfg = ALL_STRATEGIES[name]
                top.append((name, cls, cfg, allow_short, trend_filter))

            if not top:
                out("\nNo viable strategies. Aborting.")
                return

            # Phase 2
            p2 = await phase2(reader, symbol, tf, train_start, train_end, top[:3])

            best_p2 = max([r for r in p2 if r.trades >= 5], key=lambda x: x.sharpe, default=None)
            if best_p2:
                exit_part = best_p2.label.split("] ")[1] if "] " in best_p2.label else ""
                out(f"\n  Best exit: {exit_part} (Sharpe={best_p2.sharpe:+.2f})")
                sl_m = re.search(r"SL([\d.]+)", exit_part)
                tp_m = re.search(r"TP([\d.]+)", exit_part)
                tr_m = re.search(r"T([\d.]+),([\d.]+)", exit_part)
                best_exit = {
                    "sl_atr": float(sl_m.group(1)) if sl_m else 2.0,
                    "tp_atr": float(tp_m.group(1)) if tp_m else 4.5,
                    "trailing_activate": float(tr_m.group(1)) if tr_m else 1.5,
                    "trailing_offset": float(tr_m.group(2)) if tr_m else 1.0,
                    "use_executor_exit": True,
                }
            else:
                best_exit = {
                    "sl_atr": 2.0,
                    "tp_atr": 4.5,
                    "trailing_activate": 1.5,
                    "trailing_offset": 1.0,
                    "use_executor_exit": True,
                }

            # Phase 3
            combo_strats = [(n, c, cfg) for n, c, cfg, _, _ in top[:5]]
            p3 = await phase3(reader, symbol, tf, train_start, train_end, best_exit, combo_strats)

            # Validation
            val = await validation(reader, symbol, tf, val_start, val_end, p1 + p2 + p3)

            # Final: cross-reference training profitable with validation
            train_profitable = {r.label for r in p1 if r.profitable}
            val_profitable = [r for r in val if r.profitable]
            both = [r for r in val_profitable if r.label in train_profitable]

            out(f"\n{'=' * 110}")
            out("  CROSS-VALIDATED: Profitable in BOTH training AND validation")
            out(f"{'=' * 110}")
            if both:
                out(HEADER)
                out(SEP)
                for r in sorted(both, key=lambda x: x.sharpe, reverse=True):
                    out(r.summary_line())
                    # Also show training result
                    train_r = next((tr for tr in p1 if tr.label == r.label), None)
                    if train_r:
                        out(
                            f"  └─ Training: {train_r.return_pct:+.2f}%, Sharpe={train_r.sharpe:+.2f}, {train_r.trades} trades"
                        )
            else:
                out("  None found. Showing validation top 5 regardless:")
                out(HEADER)
                out(SEP)
                for r in sorted(
                    [r for r in val if r.trades >= 3], key=lambda x: x.sharpe, reverse=True
                )[:5]:
                    out(r.summary_line())

            # Save report
            report_path = Path(
                f"docs/reports/autoresearch-{symbol.lower()}-{tf}-{date.today()}.txt"
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("\n".join(output_lines) + "\n")
            out(f"\nReport saved to {report_path}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
