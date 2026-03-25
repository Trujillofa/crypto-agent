#!/usr/bin/env python3
"""WFO Parameter Sweep."""

import asyncio
import subprocess
from datetime import timedelta

import pandas as pd


def parse_backtest_output(stdout):
    metrics = {}
    for line in stdout.splitlines():
        if "Total Trades:" in line:
            metrics["trades"] = int(line.split(":")[1])
        if "Win Rate:" in line:
            metrics["win_rate"] = float(line.split(":")[1].strip("%")) / 100
        if "Sharpe:" in line:
            metrics["sharpe"] = float(line.split(":")[1])
    return metrics


async def wfo_sweep(symbol, timeframe, start, end, param_grid):
    results = []
    current = pd.to_datetime(start)
    end_dt = pd.to_datetime(end)

    while current + timedelta(days=90) < end_dt:
        train_end = current + timedelta(days=180)
        test_start = train_end
        test_end = test_start + timedelta(days=90)

        if test_end > end_dt:
            break

        for params in param_grid:
            cmd = [
                "python",
                "scripts/run_backtest.py",
                "--symbol",
                symbol,
                "--timeframe",
                timeframe,
                "--start",
                train_end.strftime("%Y-%m-%d"),
                "--end",
                test_end.strftime("%Y-%m-%d"),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            metrics = parse_backtest_output(result.stdout)
            if metrics.get("sharpe", 0) > 0:
                metrics["train"] = current.strftime("%Y-%m")
                metrics["params"] = params
                results.append(metrics)

        current = train_end

    df = pd.DataFrame(results)
    if not df.empty:
        best = df.loc[df["sharpe"].idxmax()]
        print(
            f"Best: buy={best['params']['buy']}, sell={best['params']['sell']}, Sharpe={best['sharpe']:.2f}"
        )
        df.to_csv("wfo_sweep.csv", index=False)
    return df


if __name__ == "__main__":
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    param_grid = [
        {"buy": b, "sell": -s} for b in [1.1, 1.2, 1.3, 1.4] for s in [1.1, 1.2, 1.3, 1.4]
    ]
    asyncio.run(wfo_sweep(symbol, "5m", "2023-01-01", "2024-01-01", param_grid))
