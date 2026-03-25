#!/usr/bin/env python3
"""Walk-Forward Optimization runner."""

import argparse
import asyncio
import csv
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean


def run_backtest(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    config_path: str,
) -> dict[str, float] | None:
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
        "--config",
        config_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"Backtest failed: {result.stderr}")
        return None
    lines = result.stdout.splitlines()
    metrics: dict[str, float] = {}
    for line in lines:
        if "Total Trades:" in line:
            metrics["trades"] = float(int(line.split(":")[1]))
        elif "Win Rate:" in line:
            metrics["win_rate"] = float(line.split(":")[1].strip("%")) / 100.0
        elif "Sharpe:" in line:
            metrics["sharpe"] = float(line.split(":")[1])
    return metrics


async def wfo(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    train_months: int = 6,
    test_months: int = 3,
    output_path: str = "wfo_results.csv",
    config_path: str = "config/settings.yaml",
) -> list[dict[str, str | float]]:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    results: list[dict[str, str | float]] = []

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

        metrics = run_backtest(symbol, timeframe, train_str, test_str, config_path)
        if metrics:
            results.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "train_start_month": current.strftime("%Y-%m"),
                    "train_end_date": train_str,
                    "test_start_month": train_end.strftime("%Y-%m"),
                    "test_end_date": test_str,
                    "trades": metrics.get("trades", 0.0),
                    "win_rate": metrics.get("win_rate", 0.0),
                    "sharpe": metrics.get("sharpe", 0.0),
                }
            )

        current = train_end

    if results:
        sharpe_mean = mean(float(r["sharpe"]) for r in results)
        win_rate_mean = mean(float(r["win_rate"]) for r in results)
        print(f"OOS Mean Sharpe: {sharpe_mean:.2f}")
        print(f"OOS Win Rate: {win_rate_mean * 100:.1f}%")

        out = Path(output_path)
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "symbol",
                    "timeframe",
                    "train_start_month",
                    "train_end_date",
                    "test_start_month",
                    "test_end_date",
                    "trades",
                    "win_rate",
                    "sharpe",
                ],
            )
            writer.writeheader()
            writer.writerows(results)

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run walk-forward optimization")
    parser.add_argument("symbol", nargs="?", default="ETHUSDT")
    parser.add_argument("timeframe", nargs="?", default="5m")
    parser.add_argument("start", nargs="?", default="2023-01-01")
    parser.add_argument("end", nargs="?", default="2024-01-01")
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--output", default="wfo_results.csv")
    parser.add_argument(
        "--config",
        default=os.getenv("SETTINGS_PATH", "config/settings.yaml"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        wfo(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start=args.start,
            end=args.end,
            train_months=args.train_months,
            test_months=args.test_months,
            output_path=args.output,
            config_path=args.config,
        )
    )
