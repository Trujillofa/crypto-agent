#!/usr/bin/env python3
"""Walk-Forward Optimization runner."""

import asyncio
import subprocess
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path


def run_backtest(symbol, timeframe, start, end):
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
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"Backtest failed: {result.stderr}")
        return None
    lines = result.stdout.splitlines()
    metrics = {}
    for line in lines:
        if "Total Trades:" in line:
            metrics["trades"] = int(line.split(":")[1])
        elif "Win Rate:" in line:
            metrics["win_rate"] = float(line.split(":")[1].strip("%")) / 100
        elif "Sharpe:" in line:
            metrics["sharpe"] = float(line.split(":")[1])
    return metrics


async def wfo(symbol, timeframe, start, end, train_months=6, test_months=3):
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    results = []

    current = start_dt
    while current + timedelta(days=test_months * 30 + 1) < end_dt:
        train_end = current + timedelta(days=train_months * 30)
        test_start = train_end
        test_end = test_start + timedelta(days=test_months * 30)

        if test_end > end_dt:
            break

        train_str = train_end.strftime("%Y-%m-%d")
        test_str = test_end.strftime("%Y-%m-%d")

        print(
            f"Train: {current.strftime('%Y-%m')} - {train_str} | Test: {train_end.strftime('%Y-%m')} - {test_str}"
        )

        metrics = run_backtest(symbol, timeframe, train_str, test_str)
        if metrics:
            metrics["train_period"] = (current.strftime("%Y-%m"), train_str)
            metrics["test_period"] = (train_end.strftime("%Y-%m"), test_str)
            results.append(metrics)

        current = train_end

    df = pd.DataFrame(results)
    if not df.empty:
        print(f"OOS Mean Sharpe: {df['sharpe'].mean():.2f}")
        print(f"OOS Win Rate: {df['win_rate'].mean() * 100:.1f}%")
        Path("wfo_results.csv").write_text(df.to_csv(index=False))

    return df


if __name__ == "__main__":
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    asyncio.run(wfo(symbol, "5m", "2023-01-01", "2024-01-01"))
