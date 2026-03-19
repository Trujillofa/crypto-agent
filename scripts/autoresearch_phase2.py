#!/usr/bin/env python3
"""Phase 2 autoresearch: Deep-dive on MA + CCI (the validated winners).

Tests:
1. MA solo with refined exit params on full 2024-2025
2. CCI solo with refined exit params on full 2024-2025
3. MA+CCI combos with aggregator tuning
4. Cross-validation on rolling 6-month windows
"""

import asyncio
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
    CCIBreakoutStrategy,
    MACDHistogramStrategy,
    SimpleMACrossoverStrategy,
)
from src.utils.logger import configure_logger


@dataclass
class Result:
    label: str
    period: str
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


async def run_bt(reader, classes, configs, symbol, tf, start, end, label, **kwargs) -> Result:
    agg = kwargs.pop("aggregator_config", None) or {
        "buy_threshold": kwargs.pop("buy_threshold", 0.5),
        "sell_threshold": kwargs.pop("sell_threshold", -0.5),
        "min_agreement": kwargs.pop("min_agreement", 1),
        "min_confidence": kwargs.pop("min_confidence", 0.0),
    }

    config = BacktestConfig(
        symbol=symbol, timeframe=tf, start_date=start, end_date=end,
        initial_capital=10000.0, fee_rate=0.001,
        stop_loss_pct=kwargs.get("stop_loss_pct", 0.02),
        take_profit_pct=kwargs.get("take_profit_pct", 0.05),
        sl_atr_multiplier=kwargs.get("sl_atr", 2.0),
        tp_atr_multiplier=kwargs.get("tp_atr", 4.5),
        trailing_activate_atr=kwargs.get("trailing_activate", 1.5),
        trailing_offset_atr=kwargs.get("trailing_offset", 1.0),
        use_executor_exit_model=kwargs.get("use_executor_exit", False),
        ignore_signal_sells=False,
        apply_global_trend_filter=kwargs.get("trend_filter", True),
        allow_short=kwargs.get("allow_short", True),
        strategy_classes=classes, strategy_configs=configs,
        aggregator_config=agg,
    )

    engine = BacktestEngine(config, reader)
    r = await engine.run()
    wins = len([t for t in r.trades if t.pnl > 0])
    losses = len([t for t in r.trades if t.pnl <= 0])

    return Result(
        label=label, period=f"{start} to {end}",
        trades=r.total_trades, wins=wins, losses=losses,
        win_rate=r.win_rate, return_pct=r.total_return_pct,
        max_drawdown=r.max_drawdown * 100, sharpe=r.sharpe_ratio,
        profit_factor=r.profit_factor,
    )


async def main():
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

    symbol = "SOLUSDT"
    tf = "4h"

    # Rolling windows for robustness
    periods = [
        ("2024-01-01", "2024-06-30", "2024-H1"),
        ("2024-07-01", "2024-12-31", "2024-H2"),
        ("2025-01-01", "2025-06-30", "2025-H1"),
        ("2024-01-01", "2024-12-31", "2024-Full"),
        ("2025-01-01", "2025-12-31", "2025-Full"),
        ("2024-01-01", "2025-12-31", "All"),
    ]

    MA = SimpleMACrossoverStrategy
    CCI = CCIBreakoutStrategy
    MACD = MACDHistogramStrategy

    cci_cfg = {"cci_buy_threshold": 100, "cci_sell_threshold": -100, "atr_min_pct": 0.005}
    macd_cfg = {"min_histogram_threshold": 0.0001, "use_atr_filter": True, "atr_min_pct": 0.003}

    try:
        reader = IndicatorReader(db_config)
        async with reader:

            # ═══════════════════════════════════════════════════════════════
            # SECTION 1: MA Crossover — refined exit sweep across all periods
            # ═══════════════════════════════════════════════════════════════
            print(f"\n{'=' * 110}")
            print("  SECTION 1: MA Crossover — Exit & Config Sweep")
            print(f"{'=' * 110}")

            ma_results = []
            configs_to_test = [
                # (label_suffix, kwargs)
                ("Fix,S,TF", {"allow_short": True, "trend_filter": True, "use_executor_exit": False}),
                ("Fix,S,noTF", {"allow_short": True, "trend_filter": False, "use_executor_exit": False}),
                ("ATR1.0/T2.5,S,TF", {"allow_short": True, "trend_filter": True, "use_executor_exit": True,
                                        "sl_atr": 1.0, "tp_atr": 2.0, "trailing_activate": 2.5, "trailing_offset": 1.5}),
                ("ATR1.0/T2.0,S,TF", {"allow_short": True, "trend_filter": True, "use_executor_exit": True,
                                        "sl_atr": 1.0, "tp_atr": 3.0, "trailing_activate": 2.0, "trailing_offset": 1.0}),
                ("ATR1.5/T2.0,S,TF", {"allow_short": True, "trend_filter": True, "use_executor_exit": True,
                                        "sl_atr": 1.5, "tp_atr": 3.0, "trailing_activate": 2.0, "trailing_offset": 1.0}),
                ("Fix,L,TF", {"allow_short": False, "trend_filter": True, "use_executor_exit": False}),
                ("Fix,L,noTF", {"allow_short": False, "trend_filter": False, "use_executor_exit": False}),
            ]

            for start, end, period_label in periods:
                print(f"\n  --- {period_label} ({start} to {end}) ---")
                print(HEADER)
                print(SEP)
                for cfg_label, kwargs in configs_to_test:
                    label = f"MA [{cfg_label}]"
                    r = await run_bt(reader, [MA], [{}], symbol, tf, start, end, label, **kwargs)
                    ma_results.append(r)
                    print(r.summary_line())

            # ═══════════════════════════════════════════════════════════════
            # SECTION 2: CCI — refined configs across all periods
            # ═══════════════════════════════════════════════════════════════
            print(f"\n{'=' * 110}")
            print("  SECTION 2: CCI Breakout — Config Sweep")
            print(f"{'=' * 110}")

            cci_results = []
            cci_configs = [
                ("Fix,S,noTF", {"allow_short": True, "trend_filter": False, "use_executor_exit": False}),
                ("Fix,S,TF", {"allow_short": True, "trend_filter": True, "use_executor_exit": False}),
                ("Fix,L,TF", {"allow_short": False, "trend_filter": True, "use_executor_exit": False}),
                ("Fix,L,noTF", {"allow_short": False, "trend_filter": False, "use_executor_exit": False}),
                # CCI with different thresholds
                ("Fix,S,noTF,cci80", {"allow_short": True, "trend_filter": False, "use_executor_exit": False}),
                ("Fix,S,noTF,cci120", {"allow_short": True, "trend_filter": False, "use_executor_exit": False}),
            ]

            cci_param_variants = [
                cci_cfg,
                cci_cfg,
                cci_cfg,
                cci_cfg,
                {"cci_buy_threshold": 80, "cci_sell_threshold": -80, "atr_min_pct": 0.005},
                {"cci_buy_threshold": 120, "cci_sell_threshold": -120, "atr_min_pct": 0.005},
            ]

            for start, end, period_label in periods:
                print(f"\n  --- {period_label} ({start} to {end}) ---")
                print(HEADER)
                print(SEP)
                for (cfg_label, kwargs), ccfg in zip(cci_configs, cci_param_variants):
                    label = f"CCI [{cfg_label}]"
                    r = await run_bt(reader, [CCI], [ccfg], symbol, tf, start, end, label, **kwargs)
                    cci_results.append(r)
                    print(r.summary_line())

            # ═══════════════════════════════════════════════════════════════
            # SECTION 3: MA+CCI and MA+MACD combos
            # ═══════════════════════════════════════════════════════════════
            print(f"\n{'=' * 110}")
            print("  SECTION 3: Strategy Combos — MA+CCI, MA+MACD, MA+CCI+MACD")
            print(f"{'=' * 110}")

            combo_results = []
            combo_configs = [
                # MA+CCI
                ("MA+CCI [Fix,S,TF,ag1]", [MA, CCI], [{}, cci_cfg],
                 {"allow_short": True, "trend_filter": True, "min_agreement": 1}),
                ("MA+CCI [Fix,S,TF,ag2]", [MA, CCI], [{}, cci_cfg],
                 {"allow_short": True, "trend_filter": True, "min_agreement": 2}),
                ("MA+CCI [Fix,S,noTF,ag1]", [MA, CCI], [{}, cci_cfg],
                 {"allow_short": True, "trend_filter": False, "min_agreement": 1}),
                ("MA+CCI [Fix,L,TF,ag1]", [MA, CCI], [{}, cci_cfg],
                 {"allow_short": False, "trend_filter": True, "min_agreement": 1}),
                # MA+MACD
                ("MA+MACD [Fix,S,TF,ag1]", [MA, MACD], [{}, macd_cfg],
                 {"allow_short": True, "trend_filter": True, "min_agreement": 1}),
                ("MA+MACD [Fix,S,TF,ag2]", [MA, MACD], [{}, macd_cfg],
                 {"allow_short": True, "trend_filter": True, "min_agreement": 2}),
                ("MA+MACD [Fix,L,TF,ag1]", [MA, MACD], [{}, macd_cfg],
                 {"allow_short": False, "trend_filter": True, "min_agreement": 1}),
                # Triple
                ("MA+CCI+MACD [Fix,S,TF,ag1]", [MA, CCI, MACD], [{}, cci_cfg, macd_cfg],
                 {"allow_short": True, "trend_filter": True, "min_agreement": 1}),
                ("MA+CCI+MACD [Fix,S,TF,ag2]", [MA, CCI, MACD], [{}, cci_cfg, macd_cfg],
                 {"allow_short": True, "trend_filter": True, "min_agreement": 2}),
                ("MA+CCI+MACD [Fix,L,TF,ag2]", [MA, CCI, MACD], [{}, cci_cfg, macd_cfg],
                 {"allow_short": False, "trend_filter": True, "min_agreement": 2}),
            ]

            for start, end, period_label in periods:
                print(f"\n  --- {period_label} ({start} to {end}) ---")
                print(HEADER)
                print(SEP)
                for label, classes, cfgs, kwargs in combo_configs:
                    r = await run_bt(reader, classes, cfgs, symbol, tf, start, end, label, **kwargs)
                    combo_results.append(r)
                    print(r.summary_line())

            # ═══════════════════════════════════════════════════════════════
            # SECTION 4: Best config with aggregator fine-tuning
            # ═══════════════════════════════════════════════════════════════
            print(f"\n{'=' * 110}")
            print("  SECTION 4: Aggregator Fine-Tuning on MA [Fix,S,TF]")
            print(f"{'=' * 110}")

            agg_results = []
            buy_thresholds = [0.3, 0.5, 0.8, 1.0]
            sell_thresholds = [-0.3, -0.5, -0.8]
            min_confs = [0.0, 0.5, 0.7]

            for start, end, period_label in periods:
                print(f"\n  --- {period_label} ({start} to {end}) ---")
                print(HEADER)
                print(SEP)
                for bt in buy_thresholds:
                    for st in sell_thresholds:
                        for mc in min_confs:
                            label = f"MA[S,TF] bt={bt}/st={st}/mc={mc}"
                            r = await run_bt(
                                reader, [MA], [{}], symbol, tf, start, end, label,
                                allow_short=True, trend_filter=True, use_executor_exit=False,
                                buy_threshold=bt, sell_threshold=st, min_confidence=mc,
                            )
                            agg_results.append(r)
                            if r.sharpe > 0 or r.profitable:
                                print(r.summary_line())

            # ═══════════════════════════════════════════════════════════════
            # FINAL SUMMARY
            # ═══════════════════════════════════════════════════════════════
            all_results = ma_results + cci_results + combo_results + agg_results

            # Group profitable by period
            print(f"\n{'=' * 110}")
            print("  FINAL SUMMARY — Profitable Configs by Period")
            print(f"{'=' * 110}")

            for start, end, period_label in periods:
                profitable = [r for r in all_results if r.period == f"{start} to {end}" and r.profitable]
                if profitable:
                    profitable.sort(key=lambda x: x.sharpe, reverse=True)
                    print(f"\n  --- {period_label} ({len(profitable)} profitable) ---")
                    print(HEADER)
                    print(SEP)
                    for r in profitable[:10]:
                        print(r.summary_line())

            # Cross-period consistency: configs profitable in ALL periods
            print(f"\n{'=' * 110}")
            print("  CROSS-PERIOD CONSISTENCY — Profitable in EVERY period")
            print(f"{'=' * 110}")

            config_labels = set(r.label for r in all_results)
            consistent = []
            for label in config_labels:
                label_results = [r for r in all_results if r.label == label]
                if len(label_results) >= 3 and all(r.return_pct > 0 for r in label_results if r.trades >= 3):
                    avg_sharpe = sum(r.sharpe for r in label_results) / len(label_results)
                    avg_return = sum(r.return_pct for r in label_results) / len(label_results)
                    total_trades = sum(r.trades for r in label_results)
                    consistent.append((label, avg_sharpe, avg_return, total_trades, label_results))

            consistent.sort(key=lambda x: x[1], reverse=True)
            if consistent:
                for label, avg_s, avg_r, tt, results in consistent:
                    print(f"\n  {label} — avg Sharpe={avg_s:+.2f}, avg Return={avg_r:+.2f}%, total trades={tt}")
                    for r in results:
                        print(f"    {r.period}: {r.return_pct:+.2f}% | Sharpe={r.sharpe:+.2f} | {r.trades} trades | WR={r.win_rate:.1f}%")
            else:
                print("  No configs profitable across all periods.")
                # Show configs profitable in at least 4/6 periods
                print(f"\n  Configs profitable in >= 4 of 6 periods:")
                for label in config_labels:
                    label_results = [r for r in all_results if r.label == label and r.trades >= 3]
                    profitable_count = sum(1 for r in label_results if r.return_pct > 0)
                    if profitable_count >= 4 and len(label_results) >= 4:
                        avg_sharpe = sum(r.sharpe for r in label_results) / len(label_results)
                        print(f"    {label}: {profitable_count}/{len(label_results)} profitable, avg Sharpe={avg_sharpe:+.2f}")
                        for r in sorted(label_results, key=lambda x: x.period):
                            status = "+" if r.return_pct > 0 else "-"
                            print(f"      [{status}] {r.period}: {r.return_pct:+.2f}%")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
