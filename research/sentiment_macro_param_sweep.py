#!/usr/bin/env python3
"""Sentiment-Macro parameter sweep — compare current vs improved configurations.

Sweeps:
  1. SL ATR multiplier: 1.5 (current) vs 2.0 (improved) vs 2.5
  2. Volatility regime filter: off vs on at various atr_pct thresholds
  3. RSI oversold depth: 30 vs 35 (current) vs 40

Runs on BTCUSDT, ETHUSDT, SOLUSDT across the last 6 months.
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.sentiment_mean_reversion import SentimentMeanReversionStrategy
from src.utils.logger import configure_logger


@dataclass
class SweepResult:
    label: str
    symbol: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    return_pct: float
    max_drawdown: float
    sharpe: float
    profit_factor: float
    avg_win: float = 0.0
    avg_loss: float = 0.0

    def summary_line(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor < 100 else "inf"
        flag = " ***" if self.return_pct > 0 and self.trades >= 10 else ""
        return (
            f"{self.label:<55} | {self.symbol:>10} | {self.trades:>4} | "
            f"{self.wins:>3}/{self.losses:>3} | {self.win_rate:>5.1f}% | "
            f"{self.return_pct:>+8.2f}% | {self.max_drawdown:>5.1f}% | "
            f"{self.sharpe:>+6.2f} | {pf:>5}{flag}"
        )


HEADER = (
    f"{'Config':<55} | {'Symbol':>10} | {'#':>4} | "
    f"{'W':>3}/{'L':>3} | {'Win%':>5}  | {'Return%':>8} | "
    f"{'MDD%':>5}  | {'Sharpe':>6} | {'PF':>5}"
)
SEP = "-" * 125

# ── Parameter grid ──

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "1h"

# Date ranges
END_DATE = datetime.utcnow().strftime("%Y-%m-%d")
START_DATE = (datetime.utcnow() - timedelta(days=180)).strftime("%Y-%m-%d")

# SL/TP combos
SL_TP_COMBOS = [
    (1.5, 3.5, "SL1.5/TP3.5 (current)"),
    (2.0, 3.5, "SL2.0/TP3.5 (improved)"),
    (2.0, 4.5, "SL2.0/TP4.5"),
    (2.5, 4.5, "SL2.5/TP4.5"),
]

# Volatility regime filter configs
VOL_FILTER_CONFIGS = [
    {"volatility_regime_filter": False, "atr_pct_threshold": 0.006, "label": "no-vol-filter"},
    {"volatility_regime_filter": True, "atr_pct_threshold": 0.005, "label": "vol>0.5%"},
    {"volatility_regime_filter": True, "atr_pct_threshold": 0.006, "label": "vol>0.6%"},
    {"volatility_regime_filter": True, "atr_pct_threshold": 0.007, "label": "vol>0.7%"},
    {"volatility_regime_filter": True, "atr_pct_threshold": 0.008, "label": "vol>0.8%"},
]

# RSI thresholds
RSI_CONFIGS = [
    {"rsi_oversold": 30.0, "label": "RSI30"},
    {"rsi_oversold": 35.0, "label": "RSI35 (current)"},
    {"rsi_oversold": 40.0, "label": "RSI40"},
]


async def run_single(
    reader: IndicatorReader,
    symbol: str,
    strategy_config: dict,
    sl_atr: float,
    tp_atr: float,
    label: str,
) -> SweepResult:
    config = BacktestConfig(
        symbol=symbol,
        timeframe=TIMEFRAME,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10000.0,
        fee_rate=0.001,
        sl_atr_multiplier=sl_atr,
        tp_atr_multiplier=tp_atr,
        use_executor_exit_model=True,
        time_stop_minutes=1440,
        apply_global_trend_filter=False,
        allow_short=False,
        strategy_classes=[SentimentMeanReversionStrategy],
        strategy_configs=[strategy_config],
        aggregator_config={
            "buy_threshold": 0.6,
            "sell_threshold": -0.6,
            "min_agreement": 1,
            "min_confidence": 0.0,
        },
    )

    engine = BacktestEngine(config, reader)
    r = await engine.run()

    wins = len([t for t in r.trades if t.pnl > 0])
    losses = len([t for t in r.trades if t.pnl <= 0])
    avg_win = sum(t.pnl for t in r.trades if t.pnl > 0) / max(wins, 1)
    avg_loss = sum(t.pnl for t in r.trades if t.pnl <= 0) / max(losses, 1)

    return SweepResult(
        label=label,
        symbol=symbol,
        trades=r.total_trades,
        wins=wins,
        losses=losses,
        win_rate=r.win_rate,
        return_pct=r.total_return_pct,
        max_drawdown=r.max_drawdown * 100,
        sharpe=r.sharpe_ratio,
        profit_factor=r.profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )


async def main():
    configure_logger("WARNING")

    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "name": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", ""),
    }

    await init_pool(db_config)

    try:
        reader = IndicatorReader(db_config)
        async with reader:
            all_results: list[SweepResult] = []

            # ── SWEEP 1: SL/TP comparison (baseline config, no vol filter) ──
            print(f"\n{'=' * 125}")
            print(f"  SWEEP 1: SL/TP Multiplier Comparison — {START_DATE} to {END_DATE}")
            print(f"{'=' * 125}")
            print(HEADER)
            print(SEP)

            base_config = {
                "rsi_oversold": 35.0,
                "rsi_overbought": 65.0,
                "bb_distance_threshold": 0.005,
                "sentiment_gate_threshold": 35.0,
                "sentiment_panic_threshold": 20.0,
                "sentiment_boost_threshold": 65.0,
                "volatility_regime_filter": False,
            }

            for sl, tp, sl_label in SL_TP_COMBOS:
                for symbol in SYMBOLS:
                    r = await run_single(reader, symbol, base_config, sl, tp, sl_label)
                    all_results.append(r)
                    print(r.summary_line())

            # ── SWEEP 2: Volatility regime filter (improved SL=2.0) ──
            print(f"\n{'=' * 125}")
            print("  SWEEP 2: Volatility Regime Filter — SL=2.0/TP=3.5")
            print(f"{'=' * 125}")
            print(HEADER)
            print(SEP)

            for vol_cfg in VOL_FILTER_CONFIGS:
                for symbol in SYMBOLS:
                    cfg = {**base_config, **vol_cfg}
                    label = f"SL2.0/TP3.5 + {vol_cfg['label']}"
                    r = await run_single(reader, symbol, cfg, 2.0, 3.5, label)
                    all_results.append(r)
                    print(r.summary_line())

            # ── SWEEP 3: RSI oversold depth (with vol filter + improved SL) ──
            print(f"\n{'=' * 125}")
            print("  SWEEP 3: RSI Oversold Depth — SL=2.0/TP3.5 + vol>0.6%")
            print(f"{'=' * 125}")
            print(HEADER)
            print(SEP)

            for rsi_cfg in RSI_CONFIGS:
                for symbol in SYMBOLS:
                    cfg = {
                        **base_config,
                        "volatility_regime_filter": True,
                        "atr_pct_threshold": 0.006,
                        "rsi_oversold": rsi_cfg["rsi_oversold"],
                    }
                    label = f"SL2.0/TP3.5 vol>0.6% {rsi_cfg['label']}"
                    r = await run_single(reader, symbol, cfg, 2.0, 3.5, label)
                    all_results.append(r)
                    print(r.summary_line())

            # ── SUMMARY: Top results ──
            print(f"\n{'=' * 125}")
            print("  TOP 20 BY SHARPE (min 5 trades)")
            print(f"{'=' * 125}")
            print(HEADER)
            print(SEP)
            viable = [r for r in all_results if r.trades >= 5]
            for r in sorted(viable, key=lambda x: x.sharpe, reverse=True)[:20]:
                print(r.summary_line())

            print(f"\n{'=' * 125}")
            print("  TOP 20 BY RETURN (min 5 trades)")
            print(f"{'=' * 125}")
            print(HEADER)
            print(SEP)
            for r in sorted(viable, key=lambda x: x.return_pct, reverse=True)[:20]:
                print(r.summary_line())

            # ── PROFITABLE CONFIGS ──
            profitable = [r for r in all_results if r.return_pct > 0 and r.trades >= 5]
            if profitable:
                print(f"\n{'=' * 125}")
                print(f"  ALL PROFITABLE CONFIGS ({len(profitable)})")
                print(f"{'=' * 125}")
                print(HEADER)
                print(SEP)
                for r in sorted(profitable, key=lambda x: x.sharpe, reverse=True):
                    print(r.summary_line())
            else:
                print("\n  ⚠ No profitable configurations found.")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
