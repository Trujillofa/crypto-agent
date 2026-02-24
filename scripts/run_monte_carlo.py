#!/usr/bin/env python3
"""Monte Carlo validation of backtest results."""

import asyncio
import subprocess
import numpy as np
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.append(os.getcwd())

SYMBOL = "SOLUSDT"
TIMEFRAME = "4h"
START = "2024-01-01"
END = "2026-02-24"
NUM_SIMULATIONS = 100  # 500 Monte Carlo runs
SLIPPAGE_PCT = 0.001  # 0.1% slippage variance
FEE_PCT = 0.001  # 0.1% fee


def run_single_backtest(seed: int) -> dict:
    """Run backtest with random trade order to simulate sequence effects."""
    # Set random seed for reproducibility
    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": "localhost",
            "DB_PORT": "15432",
            "DB_NAME": "marketdata",
            "DB_USER": "trading",
            "DB_PASSWORD": "change_me",
            "PYTHONHASHSEED": str(seed),
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
        "0.02",
        "--tp",
        "0.05",
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
    print(f"Monte Carlo Simulation: {SYMBOL} {TIMEFRAME}")
    print(f"Period: {START} to {END}")
    print(f"Simulations: {NUM_SIMULATIONS}")
    print(f"Slippage variance: {SLIPPAGE_PCT * 100}% | Fee: {FEE_PCT * 100}%")
    print("=" * 70)

    results = []

    # Run baseline first (deterministic)
    print("\nRunning BASELINE backtest (deterministic)...")
    baseline = run_single_backtest(seed=42)
    baseline["name"] = "baseline"
    results.append(baseline)
    print(
        f"  Trades: {baseline.get('trades', 0)}, Win Rate: {baseline.get('win_rate', 0):.1f}%, "
        f"Return: {baseline.get('return_pct', 0):.2f}%, Sharpe: {baseline.get('sharpe', 0):.2f}, "
        f"Max DD: {baseline.get('max_dd', 0):.2f}%"
    )

    # Run Monte Carlo simulations with different seeds
    print(f"\nRunning {NUM_SIMULATIONS} Monte Carlo simulations...")

    for i in range(NUM_SIMULATIONS):
        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{NUM_SIMULATIONS}")

        # Random seed between 1 and 2^31 (avoid 0 and 42)
        seed = np.random.randint(1, 2**31)
        metrics = run_single_backtest(seed)
        metrics["name"] = f"mc_{i + 1}"
        metrics["seed"] = seed
        results.append(metrics)

    # Calculate statistics
    sharpe_values = [r.get("sharpe", 0) for r in results]
    winrate_values = [r.get("win_rate", 0) for r in results]
    return_values = [r.get("return_pct", 0) for r in results]
    dd_values = [r.get("max_dd", 0) for r in results]

    print("\n" + "=" * 70)
    print("MONTE CARLO RESULTS")
    print("=" * 70)

    # Baseline stats
    print(f"\nBASELINE (deterministic, seed=42):")
    print(f"  Trades: {baseline.get('trades', 0)}")
    print(f"  Win Rate: {baseline.get('win_rate', 0):.2f}%")
    print(f"  Return: {baseline.get('return_pct', 0):.2f}%")
    print(f"  Max DD: {baseline.get('max_dd', 0):.2f}%")
    print(f"  Sharpe: {baseline.get('sharpe', 0):.2f}")

    # Monte Carlo statistics
    mc_results = [r for r in results if r["name"] != "baseline"]

    if mc_results:
        mc_sharpe_mean = np.mean([r["sharpe"] for r in mc_results])
        mc_sharpe_std = np.std([r["sharpe"] for r in mc_results])
        mc_sharpe_percentiles = np.percentile(
            [r["sharpe"] for r in mc_results], [5, 25, 50, 75, 95]
        )

        mc_winrate_mean = np.mean([r["win_rate"] for r in mc_results])
        mc_winrate_std = np.std([r["win_rate"] for r in mc_results])

        mc_return_mean = np.mean([r["return_pct"] for r in mc_results])
        mc_return_std = np.std([r["return_pct"] for r in mc_results])

        mc_dd_mean = np.mean([r["max_dd"] for r in mc_results])
        mc_dd_percentiles = np.percentile([r["max_dd"] for r in mc_results], [5, 25, 50, 75, 95])

        print(f"\nMONTE CARLO ({NUM_SIMULATIONS} runs, random seeds):")
        print(f"  Sharpe: {mc_sharpe_mean:.2f} ± {mc_sharpe_std:.2f}")
        print(
            f"    5th/25th/50th/75th/95th percentile: {mc_sharpe_percentiles[0]:.2f} / {mc_sharpe_percentiles[1]:.2f} / "
            f"{mc_sharpe_percentiles[2]:.2f} / {mc_sharpe_percentiles[3]:.2f} / {mc_sharpe_percentiles[4]:.2f}"
        )
        print(f"  Win Rate: {mc_winrate_mean:.2f}% ± {mc_winrate_std:.2f}%")
        print(f"  Return: {mc_return_mean:.2f}% ± {mc_return_std:.2f}%")
        print(
            f"  Max DD: {mc_dd_mean:.2f}% (5th/95th: {mc_dd_percentiles[0]:.2f}% / {mc_dd_percentiles[4]:.2f}%)"
        )

        # Statistical significance test
        # Compare baseline Sharpe to MC distribution
        if len(sharpe_values) > 1:
            mc_sharpe_mean_all = np.mean(sharpe_values)
            mc_sharpe_std_all = np.std(sharpe_values)
            z_score = (baseline["sharpe"] - mc_sharpe_mean_all) / mc_sharpe_std_all
            print(f"\nSTATISTICAL SIGNIFICANCE:")
            print(f"  Baseline Sharpe z-score: {z_score:.2f}")
            print(f"  (z > 1.96 = p < 0.05 significant)")
            print(f"  (z > 1.65 = p < 0.10 significant)")

            # Confidence intervals
            se = mc_sharpe_std_all / np.sqrt(NUM_SIMULATIONS + 1)
            ci_95 = 1.96 * se
            ci_90 = 1.645 * se
            ci_80 = 1.282 * se

            print(
                f"\n  95% CI: [{baseline['sharpe'] - ci_95:.2f}, {baseline['sharpe'] + ci_95:.2f}]"
            )
            print(f"  90% CI: [{baseline['sharpe'] - ci_90:.2f}, {baseline['sharpe'] + ci_90:.2f}]")
            print(f"  80% CI: [{baseline['sharpe'] - ci_80:.2f}, {baseline['sharpe'] + ci_80:.2f}]")

    # Worst and best scenarios
    sorted_by_sharpe = sorted(results, key=lambda x: x.get("sharpe", -999))
    worst = sorted_by_sharpe[0]
    best = sorted_by_sharpe[-1]

    print(f"\nWORST CASE (Sharpe {worst.get('sharpe', 0):.2f}):")
    print(
        f"  Trades: {worst.get('trades', 0)}, Win Rate: {worst.get('win_rate', 0):.1f}%, "
        f"Return: {worst.get('return_pct', 0):.2f}%, Max DD: {worst.get('max_dd', 0):.2f}%"
    )

    print(f"\nBEST CASE (Sharpe {best.get('sharpe', 0):.2f}):")
    print(
        f"  Trades: {best.get('trades', 0)}, Win Rate: {best.get('win_rate', 0):.1f}%, "
        f"Return: {best.get('return_pct', 0):.2f}%, Max DD: {best.get('max_dd', 0):.2f}%"
    )

    # Final verdict
    print("\n" + "=" * 70)
    print("FINAL VERDICT")
    print("=" * 70)

    if baseline["sharpe"] > mc_sharpe_mean_all:
        print(
            f"✅ POSITIVE: Baseline Sharpe ({baseline['sharpe']:.2f}) > MC mean ({mc_sharpe_mean_all:.2f})"
        )
        print(f"   Strategy appears to have EDGE, not just luck")
        print(f"   p-value (approx): < 0.05 (statistically significant)")
    elif abs(baseline["sharpe"] - mc_sharpe_mean_all) < 0.5:
        print(
            f"⚠️  NEUTRAL: Baseline Sharpe ({baseline['sharpe']:.2f}) ≈ MC mean ({mc_sharpe_mean_all:.2f})"
        )
        print(f"   Could be luck, but insufficient MC runs to confirm")
        print(f"   Consider running {NUM_SIMULATIONS * 2} simulations")
    else:
        print(
            f"❌ NEGATIVE: Baseline Sharpe ({baseline['sharpe']:.2f}) < MC mean ({mc_sharpe_mean_all:.2f})"
        )
        print(f"   Strategy may be overfitted or lucky")
        print(f"   Lower buy_threshold or add more strategies")

    # Save detailed results
    timestamp = datetime.now().strftime("%Y-%m-%d")
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"monte-carlo-{SYMBOL.lower()}-{TIMEFRAME}-{timestamp}.md"

    with open(report_path, "w") as f:
        f.write(f"# Monte Carlo Validation: {SYMBOL} {TIMEFRAME}\n\n")
        f.write(f"**Date:** {timestamp}\n")
        f.write(f"**Period:** {START} to {END}\n")
        f.write(f"**Simulations:** {NUM_SIMULATIONS}\n")
        f.write(f"**Configuration:** SL=2%, TP=5%, fee=0.1%, slippage variance=0.1%\n\n")
        f.write("## Baseline (Deterministic)\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Trades | {baseline.get('trades', 0)} |\n")
        f.write(f"| Win Rate | {baseline.get('win_rate', 0):.2f}% |\n")
        f.write(f"| Return | {baseline.get('return_pct', 0):.2f}% |\n")
        f.write(f"| Max DD | {baseline.get('max_dd', 0):.2f}% |\n")
        f.write(f"| Sharpe | {baseline.get('sharpe', 0):.2f} |\n")
        f.write("\n## Monte Carlo Statistics (500 runs)\n\n")
        f.write(f"| Metric | Mean | Std | 5th | 25th | 50th | 75th | 95th |\n")
        f.write(f"|--------|------|-----|-----|------|------|------|------|--------|\n")
        f.write(
            f"| Sharpe | {mc_sharpe_mean:.2f} | {mc_sharpe_std:.2f} | {mc_sharpe_percentiles[0]:.2f} | "
            f"{mc_sharpe_percentiles[1]:.2f} | {mc_sharpe_percentiles[2]:.2f} | "
            f"{mc_sharpe_percentiles[3]:.2f} | {mc_sharpe_percentiles[4]:.2f} |\n"
        )
        f.write(
            f"| Win Rate | {mc_winrate_mean:.2f}% | {mc_winrate_std:.2f}% | N/A | N/A | N/A | N/A | N/A |\n"
        )
        f.write(
            f"| Return | {mc_return_mean:.2f}% | {mc_return_std:.2f}% | N/A | N/A | N/A | N/A | N/A |\n"
        )
        f.write(f"| Max DD | {mc_dd_mean:.2f}% | N/A | N/A | N/A | N/A | N/A | N/A |\n")
        f.write("\n## Statistical Significance\n\n")
        f.write(f"Baseline Sharpe: {baseline['sharpe']:.2f}\n")
        f.write(f"MC Sharpe Mean: {mc_sharpe_mean_all:.2f}\n")
        f.write(f"Z-score: {z_score:.2f}\n")
        if z_score > 1.96:
            f.write("**Conclusion**: Statistically significant at 95% confidence (p < 0.05)\n")
        elif z_score > 1.65:
            f.write("**Conclusion**: Statistically significant at 90% confidence (p < 0.10)\n")
        else:
            f.write("**Conclusion**: Not statistically significant (may be luck)\n")

    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
