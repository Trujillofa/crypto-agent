#!/usr/bin/env python3
"""Walk-Forward Optimization comparison: Long-Only vs Long+Short."""

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.append(os.getcwd())


def run_backtest(symbol, timeframe, start, end, allow_short=False, sl=0.02, tp=0.05):
    """Run a single backtest and return metrics."""
    cmd = [
        "python",
        "scripts/run_backtest.py",
        "--symbol",
        symbol,
        "--timeframe",
        timeframe,
        "--start",
        start,
        "--end",
        end,
        "--sl",
        str(sl),
        "--tp",
        str(tp),
    ]
    if allow_short:
        cmd.append("--allow-short")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"Backtest failed: {result.stderr}")
        return None

    lines = result.stdout.splitlines()
    metrics = {}
    for line in lines:
        if "Total Trades:" in line:
            metrics["trades"] = int(line.split(":")[1].strip())
        elif "Win Rate:" in line:
            metrics["win_rate"] = float(line.split(":")[1].strip().replace("%", ""))
        elif "Sharpe Ratio:" in line:
            metrics["sharpe"] = float(line.split(":")[1].strip())
        elif "Total Return:" in line:
            # Parse "$123.45 (12.34%)"
            parts = line.split("(")
            if len(parts) > 1:
                metrics["return_pct"] = float(parts[1].split("%")[0])
        elif "Max Drawdown:" in line:
            metrics["max_dd"] = float(line.split(":")[1].strip().replace("%", ""))
    return metrics


def wfo_comparison(symbol, timeframe, start, end, train_months=6, test_months=3, sl=0.02, tp=0.05):
    """Run WFO comparing long-only vs long+short."""
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    results_long = []
    results_short = []

    current = start_dt
    window = 0

    while current + timedelta(days=test_months * 30 + 1) < end_dt:
        train_end = current + timedelta(days=train_months * 30)
        test_start = train_end
        test_end = test_start + timedelta(days=test_months * 30)

        if test_end > end_dt:
            break

        train_str = train_end.strftime("%Y-%m-%d")
        test_str = test_end.strftime("%Y-%m-%d")

        window += 1
        print(f"\n{'=' * 60}")
        print(f"Window {window}: Train to {train_str} | Test to {test_str}")
        print(f"{'=' * 60}")

        # Long-only test
        print(f"\n[LONG-ONLY] Testing {test_start.strftime('%Y-%m-%d')} to {test_str}...")
        metrics_long = run_backtest(
            symbol,
            timeframe,
            test_start.strftime("%Y-%m-%d"),
            test_str,
            allow_short=False,
            sl=sl,
            tp=tp,
        )
        if metrics_long:
            metrics_long["window"] = window
            metrics_long["test_period"] = f"{test_start.strftime('%Y-%m')}-{test_str}"
            results_long.append(metrics_long)
            print(
                f"  Trades: {metrics_long.get('trades', 0)}, Win Rate: {metrics_long.get('win_rate', 0):.1f}%, "
                f"Sharpe: {metrics_long.get('sharpe', 0):.2f}, Return: {metrics_long.get('return_pct', 0):.2f}%"
            )

        # Long+Short test
        print(f"\n[LONG+SHORT] Testing {test_start.strftime('%Y-%m-%d')} to {test_str}...")
        metrics_short = run_backtest(
            symbol,
            timeframe,
            test_start.strftime("%Y-%m-%d"),
            test_str,
            allow_short=True,
            sl=sl,
            tp=tp,
        )
        if metrics_short:
            metrics_short["window"] = window
            metrics_short["test_period"] = f"{test_start.strftime('%Y-%m')}-{test_str}"
            results_short.append(metrics_short)
            print(
                f"  Trades: {metrics_short.get('trades', 0)}, Win Rate: {metrics_short.get('win_rate', 0):.1f}%, "
                f"Sharpe: {metrics_short.get('sharpe', 0):.2f}, Return: {metrics_short.get('return_pct', 0):.2f}%"
            )

        current = train_end

    # Summary
    print(f"\n{'=' * 60}")
    print("WFO COMPARISON SUMMARY")
    print(f"{'=' * 60}")

    df_long = pd.DataFrame(results_long)
    df_short = pd.DataFrame(results_short)

    if not df_long.empty:
        print(f"\n[LONG-ONLY] OOS Results ({len(df_long)} windows):")
        print(f"  Mean Win Rate: {df_long['win_rate'].mean():.1f}%")
        print(f"  Mean Sharpe:   {df_long['sharpe'].mean():.2f}")
        print(f"  Mean Return:   {df_long['return_pct'].mean():.2f}%")
        print(f"  Mean Max DD:   {df_long['max_dd'].mean():.2f}%")
        print(f"  Total Trades:  {df_long['trades'].sum()}")

    if not df_short.empty:
        print(f"\n[LONG+SHORT] OOS Results ({len(df_short)} windows):")
        print(f"  Mean Win Rate: {df_short['win_rate'].mean():.1f}%")
        print(f"  Mean Sharpe:   {df_short['sharpe'].mean():.2f}")
        print(f"  Mean Return:   {df_short['return_pct'].mean():.2f}%")
        print(f"  Mean Max DD:   {df_short['max_dd'].mean():.2f}%")
        print(f"  Total Trades:  {df_short['trades'].sum()}")

    if not df_long.empty and not df_short.empty:
        print("\n[DELTA] (Short - Long):")
        print(f"  Win Rate Δ:    {df_short['win_rate'].mean() - df_long['win_rate'].mean():+.1f}%")
        print(f"  Sharpe Δ:      {df_short['sharpe'].mean() - df_long['sharpe'].mean():+.2f}")
        print(
            f"  Return Δ:      {df_short['return_pct'].mean() - df_long['return_pct'].mean():+.2f}%"
        )
        print(f"  Max DD Δ:      {df_short['max_dd'].mean() - df_long['max_dd'].mean():+.2f}%")

        # Recommendation
        sharpe_improvement = df_short["sharpe"].mean() > df_long["sharpe"].mean()
        winrate_improvement = df_short["win_rate"].mean() > df_long["win_rate"].mean()

        if sharpe_improvement and winrate_improvement:
            print("\n✅ RECOMMENDATION: Enable shorts — both Sharpe and Win Rate improved")
        elif sharpe_improvement:
            print(
                "\n⚠️  RECOMMENDATION: Shorts improve Sharpe but lower Win Rate — evaluate risk tolerance"
            )
        else:
            print("\n❌ RECOMMENDATION: Keep long-only — shorts degrade performance")

    # Save results
    timestamp = datetime.now().strftime("%Y-%m-%d")
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / f"wfo-short-comparison-{timestamp}.md"
    with open(report_path, "w") as f:
        f.write(f"# WFO Short Comparison Report: {symbol} {timeframe}\n\n")
        f.write(f"**Date:** {timestamp}\n")
        f.write(f"**Period:** {start} to {end}\n")
        f.write(
            f"**Config:** SL={sl * 100}%, TP={tp * 100}%, train={train_months}mo, test={test_months}mo\n\n"
        )

        f.write("## Long-Only Results\n\n")
        if not df_long.empty:
            f.write("| Window | Test Period | Trades | Win Rate | Sharpe | Return | Max DD |\n")
            f.write("|--------|-------------|--------|----------|--------|--------|--------|\n")
            for _, row in df_long.iterrows():
                f.write(
                    f"| {row['window']} | {row['test_period']} | {row.get('trades', 0)} | "
                    f"{row.get('win_rate', 0):.1f}% | {row.get('sharpe', 0):.2f} | "
                    f"{row.get('return_pct', 0):.2f}% | {row.get('max_dd', 0):.2f}% |\n"
                )
            f.write(
                f"\n**Mean:** Win Rate {df_long['win_rate'].mean():.1f}%, "
                f"Sharpe {df_long['sharpe'].mean():.2f}, Return {df_long['return_pct'].mean():.2f}%\n"
            )

        f.write("\n## Long+Short Results\n\n")
        if not df_short.empty:
            f.write("| Window | Test Period | Trades | Win Rate | Sharpe | Return | Max DD |\n")
            f.write("|--------|-------------|--------|----------|--------|--------|--------|\n")
            for _, row in df_short.iterrows():
                f.write(
                    f"| {row['window']} | {row['test_period']} | {row.get('trades', 0)} | "
                    f"{row.get('win_rate', 0):.1f}% | {row.get('sharpe', 0):.2f} | "
                    f"{row.get('return_pct', 0):.2f}% | {row.get('max_dd', 0):.2f}% |\n"
                )
            f.write(
                f"\n**Mean:** Win Rate {df_short['win_rate'].mean():.1f}%, "
                f"Sharpe {df_short['sharpe'].mean():.2f}, Return {df_short['return_pct'].mean():.2f}%\n"
            )

        if not df_long.empty and not df_short.empty:
            f.write("\n## Delta (Short - Long)\n\n")
            f.write(
                f"- Win Rate: {df_short['win_rate'].mean() - df_long['win_rate'].mean():+.1f}%\n"
            )
            f.write(f"- Sharpe: {df_short['sharpe'].mean() - df_long['sharpe'].mean():+.2f}\n")
            f.write(
                f"- Return: {df_short['return_pct'].mean() - df_long['return_pct'].mean():+.2f}%\n"
            )
            f.write(f"- Max DD: {df_short['max_dd'].mean() - df_long['max_dd'].mean():+.2f}%\n")

    print(f"\nReport saved to: {report_path}")

    return df_long, df_short


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WFO comparison: Long-Only vs Long+Short")
    parser.add_argument("--symbol", type=str, default="SOLUSDT", help="Trading pair")
    parser.add_argument("--timeframe", type=str, default="4h", help="Timeframe")
    parser.add_argument("--start", type=str, default="2024-01-01", help="Start date")
    parser.add_argument("--end", type=str, default="2026-02-24", help="End date")
    parser.add_argument("--sl", type=float, default=0.02, help="Stop loss (e.g. 0.02 for 2%)")
    parser.add_argument("--tp", type=float, default=0.05, help="Take profit (e.g. 0.05 for 5%)")

    args = parser.parse_args()

    wfo_comparison(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        sl=args.sl,
        tp=args.tp,
    )
