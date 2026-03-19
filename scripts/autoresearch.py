#!/usr/bin/env python3
"""Automated strategy research — systematic search for profitable configurations.

Phase 1: Individual strategy profiling (long-only vs short-allowed, trend filter on/off)
Phase 2: Exit parameter sweep on best individual strategies
Phase 3: Strategy combinations (pairs, triples) with best exit params
Phase 4: Aggregator threshold tuning on best combos
"""

import asyncio
import itertools
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import load_settings
from src.strategy import (
    BollingerBounceStrategy,
    BreakoutRetestStrategy,
    CCIBreakoutStrategy,
    MACDHistogramStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    RSIReversalStrategy,
    SimpleMACrossoverStrategy,
    TrendPullbackStrategy,
    VWAPReversionStrategy,
)
from src.utils.logger import configure_logger


ALL_STRATEGIES = {
    "RSI": (RSIReversalStrategy, {"rsi_period": 7, "oversold_threshold": 35, "overbought_threshold": 65}),
    "MACD": (MACDHistogramStrategy, {"min_histogram_threshold": 0.0001, "use_atr_filter": True, "atr_min_pct": 0.003}),
    "BB": (BollingerBounceStrategy, {"band_distance_threshold": 0.003, "rsi_oversold": 35, "rsi_overbought": 65}),
    "CCI": (CCIBreakoutStrategy, {"cci_buy_threshold": 100, "cci_sell_threshold": -100, "atr_min_pct": 0.005}),
    "VWAP": (VWAPReversionStrategy, {"vwap_atr_multiplier": 1.5, "rsi_oversold": 40, "rsi_overbought": 60}),
    "MA": (SimpleMACrossoverStrategy, {}),
    "Breakout": (BreakoutRetestStrategy, {}),
    "Pullback": (TrendPullbackStrategy, {}),
    "Momentum": (MomentumStrategy, {}),
    "MeanRev": (MeanReversionStrategy, {}),
}


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
    extra: dict = field(default_factory=dict)

    @property
    def profitable(self) -> bool:
        return self.return_pct > 0 and self.trades >= 5

    def summary_line(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor < 100 else "inf"
        flag = " ***" if self.profitable else ""
        return (
            f"{self.label:<45} | {self.trades:>4} | {self.wins:>3}/{self.losses:>3} | "
            f"{self.win_rate:>5.1f}% | {self.return_pct:>+8.2f}% | {self.max_drawdown:>5.1f}% | "
            f"{self.sharpe:>+6.2f} | {pf:>5}{flag}"
        )


HEADER = f"{'Config':<45} | {'#':>4} | {'W':>3}/{'L':>3} | {'Win%':>5}  | {'Return%':>8} | {'MDD%':>5}  | {'Sharpe':>6} | {'PF':>5}"
SEP = "-" * 105


async def run_backtest(
    reader: IndicatorReader,
    strategy_classes: list,
    strategy_configs: list,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    label: str,
    *,
    allow_short: bool = False,
    trend_filter: bool = True,
    sl_atr: float = 2.0,
    tp_atr: float = 4.5,
    trailing_activate: float = 1.5,
    trailing_offset: float = 1.0,
    use_executor_exit: bool = True,
    stop_loss_pct: float = 0.02,
    take_profit_pct: float = 0.05,
    aggregator_config: dict | None = None,
    min_confidence: float = 0.0,
    buy_threshold: float = 0.5,
    sell_threshold: float = -0.5,
    min_agreement: int = 1,
) -> Result:
    agg = aggregator_config or {
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
        "min_agreement": min_agreement,
        "min_confidence": min_confidence,
    }

    config = BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        initial_capital=10000.0,
        fee_rate=0.001,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        sl_atr_multiplier=sl_atr,
        tp_atr_multiplier=tp_atr,
        trailing_activate_atr=trailing_activate,
        trailing_offset_atr=trailing_offset,
        use_executor_exit_model=use_executor_exit,
        ignore_signal_sells=False,
        apply_global_trend_filter=trend_filter,
        allow_short=allow_short,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
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


async def phase1_individual(reader, symbol, tf, start, end) -> list[Result]:
    """Test each strategy individually with different settings."""
    print(f"\n{'=' * 105}")
    print(f"  PHASE 1: Individual Strategy Profiling — {symbol} {tf} ({start} to {end})")
    print(f"{'=' * 105}")
    print(HEADER)
    print(SEP)

    results = []
    for name, (cls, cfg) in ALL_STRATEGIES.items():
        for allow_short in [False, True]:
            for trend_filter in [True, False]:
                for use_exec_exit in [True, False]:
                    short_tag = "S" if allow_short else "L"
                    tf_tag = "TF" if trend_filter else "noTF"
                    exit_tag = "ATR" if use_exec_exit else "Fix"
                    label = f"{name} [{short_tag},{tf_tag},{exit_tag}]"

                    r = await run_backtest(
                        reader, [cls], [cfg], symbol, tf, start, end, label,
                        allow_short=allow_short,
                        trend_filter=trend_filter,
                        use_executor_exit=use_exec_exit,
                    )
                    results.append(r)
                    print(r.summary_line())

    return results


async def phase2_exit_sweep(reader, symbol, tf, start, end, top_strategies: list[tuple]) -> list[Result]:
    """Sweep exit parameters on the best individual strategies."""
    print(f"\n{'=' * 105}")
    print(f"  PHASE 2: Exit Parameter Sweep — {symbol} {tf} ({start} to {end})")
    print(f"{'=' * 105}")
    print(HEADER)
    print(SEP)

    sl_atrs = [1.0, 1.5, 2.0, 2.5, 3.0]
    tp_atrs = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0]
    trailing_combos = [(1.0, 0.5), (1.5, 1.0), (2.0, 1.0), (2.5, 1.5)]

    results = []
    for name, cls, cfg, allow_short, trend_filter in top_strategies:
        short_tag = "S" if allow_short else "L"
        tf_tag = "TF" if trend_filter else "noTF"

        for sl in sl_atrs:
            for tp in tp_atrs:
                if tp <= sl:
                    continue  # TP must be > SL
                for trail_act, trail_off in trailing_combos:
                    label = f"{name}[{short_tag},{tf_tag}] SL{sl}/TP{tp}/T{trail_act},{trail_off}"

                    r = await run_backtest(
                        reader, [cls], [cfg], symbol, tf, start, end, label,
                        allow_short=allow_short,
                        trend_filter=trend_filter,
                        use_executor_exit=True,
                        sl_atr=sl,
                        tp_atr=tp,
                        trailing_activate=trail_act,
                        trailing_offset=trail_off,
                    )
                    results.append(r)
                    if r.sharpe > -1.0 or r.profitable:
                        print(r.summary_line())

    # Print top 20 by Sharpe
    print(f"\n--- Top 20 by Sharpe ---")
    print(HEADER)
    print(SEP)
    for r in sorted(results, key=lambda x: x.sharpe if x.trades > 3 else -999, reverse=True)[:20]:
        print(r.summary_line())

    return results


async def phase3_combinations(
    reader, symbol, tf, start, end,
    best_exit: dict,
    top_strats: list[tuple],
) -> list[Result]:
    """Test strategy combinations (pairs and triples)."""
    print(f"\n{'=' * 105}")
    print(f"  PHASE 3: Strategy Combinations — {symbol} {tf} ({start} to {end})")
    print(f"{'=' * 105}")
    print(HEADER)
    print(SEP)

    results = []

    # Test pairs
    for combo in itertools.combinations(top_strats, 2):
        names = "+".join(c[0] for c in combo)
        classes = [c[1] for c in combo]
        configs = [c[2] for c in combo]

        for allow_short in [False, True]:
            for min_agree in [1, 2]:
                short_tag = "S" if allow_short else "L"
                label = f"{names} [{short_tag},agree={min_agree}]"

                r = await run_backtest(
                    reader, classes, configs, symbol, tf, start, end, label,
                    allow_short=allow_short,
                    trend_filter=True,
                    min_agreement=min_agree,
                    **best_exit,
                )
                results.append(r)
                print(r.summary_line())

    # Test triples
    if len(top_strats) >= 3:
        for combo in itertools.combinations(top_strats, 3):
            names = "+".join(c[0] for c in combo)
            classes = [c[1] for c in combo]
            configs = [c[2] for c in combo]

            for allow_short in [False, True]:
                for min_agree in [1, 2]:
                    short_tag = "S" if allow_short else "L"
                    label = f"{names} [{short_tag},agree={min_agree}]"

                    r = await run_backtest(
                        reader, classes, configs, symbol, tf, start, end, label,
                        allow_short=allow_short,
                        trend_filter=True,
                        min_agreement=min_agree,
                        **best_exit,
                    )
                    results.append(r)
                    if r.sharpe > -1.0 or r.profitable:
                        print(r.summary_line())

    # Print top 20
    print(f"\n--- Top 20 Combos by Sharpe ---")
    print(HEADER)
    print(SEP)
    for r in sorted(results, key=lambda x: x.sharpe if x.trades > 3 else -999, reverse=True)[:20]:
        print(r.summary_line())

    return results


async def phase4_aggregator_tuning(
    reader, symbol, tf, start, end,
    best_combo: tuple,  # (classes, configs, allow_short)
    best_exit: dict,
) -> list[Result]:
    """Fine-tune aggregator thresholds on the best combo."""
    print(f"\n{'=' * 105}")
    print(f"  PHASE 4: Aggregator Tuning — {symbol} {tf} ({start} to {end})")
    print(f"{'=' * 105}")
    print(HEADER)
    print(SEP)

    classes, configs, allow_short = best_combo
    results = []

    buy_thresholds = [0.3, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5]
    sell_thresholds = [-0.3, -0.5, -0.6, -0.8, -1.0, -1.5]
    min_confidences = [0.0, 0.3, 0.5, 0.6, 0.7]

    for bt in buy_thresholds:
        for st in sell_thresholds:
            for mc in min_confidences:
                label = f"buy={bt}/sell={st}/mc={mc}"
                r = await run_backtest(
                    reader, classes, configs, symbol, tf, start, end, label,
                    allow_short=allow_short,
                    trend_filter=True,
                    buy_threshold=bt,
                    sell_threshold=st,
                    min_confidence=mc,
                    **best_exit,
                )
                results.append(r)
                if r.sharpe > -0.5 or r.profitable:
                    print(r.summary_line())

    print(f"\n--- Top 20 Aggregator Configs ---")
    print(HEADER)
    print(SEP)
    for r in sorted(results, key=lambda x: x.sharpe if x.trades > 3 else -999, reverse=True)[:20]:
        print(r.summary_line())

    return results


async def main():
    configure_logger("WARNING")

    config_path = Path("config/settings.yaml")
    settings = load_settings(config_path)

    db_config = {
        "host": str(os.getenv("DB_HOST", settings.database.get("host", "localhost"))),
        "port": int(os.getenv("DB_PORT", int(settings.database.get("port", 5432)))),
        "name": str(os.getenv("DB_NAME", settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("DB_USER", settings.database.get("user", "trading"))),
        "password": str(os.getenv("DB_PASSWORD", settings.database.get("password", ""))),
    }

    await init_pool(db_config)

    symbol = "SOLUSDT"
    timeframe = "4h"
    # Use 2024 for discovery, 2025 for validation
    train_start, train_end = "2024-01-01", "2024-12-31"
    val_start, val_end = "2025-01-01", "2025-06-30"

    try:
        reader = IndicatorReader(db_config)
        async with reader:
            # ── PHASE 1: Individual strategies ──
            p1_results = await phase1_individual(reader, symbol, timeframe, train_start, train_end)

            # Select top 5 by Sharpe (with at least 5 trades)
            viable = [r for r in p1_results if r.trades >= 5]
            viable.sort(key=lambda x: x.sharpe, reverse=True)

            print(f"\n{'=' * 105}")
            print("  PHASE 1 WINNERS (Top 10)")
            print(f"{'=' * 105}")
            print(HEADER)
            print(SEP)
            for r in viable[:10]:
                print(r.summary_line())

            # Extract strategy info from top results for phase 2
            # Parse label to recover strategy name and settings
            top_for_exit = []
            seen = set()
            for r in viable[:10]:
                # Parse: "RSI [L,TF,ATR]" → name="RSI", allow_short=False, trend_filter=True
                parts = r.label.split(" [")
                name = parts[0]
                if name in seen:
                    continue
                seen.add(name)
                flags = parts[1].rstrip("]").split(",")
                allow_short = flags[0] == "S"
                trend_filter = flags[1] == "TF"
                cls, cfg = ALL_STRATEGIES[name]
                top_for_exit.append((name, cls, cfg, allow_short, trend_filter))

            if not top_for_exit:
                print("\nNo viable strategies found. Aborting.")
                return

            # ── PHASE 2: Exit parameter sweep on top 3 strategies ──
            p2_results = await phase2_exit_sweep(
                reader, symbol, timeframe, train_start, train_end,
                top_for_exit[:3],
            )

            # Find best exit params
            best_p2 = max(
                [r for r in p2_results if r.trades >= 5],
                key=lambda x: x.sharpe,
                default=None,
            )

            if best_p2:
                # Parse exit params from label: "RSI[L,TF] SL2.0/TP4.0/T1.5,1.0"
                exit_part = best_p2.label.split("] ")[1] if "] " in best_p2.label else ""
                print(f"\n  Best exit config: {exit_part} (Sharpe={best_p2.sharpe:+.2f})")

                # Extract numeric values
                import re
                sl_match = re.search(r"SL([\d.]+)", exit_part)
                tp_match = re.search(r"TP([\d.]+)", exit_part)
                trail_match = re.search(r"T([\d.]+),([\d.]+)", exit_part)

                best_exit = {
                    "sl_atr": float(sl_match.group(1)) if sl_match else 2.0,
                    "tp_atr": float(tp_match.group(1)) if tp_match else 4.5,
                    "trailing_activate": float(trail_match.group(1)) if trail_match else 1.5,
                    "trailing_offset": float(trail_match.group(2)) if trail_match else 1.0,
                    "use_executor_exit": True,
                }
            else:
                best_exit = {
                    "sl_atr": 2.0, "tp_atr": 4.5,
                    "trailing_activate": 1.5, "trailing_offset": 1.0,
                    "use_executor_exit": True,
                }

            # ── PHASE 3: Strategy combinations ──
            # Use top 5 unique strategies for combos
            combo_strats = [(n, c, cfg) for n, c, cfg, _, _ in top_for_exit[:5]]
            p3_results = await phase3_combinations(
                reader, symbol, timeframe, train_start, train_end,
                best_exit, combo_strats,
            )

            # Find best combo
            best_p3 = max(
                [r for r in p3_results if r.trades >= 5],
                key=lambda x: x.sharpe,
                default=None,
            )

            if best_p3:
                print(f"\n  Best combo: {best_p3.label} (Sharpe={best_p3.sharpe:+.2f})")
                # Parse combo for phase 4
                combo_part = best_p3.label.split(" [")[0]
                flags = best_p3.label.split(" [")[1].rstrip("]").split(",")
                allow_short = flags[0] == "S"

                combo_names = combo_part.split("+")
                classes = [ALL_STRATEGIES[n][0] for n in combo_names]
                configs = [ALL_STRATEGIES[n][1] for n in combo_names]

                # ── PHASE 4: Aggregator tuning ──
                p4_results = await phase4_aggregator_tuning(
                    reader, symbol, timeframe, train_start, train_end,
                    (classes, configs, allow_short),
                    best_exit,
                )

            # ── VALIDATION: Run best configs on 2025 data ──
            print(f"\n{'=' * 105}")
            print(f"  VALIDATION: Best Configs on {val_start} to {val_end}")
            print(f"{'=' * 105}")
            print(HEADER)
            print(SEP)

            # Collect all profitable or top results
            all_results = p1_results + p2_results + p3_results
            if best_p3:
                all_results += p4_results

            top_all = sorted(
                [r for r in all_results if r.trades >= 5],
                key=lambda x: x.sharpe, reverse=True,
            )[:15]

            # For validation, re-run top configs
            # We need to reconstruct the configs — for now validate the single best
            if best_p3 and p4_results:
                best_overall = max(
                    [r for r in p4_results if r.trades >= 5],
                    key=lambda x: x.sharpe,
                    default=best_p3,
                )
            elif best_p2:
                best_overall = best_p2
            else:
                best_overall = viable[0] if viable else None

            if best_overall:
                print(f"\n  Training best: {best_overall.label} → Sharpe={best_overall.sharpe:+.2f}, Return={best_overall.return_pct:+.2f}%")

            # Also run all individual strategies on validation for comparison
            print(f"\n  Running all individuals on validation period...")
            val_results = await phase1_individual(reader, symbol, timeframe, val_start, val_end)

            val_viable = sorted(
                [r for r in val_results if r.trades >= 3],
                key=lambda x: x.sharpe, reverse=True,
            )

            print(f"\n{'=' * 105}")
            print("  VALIDATION WINNERS (Top 10)")
            print(f"{'=' * 105}")
            print(HEADER)
            print(SEP)
            for r in val_viable[:10]:
                print(r.summary_line())

            # Final summary of profitable configs
            all_profitable = [r for r in p1_results + p2_results + p3_results + (p4_results if best_p3 else []) if r.profitable]
            if all_profitable:
                print(f"\n{'=' * 105}")
                print(f"  ALL PROFITABLE CONFIGS FOUND ({len(all_profitable)})")
                print(f"{'=' * 105}")
                print(HEADER)
                print(SEP)
                for r in sorted(all_profitable, key=lambda x: x.sharpe, reverse=True):
                    print(r.summary_line())
            else:
                print(f"\n  ⚠ No profitable configurations found in training period.")
                print("  Consider: different timeframes, different symbols, or strategy parameter tuning.")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
