#!/usr/bin/env python3
"""
ETHUSDT WFO sweep for sentiment_mean_reversion strategy.
Optimized parameter grid for ETH's higher volatility vs BTC/SOL.

Run on Hetzner: python scripts/eth_wfo_sweep.py
"""

import argparse
import csv
import os
import subprocess
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, "/opt/crypto-agent")

# ── ETH-specific parameter grid ──────────────────────────────────────────────
# ETH moves ~2-3x more than BTC per 1h candle. Default BB threshold of 0.005
# catches too many fakeouts. We test wider bands + adjusted RSI bounds.

PARAM_GRID = [
    # Wider BB distance (ETH needs more room)
    {
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_distance_threshold": 0.008,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_distance_threshold": 0.010,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_distance_threshold": 0.012,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_distance_threshold": 0.015,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    # Tighter RSI (more selective entries)
    {
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "bb_distance_threshold": 0.008,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "bb_distance_threshold": 0.010,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "bb_distance_threshold": 0.012,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "bb_distance_threshold": 0.015,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    # Even tighter RSI
    {
        "rsi_oversold": 25.0,
        "rsi_overbought": 75.0,
        "bb_distance_threshold": 0.010,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 25.0,
        "rsi_overbought": 75.0,
        "bb_distance_threshold": 0.012,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 25.0,
        "rsi_overbought": 75.0,
        "bb_distance_threshold": 0.015,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 25.0,
        "rsi_overbought": 75.0,
        "bb_distance_threshold": 0.018,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    # Higher sentiment gate (more selective, fewer but better signals)
    {
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_distance_threshold": 0.010,
        "sentiment_gate_threshold": 45.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_distance_threshold": 0.012,
        "sentiment_gate_threshold": 45.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "bb_distance_threshold": 0.012,
        "sentiment_gate_threshold": 45.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    {
        "rsi_oversold": 28.0,
        "rsi_overbought": 72.0,
        "bb_distance_threshold": 0.015,
        "sentiment_gate_threshold": 45.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
    # Baseline reference (current agent config)
    {
        "rsi_oversold": 35.0,
        "rsi_overbought": 65.0,
        "bb_distance_threshold": 0.005,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    },
]

# WFO window schedule: 6mo train / 3mo test, shifted every 3 months
WFO_WINDOWS = [
    # (train_start, train_end, test_start, test_end)
    ("2024-01-01", "2024-07-01", "2024-07-01", "2024-10-01"),
    ("2024-04-01", "2024-10-01", "2024-10-01", "2025-01-01"),
    ("2024-07-01", "2025-01-01", "2025-01-01", "2025-04-01"),
    ("2024-10-01", "2025-04-01", "2025-04-01", "2025-07-01"),
    ("2025-01-01", "2025-07-01", "2025-07-01", "2025-10-01"),
    ("2025-04-01", "2025-10-01", "2025-10-01", "2026-01-01"),
    ("2025-07-01", "2026-01-01", "2026-01-01", "2026-04-08"),
]

OUT_PATH = Path("/opt/crypto-agent/docs/reports/eth_wfo_sweep_results.csv")


def run_backtest(symbol, timeframe, start, end, params, config_path):
    """Run a single backtest with given params, return metrics dict or None."""
    # Build a temp config with strategy params overridden
    import yaml

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    # Override strategy config
    strat = cfg.setdefault("strategy", {})
    strat["strategies"] = [
        {
            "name": "sentiment_mean_reversion",
            "config": params,
        }
    ]
    # Write temp config
    tmp_cfg_path = f"/tmp/eth_wfo_cfg_{os.getpid()}.yaml"
    with open(tmp_cfg_path, "w") as f:
        yaml.dump(cfg, f)

    cmd = [
        "python3",
        "scripts/run_backtest.py",
        "--symbol",
        symbol,
        "--timeframe",
        timeframe,
        "--start",
        start,
        "--end",
        end,
        "--config",
        tmp_cfg_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd="/opt/crypto-agent"
        )
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT for params={params}")
        return None
    finally:
        Path(tmp_cfg_path).unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"  FAILED: {result.stderr[:200]}")
        return None

    metrics = {}
    for line in result.stdout.splitlines():
        if "Total Trades:" in line:
            try:
                metrics["trades"] = int(line.split(":")[1].strip())
            except ValueError:
                pass
        elif "Win Rate:" in line:
            try:
                metrics["win_rate"] = float(line.split(":")[1].strip().replace("%", "")) / 100
            except ValueError:
                pass
        elif "Sharpe Ratio:" in line:
            try:
                metrics["sharpe"] = float(line.split(":")[1].strip())
            except ValueError:
                pass
        elif "Profit Factor:" in line:
            try:
                metrics["profit_factor"] = float(line.split(":")[1].strip())
            except ValueError:
                pass
        elif "Total Return:" in line:
            try:
                metrics["total_return_pct"] = float(
                    result.stdout.split("Total Return:")[1].split("(")[1].split("%")[0].strip()
                )
            except (ValueError, IndexError):
                pass

    return metrics


def run():
    parser = argparse.ArgumentParser(description="ETHUSDT WFO sweep for sentiment_mean_reversion")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--config", default="/opt/crypto-agent/config/settings.sentiment_macro.yaml"
    )
    parser.add_argument(
        "--min-oos-trades",
        type=int,
        default=10,
        help="Minimum trades in OOS window to accept result",
    )
    args = parser.parse_args()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    results = []

    total_combos = len(PARAM_GRID)
    print(f"Testing {total_combos} parameter combos across {len(WFO_WINDOWS)} WFO windows")
    print(f"Total backtest runs: {total_combos * len(WFO_WINDOWS)}")
    print()

    for idx, params in enumerate(PARAM_GRID, 1):
        oos_sharpe, oos_wr, oos_trades = [], [], []

        for win_idx, (_train_start, _train_end, test_start, test_end) in enumerate(WFO_WINDOWS, 1):
            print(
                f"  [{idx}/{total_combos}] params={params['bb_distance_threshold']} "
                f"[{win_idx}/{len(WFO_WINDOWS)}] {test_start}→{test_end}",
                flush=True,
            )

            metrics = run_backtest(
                args.symbol,
                args.timeframe,
                test_start,
                test_end,
                params,
                args.config,
            )

            if metrics and metrics.get("trades", 0) >= args.min_oos_trades:
                oos_sharpe.append(metrics.get("sharpe", 0))
                oos_wr.append(metrics.get("win_rate", 0))
                oos_trades.append(metrics["trades"])
                print(
                    f"    → trades={metrics['trades']}, wr={metrics.get('win_rate', 0) * 100:.1f}%, "
                    f"sharpe={metrics.get('sharpe', 0):.2f}"
                )
            else:
                print(
                    f"    → SKIP (trades={metrics.get('trades', 0) if metrics else 0} < {args.min_oos_trades})"
                )

        # Aggregate OOS across windows
        if oos_sharpe:
            row = {
                "rsi_oversold": params["rsi_oversold"],
                "rsi_overbought": params["rsi_overbought"],
                "bb_distance_threshold": params["bb_distance_threshold"],
                "sentiment_gate_threshold": params["sentiment_gate_threshold"],
                "sentiment_panic_threshold": params["sentiment_panic_threshold"],
                "sentiment_boost_threshold": params["sentiment_boost_threshold"],
                "windows_with_trades": len(oos_sharpe),
                "total_oos_trades": sum(oos_trades),
                "mean_oos_sharpe": mean(oos_sharpe),
                "mean_oos_win_rate": mean(oos_wr) * 100,
                "mean_oos_trades": sum(oos_trades) / len(WFO_WINDOWS),
            }
            results.append(row)
            print(
                f"  >> OOS mean sharpe={row['mean_oos_sharpe']:.2f}, win_rate={row['mean_oos_win_rate']:.1f}%, "
                f"trades/window={row['mean_oos_trades']:.0f}"
            )
        else:
            print("  >> NO VALID OOS WINDOWS")

    # Sort by OOS Sharpe descending
    results.sort(key=lambda r: r["mean_oos_sharpe"], reverse=True)

    # Write CSV
    fieldnames = [
        "rsi_oversold",
        "rsi_overbought",
        "bb_distance_threshold",
        "sentiment_gate_threshold",
        "sentiment_panic_threshold",
        "sentiment_boost_threshold",
        "windows_with_trades",
        "total_oos_trades",
        "mean_oos_sharpe",
        "mean_oos_win_rate",
        "mean_oos_trades",
    ]
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nResults written to {OUT_PATH}")
    print("\n=== TOP 5 CONFIGURATIONS (by OOS Sharpe) ===")
    for i, r in enumerate(results[:5], 1):
        print(
            f"  {i}. RSI({r['rsi_oversold']},{r['rsi_overbought']}) "
            f"BB={r['bb_distance_threshold']} SentGate={r['sentiment_gate_threshold']} "
            f"→ sharpe={r['mean_oos_sharpe']:.2f} win_rate={r['mean_oos_win_rate']:.1f}% "
            f"trades={r['total_oos_trades']}"
        )

    if results:
        best = results[0]
        print("\n=== RECOMMENDED CONFIG ===")
        print(f"  rsi_oversold: {best['rsi_oversold']}")
        print(f"  rsi_overbought: {best['rsi_overbought']}")
        print(f"  bb_distance_threshold: {best['bb_distance_threshold']}")
        print(f"  sentiment_gate_threshold: {best['sentiment_gate_threshold']}")
        print(f"  sentiment_panic_threshold: {best['sentiment_panic_threshold']}")
        print(f"  sentiment_boost_threshold: {best['sentiment_boost_threshold']}")
        print(f"\n  OOS Sharpe: {best['mean_oos_sharpe']:.2f}")
        print(f"  OOS Win Rate: {best['mean_oos_win_rate']:.1f}%")
        print(f"  OOS Total Trades: {best['total_oos_trades']}")


if __name__ == "__main__":
    run()
