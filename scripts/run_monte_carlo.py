#!/usr/bin/env python3
"""
Monte Carlo validation via bootstrap resampling.

Runs a single deterministic backtest to obtain realized trade returns,
then resamples those returns N times (with replacement) to estimate the
distribution of Sharpe ratio, win rate, and total return. This correctly
quantifies uncertainty from the finite trade sample — unlike re-running the
same deterministic backtest with different seeds, which produces zero variance.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings
from src.utils.logger import configure_logger

SYMBOL = "SOLUSDT"
TIMEFRAME = "4h"
START = "2024-01-01"
END = "2026-02-24"
N_BOOTSTRAP = 2000
RANDOM_SEED = 42


def _compute_bootstrap_metrics(
    trade_returns: list[float],
) -> dict[str, float]:
    """Compute metrics for a single bootstrap sample."""
    n = len(trade_returns)
    if n == 0:
        return {"total_return_pct": 0.0, "win_rate": 0.0, "trade_sharpe": 0.0}

    compound = 1.0
    for r in trade_returns:
        compound *= 1.0 + r / 100.0
    total_return_pct = (compound - 1.0) * 100.0

    wins = sum(1 for r in trade_returns if r > 0)
    win_rate = wins / n * 100.0

    mean_r = sum(trade_returns) / n
    variance = sum((r - mean_r) ** 2 for r in trade_returns) / n
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    # Cap Sharpe at a meaningful ceiling; inf/nan signals degenerate sample (all wins)
    trade_sharpe = min(mean_r / std_r, 10.0) if std_r > 1e-6 else 10.0

    return {
        "total_return_pct": total_return_pct,
        "win_rate": win_rate,
        "trade_sharpe": trade_sharpe,
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = (len(sorted_v) - 1) * p / 100.0
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_v):
        return sorted_v[-1]
    frac = idx - lo
    return sorted_v[lo] * (1.0 - frac) + sorted_v[hi] * frac


def _write_report(
    baseline: dict,
    trade_returns: list[float],
    bootstrap_results: list[dict],
    report_path: Path,
) -> None:
    n_trades = len(trade_returns)
    ret_vals = [r["total_return_pct"] for r in bootstrap_results]
    win_vals = [r["win_rate"] for r in bootstrap_results]
    sharpe_vals = [r["trade_sharpe"] for r in bootstrap_results]

    ret_mean = sum(ret_vals) / len(ret_vals)
    ret_std = math.sqrt(sum((x - ret_mean) ** 2 for x in ret_vals) / len(ret_vals))

    win_mean = sum(win_vals) / len(win_vals)
    win_std = math.sqrt(sum((x - win_mean) ** 2 for x in win_vals) / len(win_vals))

    sharpe_mean = sum(sharpe_vals) / len(sharpe_vals)
    sharpe_std = math.sqrt(
        sum((x - sharpe_mean) ** 2 for x in sharpe_vals) / len(sharpe_vals)
    )

    percentile_pct_below_zero = sum(1 for r in ret_vals if r < 0) / len(ret_vals) * 100.0

    date_str = datetime.now().strftime("%Y-%m-%d")

    with open(report_path, "w") as f:
        f.write(f"# Monte Carlo Validation (Bootstrap): {SYMBOL} {TIMEFRAME}\n\n")
        f.write(f"**Date:** {date_str}\n")
        f.write(f"**Period:** {START} to {END}\n")
        f.write(f"**Method:** Bootstrap resampling ({N_BOOTSTRAP} iterations)\n")
        f.write(f"**Trade sample size:** {n_trades} trades per resample\n\n")

        f.write("## Baseline (Deterministic Backtest)\n\n")
        f.write("| Metric | Value |\n|--------|-------|\n")
        f.write(f"| Trades | {baseline['total_trades']} |\n")
        f.write(f"| Win Rate | {baseline['win_rate']:.2f}% |\n")
        f.write(f"| Total Return | {baseline['total_return_pct']:.2f}% |\n")
        f.write(f"| Max Drawdown | {baseline['max_drawdown_pct']:.2f}% |\n")
        f.write(f"| Sharpe Ratio | {baseline['sharpe_ratio']:.2f} |\n\n")

        f.write("## Bootstrap Distribution\n\n")
        f.write(
            "| Metric | Mean | Std | 5th pct | 25th pct | 50th pct | 75th pct | 95th pct |\n"
        )
        f.write("|--------|------|-----|---------|----------|----------|----------|----------|\n")
        f.write(
            f"| Return (%) | {ret_mean:.2f} | {ret_std:.2f} | "
            f"{_percentile(ret_vals, 5):.2f} | {_percentile(ret_vals, 25):.2f} | "
            f"{_percentile(ret_vals, 50):.2f} | {_percentile(ret_vals, 75):.2f} | "
            f"{_percentile(ret_vals, 95):.2f} |\n"
        )
        f.write(
            f"| Win Rate (%) | {win_mean:.2f} | {win_std:.2f} | "
            f"{_percentile(win_vals, 5):.2f} | {_percentile(win_vals, 25):.2f} | "
            f"{_percentile(win_vals, 50):.2f} | {_percentile(win_vals, 75):.2f} | "
            f"{_percentile(win_vals, 95):.2f} |\n"
        )
        f.write(
            f"| Trade Sharpe | {sharpe_mean:.2f} | {sharpe_std:.2f} | "
            f"{_percentile(sharpe_vals, 5):.2f} | {_percentile(sharpe_vals, 25):.2f} | "
            f"{_percentile(sharpe_vals, 50):.2f} | {_percentile(sharpe_vals, 75):.2f} | "
            f"{_percentile(sharpe_vals, 95):.2f} |\n"
        )

        f.write("\n## Risk Assessment\n\n")
        f.write(f"- **Probability of negative return:** {percentile_pct_below_zero:.1f}%\n")
        f.write(
            f"- **95% CI for total return:** "
            f"[{_percentile(ret_vals, 2.5):.2f}%, {_percentile(ret_vals, 97.5):.2f}%]\n"
        )
        f.write(
            f"- **Worst-case 5th percentile return:** {_percentile(ret_vals, 5):.2f}%\n"
        )

        f.write("\n## Interpretation\n\n")
        f.write(
            f"With only {n_trades} realized trades, bootstrap confidence intervals are wide. "
            "This is not a flaw in the analysis — it accurately reflects the uncertainty "
            "inherent in a low-frequency strategy. The intervals will narrow as more "
            "live trades accumulate.\n\n"
        )
        if n_trades < 30:
            f.write(
                "⚠️  **Insufficient sample**: Fewer than 30 trades. Results are directional "
                "only. Do not make config changes based solely on this analysis.\n"
            )
        elif percentile_pct_below_zero < 10:
            f.write(
                "✅ **Robust**: Less than 10% of bootstrap scenarios show a loss. "
                "Strategy appears to have genuine edge.\n"
            )
        elif percentile_pct_below_zero < 25:
            f.write(
                "⚠️  **Moderate confidence**: 10-25% of scenarios show a loss. "
                "Strategy is likely profitable but with meaningful tail risk.\n"
            )
        else:
            f.write(
                "❌ **Low confidence**: >25% of scenarios show a loss. "
                "Strategy may be curve-fit. Recommend additional out-of-sample validation.\n"
            )


async def main() -> None:
    configure_logger("WARNING")

    settings = load_settings(Path("config/settings.yaml"))
    result = _resolve_strategy_config(settings.strategy)
    strategy_classes, strategy_configs, aggregator_config = result[0], result[1], result[2]

    db_config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "15432")),
        "name": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", "change_me"),
    }

    backtest_cfg = BacktestConfig(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        start_date=START,
        end_date=END,
        initial_capital=10_000.0,
        fee_rate=0.001,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        slippage_pct=0.001,
        apply_global_trend_filter=True,
        allow_short=False,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )

    print(f"Running baseline backtest: {SYMBOL} {TIMEFRAME} {START}→{END}")
    reader = IndicatorReader(db_config)
    async with reader:
        engine = BacktestEngine(backtest_cfg, reader)
        bt_result = await engine.run()

    if bt_result.total_trades == 0:
        print("No trades in backtest period. Cannot run bootstrap.")
        return

    trade_returns = [t.return_pct for t in bt_result.trades]

    print(f"Baseline: {bt_result.total_trades} trades | "
          f"Win Rate {bt_result.win_rate:.1f}% | "
          f"Return {bt_result.total_return_pct:.2f}% | "
          f"Sharpe {bt_result.sharpe_ratio:.2f}")
    print(f"\nRunning {N_BOOTSTRAP} bootstrap iterations...")

    rng = random.Random(RANDOM_SEED)
    n_trades = len(trade_returns)
    bootstrap_results = []
    for i in range(N_BOOTSTRAP):
        sample = rng.choices(trade_returns, k=n_trades)
        bootstrap_results.append(_compute_bootstrap_metrics(sample))
        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{N_BOOTSTRAP} done")

    ret_vals = [r["total_return_pct"] for r in bootstrap_results]
    win_vals = [r["win_rate"] for r in bootstrap_results]
    sharpe_vals = [r["trade_sharpe"] for r in bootstrap_results]

    print("\n" + "=" * 60)
    print("BOOTSTRAP RESULTS")
    print("=" * 60)
    print(f"Return:      {sum(ret_vals)/len(ret_vals):.2f}% ± {math.sqrt(sum((x - sum(ret_vals)/len(ret_vals))**2 for x in ret_vals)/len(ret_vals)):.2f}%")
    print(f"  5th/95th:  {_percentile(ret_vals, 5):.2f}% / {_percentile(ret_vals, 95):.2f}%")
    print(f"Win Rate:    {sum(win_vals)/len(win_vals):.1f}% ± {math.sqrt(sum((x - sum(win_vals)/len(win_vals))**2 for x in win_vals)/len(win_vals)):.1f}%")
    print(f"  5th/95th:  {_percentile(win_vals, 5):.1f}% / {_percentile(win_vals, 95):.1f}%")
    print(f"Trade Sharpe:{sum(sharpe_vals)/len(sharpe_vals):.2f} ± {math.sqrt(sum((x - sum(sharpe_vals)/len(sharpe_vals))**2 for x in sharpe_vals)/len(sharpe_vals)):.2f}")
    pct_loss = sum(1 for r in ret_vals if r < 0) / len(ret_vals) * 100
    print(f"P(loss):     {pct_loss:.1f}%")
    if n_trades < 30:
        print(f"\n⚠️  Only {n_trades} trades — wide intervals expected. Directional only.")

    date_str = datetime.now().strftime("%Y-%m-%d")
    report_path = Path("docs/reports") / f"monte-carlo-bootstrap-{SYMBOL.lower()}-{TIMEFRAME}-{date_str}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    baseline_dict = {
        "total_trades": bt_result.total_trades,
        "win_rate": bt_result.win_rate,
        "total_return_pct": bt_result.total_return_pct,
        "max_drawdown_pct": bt_result.max_drawdown * 100,
        "sharpe_ratio": bt_result.sharpe_ratio,
    }
    _write_report(baseline_dict, trade_returns, bootstrap_results, report_path)
    print(f"\nReport saved: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
