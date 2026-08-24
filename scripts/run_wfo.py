#!/usr/bin/env python3
"""Walk-Forward Optimization runner."""

import argparse
import asyncio
import csv
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

sys.path.append(os.getcwd())

from src.backtest.research_safety import refuse_live_go


def run_backtest(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    config_path: str,
    replay_sentiment_log: str | None = None,
    replay_sentiment_max_age_hours: float | None = None,
) -> dict[str, float] | None:
    cmd = [
        sys.executable,
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
    if replay_sentiment_log:
        cmd.extend(["--replay-sentiment-log", replay_sentiment_log])
    if replay_sentiment_max_age_hours is not None:
        cmd.extend(["--replay-sentiment-max-age-hours", str(replay_sentiment_max_age_hours)])
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
    replay_sentiment_log: str | None = None,
    replay_sentiment_max_age_hours: float | None = None,
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

        metrics = run_backtest(
            symbol,
            timeframe,
            train_str,
            test_str,
            config_path,
            replay_sentiment_log=replay_sentiment_log,
            replay_sentiment_max_age_hours=replay_sentiment_max_age_hours,
        )
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
    parser.add_argument(
        "--replay-sentiment-log",
        type=str,
        default=None,
        help="Path to event_log JSONL with sentiment_score events for replay",
    )
    parser.add_argument(
        "--replay-sentiment-max-age-hours",
        type=float,
        default=None,
        help="Max age in hours for replayed sentiment lookup",
    )
    return parser.parse_args()


if __name__ == "__main__":
    refuse_live_go(argv=sys.argv[1:])
    args = parse_args()
    refuse_live_go(flags=vars(args))
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
            replay_sentiment_log=args.replay_sentiment_log,
            replay_sentiment_max_age_hours=args.replay_sentiment_max_age_hours,
        )
    )
