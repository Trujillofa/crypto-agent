#!/usr/bin/env python3
"""Fixed-config walk-forward OOS runner.

This is not parameter optimization. Calendar windows, half-open inclusive
fetch bounds, and ``execution_parity_v2`` match
``scripts/experiment_autopilot.py``. Use that script for gated WFO.
Not a live-go.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import subprocess
import sys
from pathlib import Path
from statistics import mean

sys.path.append(os.getcwd())

from src.backtest.experiment_autopilot import (  # noqa: E402
    WfoWindow,
    build_wfo_windows,
    wfo_inclusive_fetch_bounds,
)
from src.backtest.research_safety import refuse_live_go  # noqa: E402

ExecutionProfile = str


def oos_fetch_windows(
    start: str,
    end: str,
    train_months: int,
    test_months: int,
) -> list[tuple[WfoWindow, str, str]]:
    """Calendar WFO folds with inclusive reader bounds for the OOS test only."""
    rows: list[tuple[WfoWindow, str, str]] = []
    for window in build_wfo_windows(start, end, train_months, test_months):
        _, _, test_start, test_end = wfo_inclusive_fetch_bounds(window)
        rows.append((window, test_start, test_end))
    return rows


def run_backtest(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    config_path: str,
    replay_sentiment_log: str | None = None,
    replay_sentiment_max_age_hours: float | None = None,
    execution_profile: ExecutionProfile = "execution_parity_v2",
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
        "--execution-profile",
        execution_profile,
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
    execution_profile: ExecutionProfile = "execution_parity_v2",
) -> list[dict[str, str | float]]:
    results: list[dict[str, str | float]] = []

    for window, test_start, test_end in oos_fetch_windows(start, end, train_months, test_months):
        print(
            f"Train: {window.train_start} - {window.train_end} | "
            f"Test: {window.test_start} - {window.test_end}"
        )

        metrics = run_backtest(
            symbol,
            timeframe,
            test_start,
            test_end,
            config_path,
            replay_sentiment_log=replay_sentiment_log,
            replay_sentiment_max_age_hours=replay_sentiment_max_age_hours,
            execution_profile=execution_profile,
        )
        if metrics:
            results.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "train_start_month": window.train_start[:7],
                    "train_end_date": window.train_end[:10],
                    "test_start_month": window.test_start[:7],
                    "test_end_date": window.test_end[:10],
                    "trades": metrics.get("trades", 0.0),
                    "win_rate": metrics.get("win_rate", 0.0),
                    "sharpe": metrics.get("sharpe", 0.0),
                }
            )

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
    parser = argparse.ArgumentParser(
        description="Fixed-config walk-forward OOS (same clock as experiment_autopilot)"
    )
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
        "--execution-profile",
        choices=("legacy_v1", "execution_parity_v2"),
        default="execution_parity_v2",
        help="Execution semantics; v2 is the canonical WFO default",
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
            execution_profile=args.execution_profile,
        )
    )
