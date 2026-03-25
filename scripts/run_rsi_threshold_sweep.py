#!/usr/bin/env python3
"""Backtest sweep for RSI threshold comparison."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# Add project root to path
sys.path.append(os.getcwd())

SYMBOL = "SOLUSDT"
TIMEFRAME = "4h"
START = "2024-01-01"
END = "2026-02-24"
SL = 0.02
TP = 0.05

# Threshold combinations to test
THRESHOLDS = [
    ("baseline_30_70", 30, 70),
    ("conservative_25_75", 25, 75),
    ("aggressive_35_65", 35, 65),
    ("very_conservative_20_80", 20, 80),
    ("very_aggressive_40_60", 40, 60),
]


def modify_config(oversold: float, overbought: float) -> Path:
    """Create a temporary config with modified RSI thresholds."""
    config_path = Path("config/settings.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Find and modify rsi_reversal config
    for strategy in config.get("strategy", {}).get("strategies", []):
        if strategy.get("name") == "rsi_reversal":
            strategy["config"]["oversold_threshold"] = oversold
            strategy["config"]["overbought_threshold"] = overbought

    # Write to temp file
    temp_path = Path(tempfile.mktemp(suffix=".yaml"))
    with open(temp_path, "w") as f:
        yaml.dump(config, f)

    return temp_path


def run_backtest(config_path: Path) -> dict:
    """Run backtest and parse results."""
    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": "localhost",
            "DB_PORT": "15432",
            "DB_NAME": "marketdata",
            "DB_USER": "trading",
            "DB_PASSWORD": "change_me",
        }
    )

    cmd = [
        ".venv/bin/python",
        "scripts/run_backtest.py",
        "--symbol",
        SYMBOL,
        "--timeframe",
        TIMEFRAME,
        "--start",
        START,
        "--end",
        END,
        "--sl",
        str(SL),
        "--tp",
        str(TP),
        "--config",
        str(config_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

    metrics = {}
    for line in result.stdout.splitlines():
        if "Total Trades:" in line:
            metrics["trades"] = int(line.split(":")[1].strip())
        elif "Win Rate:" in line:
            metrics["win_rate"] = float(line.split(":")[1].strip().replace("%", ""))
        elif "Total Return:" in line:
            parts = line.split("(")
            if len(parts) > 1:
                metrics["return_pct"] = float(parts[1].split("%")[0])
        elif "Max Drawdown:" in line:
            metrics["max_dd"] = float(line.split(":")[1].strip().replace("%", ""))
        elif "Sharpe Ratio:" in line:
            metrics["sharpe"] = float(line.split(":")[1].strip())

    return metrics


def main():
    print(f"RSI Threshold Sweep: {SYMBOL} {TIMEFRAME}")
    print(f"Period: {START} to {END}")
    print(f"SL={SL * 100}%, TP={TP * 100}%")
    print("=" * 70)

    results = []

    for name, oversold, overbought in THRESHOLDS:
        print(f"\nTesting {name}: oversold={oversold}, overbought={overbought}")

        # Create temp config
        temp_config = modify_config(oversold, overbought)

        try:
            metrics = run_backtest(temp_config)
            metrics["name"] = name
            metrics["oversold"] = oversold
            metrics["overbought"] = overbought
            results.append(metrics)

            print(
                f"  Trades: {metrics.get('trades', 0)}, "
                f"Win Rate: {metrics.get('win_rate', 0):.1f}%, "
                f"Return: {metrics.get('return_pct', 0):.2f}%, "
                f"Sharpe: {metrics.get('sharpe', 0):.2f}"
            )
        except Exception as e:
            print(f"  ERROR: {e}")
        finally:
            # Cleanup temp config
            if temp_config.exists():
                temp_config.unlink()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Config':<25} {'Trades':>7} {'Win%':>7} {'Return%':>10} {'Sharpe':>8}")
    print("-" * 70)

    for r in sorted(results, key=lambda x: x.get("sharpe", 0), reverse=True):
        print(
            f"{r['name']:<25} {r.get('trades', 0):>7} {r.get('win_rate', 0):>7.1f} "
            f"{r.get('return_pct', 0):>10.2f} {r.get('sharpe', 0):>8.2f}"
        )

    # Find best
    if results:
        best = max(results, key=lambda x: x.get("sharpe", 0))
        baseline = next((r for r in results if "baseline" in r["name"]), None)

        print("\n" + "=" * 70)
        if baseline and best["name"] != baseline["name"]:
            delta_sharpe = best.get("sharpe", 0) - baseline.get("sharpe", 0)
            delta_winrate = best.get("win_rate", 0) - baseline.get("win_rate", 0)
            print(
                f"BEST: {best['name']} (Sharpe Δ {delta_sharpe:+.2f}, Win Rate Δ {delta_winrate:+.1f}%)"
            )
        else:
            print(f"BASELINE is already optimal (Sharpe {baseline.get('sharpe', 0):.2f})")


if __name__ == "__main__":
    main()
