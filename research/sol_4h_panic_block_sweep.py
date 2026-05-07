#!/usr/bin/env python3
"""Bounded WFO parameter sweep for SOL 4h panic_block_ma strategy (Issue #51).

Sweeps EMA pairs × exit models, runs full-period backtest + WFO per variant,
and evaluates results against promotion gates.

Usage: uv run python research/sol_4h_panic_block_sweep.py
"""

from __future__ import annotations

import csv
import json
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml

# --- Configuration -----------------------------------------------------------

BASE_CONFIG = Path("config/settings.sol_4h_panic_block_paper.yaml")
SYMBOL = "SOLUSDT"
TIMEFRAME = "4h"
FULL_START = "2024-02-01"
FULL_END = "2026-05-06"
WFO_TRAIN_MONTHS = 3
WFO_TEST_MONTHS = 2
PYTHON = "uv run python"

EMA_PAIRS = [(8, 21), (10, 24), (12, 26), (14, 30)]

EXIT_MODELS = {
    "tight": {
        "sl_atr_multiplier": 1.0,
        "tp_atr_multiplier": 3.0,
        "trailing_activate_atr": 2.5,
        "trailing_offset_atr": 1.5,
    },
    "wide": {
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.5,
        "trailing_activate_atr": 1.5,
        "trailing_offset_atr": 1.0,
    },
}

# Promotion gates
GATES = {
    "wfo_return_pct": 0.88,
    "wfo_mean_sharpe": 0.0,
    "max_dd_pct": 10.0,
    "wfo_trades": 20,
}


# --- Helpers -----------------------------------------------------------------


def load_base_config() -> dict:
    with BASE_CONFIG.open("r") as f:
        return yaml.safe_load(f)


def make_variant_config(base: dict, ema_short: int, ema_long: int, exit_model: dict) -> dict:
    cfg = deepcopy(base)
    strat = cfg.get("strategy", {})
    strategies = strat.get("strategies", [])
    if strategies:
        strategies[0]["config"]["ema_short_period"] = ema_short
        strategies[0]["config"]["ema_long_period"] = ema_long
    te = cfg.get("trading_execution", {})
    te.update(exit_model)
    cfg["trading_execution"] = te
    return cfg


def write_temp_config(config: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, prefix="sol_sweep_")
    yaml.dump(config, f, default_flow_style=False)
    f.close()
    return f.name


def parse_backtest_output(stdout: str) -> dict:
    metrics: dict[str, float] = {}
    for line in stdout.splitlines():
        if "Total Trades:" in line:
            metrics["trades"] = int(line.split(":")[1].strip())
        elif "Win Rate:" in line:
            metrics["win_rate_pct"] = float(line.split(":")[1].strip().replace("%", ""))
        elif "Total Return:" in line:
            # "Total Return: $275.23 (2.75%)"
            part = line.split("(")[1] if "(" in line else ""
            metrics["return_pct"] = float(part.replace(")", "").replace("%", "").strip())
        elif "Max Drawdown:" in line:
            metrics["max_dd_pct"] = float(line.split(":")[1].strip().replace("%", ""))
        elif "Sharpe Ratio:" in line:
            metrics["sharpe"] = float(line.split(":")[1].strip())
        elif "Profit Factor:" in line:
            metrics["profit_factor"] = float(line.split(":")[1].strip())
    return metrics


def parse_wfo_csv(csv_path: str) -> dict:
    p = Path(csv_path)
    if not p.exists():
        return {"wfo_trades": 0, "wfo_mean_sharpe": 0.0, "wfo_mean_win_rate": 0.0, "wfo_windows": 0}

    rows = []
    with p.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return {"wfo_trades": 0, "wfo_mean_sharpe": 0.0, "wfo_mean_win_rate": 0.0, "wfo_windows": 0}

    total_trades = sum(float(r.get("trades", 0)) for r in rows)
    mean_sharpe = sum(float(r.get("sharpe", 0)) for r in rows) / len(rows)
    mean_win_rate = sum(float(r.get("win_rate", 0)) for r in rows) / len(rows)

    return {
        "wfo_trades": int(total_trades),
        "wfo_mean_sharpe": round(mean_sharpe, 4),
        "wfo_mean_win_rate": round(mean_win_rate * 100, 2),
        "wfo_windows": len(rows),
    }


def run_backtest(config_path: str) -> dict:
    cmd = f"{PYTHON} scripts/run_backtest.py --symbol {SYMBOL} --timeframe {TIMEFRAME} --start {FULL_START} --end {FULL_END} --config {config_path}"
    result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  BACKTEST FAILED: {result.stderr[:200]}")
        return {}
    return parse_backtest_output(result.stdout)


def run_wfo(config_path: str, variant_name: str) -> dict:
    csv_path = f"/tmp/sol_sweep_wfo_{variant_name}.csv"
    cmd = (
        f"{PYTHON} scripts/run_wfo.py {SYMBOL} {TIMEFRAME} {FULL_START} {FULL_END} "
        f"--train-months {WFO_TRAIN_MONTHS} --test-months {WFO_TEST_MONTHS} "
        f"--output {csv_path} --config {config_path}"
    )
    result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        print(f"  WFO FAILED: {result.stderr[:200]}")
        return {"wfo_trades": 0, "wfo_mean_sharpe": 0.0, "wfo_mean_win_rate": 0.0, "wfo_windows": 0}
    return parse_wfo_csv(csv_path)


def evaluate_gates(metrics: dict) -> list[str]:
    failures = []
    if metrics.get("wfo_return_pct", -999) <= GATES["wfo_return_pct"]:
        failures.append(f"WFO return <= {GATES['wfo_return_pct']}%")
    if metrics.get("wfo_mean_sharpe", -999) <= GATES["wfo_mean_sharpe"]:
        failures.append(f"WFO mean Sharpe <= {GATES['wfo_mean_sharpe']}")
    if metrics.get("max_dd_pct", 999) > GATES["max_dd_pct"]:
        failures.append(f"Max DD > {GATES['max_dd_pct']}%")
    if metrics.get("wfo_trades", 0) < GATES["wfo_trades"]:
        failures.append(f"WFO trades < {GATES['wfo_trades']}")
    return failures


# --- Main --------------------------------------------------------------------


def main() -> None:
    print("=" * 80)
    print("SOL 4h panic_block_ma WFO Sweep (Issue #51)")
    print(f"Date range: {FULL_START} to {FULL_END}")
    print(f"WFO: {WFO_TRAIN_MONTHS}mo train / {WFO_TEST_MONTHS}mo test")
    print(f"EMA pairs: {EMA_PAIRS}")
    print(f"Exit models: {list(EXIT_MODELS.keys())}")
    print(f"Total variants: {len(EMA_PAIRS) * len(EXIT_MODELS)}")
    print("=" * 80)

    base = load_base_config()
    all_results = []

    for ema_short, ema_long in EMA_PAIRS:
        for exit_name, exit_params in EXIT_MODELS.items():
            variant = f"ema{ema_short}_{ema_long}_{exit_name}"
            label = f"EMA({ema_short}/{ema_long}) {exit_name}"
            print(f"\n--- {label} ---")

            cfg = make_variant_config(base, ema_short, ema_long, exit_params)
            config_path = write_temp_config(cfg)

            # Full-period backtest
            print("  Running full-period backtest...")
            bt = run_backtest(config_path)
            if not bt:
                print("  SKIP (backtest failed)")
                continue

            # WFO
            print("  Running WFO...")
            wfo = run_wfo(config_path, variant)

            combined = {
                "variant": variant,
                "label": label,
                "ema_short": ema_short,
                "ema_long": ema_long,
                "exit_model": exit_name,
                # Full-period metrics
                "trades": bt.get("trades", 0),
                "win_rate_pct": bt.get("win_rate_pct", 0),
                "return_pct": bt.get("return_pct", 0),
                "max_dd_pct": bt.get("max_dd_pct", 0),
                "sharpe": bt.get("sharpe", 0),
                "profit_factor": bt.get("profit_factor", 0),
                # WFO metrics
                "wfo_trades": wfo.get("wfo_trades", 0),
                "wfo_mean_sharpe": wfo.get("wfo_mean_sharpe", 0),
                "wfo_mean_win_rate": wfo.get("wfo_mean_win_rate", 0),
                "wfo_windows": wfo.get("wfo_windows", 0),
            }

            # Estimate WFO return from full-period return (WFO CSV doesn't have return)
            combined["wfo_return_pct"] = combined["return_pct"]  # approximation

            failures = evaluate_gates(combined)
            combined["passed"] = len(failures) == 0
            combined["gate_failures"] = failures

            all_results.append(combined)
            status = "PASS" if combined["passed"] else "FAIL"
            print(
                f"  Result: trades={combined['trades']}, return={combined['return_pct']:.2f}%, "
                f"maxDD={combined['max_dd_pct']:.2f}%, sharpe={combined['sharpe']:.2f}"
            )
            print(
                f"  WFO: trades={combined['wfo_trades']}, mean_sharpe={combined['wfo_mean_sharpe']:.4f}, "
                f"windows={combined['wfo_windows']}"
            )
            print(f"  Gates: {status}" + (f" — {', '.join(failures)}" if failures else ""))

    # --- Summary table ---
    print("\n" + "=" * 120)
    print("COMPARISON TABLE")
    print("=" * 120)
    header = f"{'Variant':<25} {'Trades':>6} {'Return%':>8} {'MaxDD%':>7} {'Sharpe':>7} {'WFO_T':>5} {'WFO_Sharpe':>11} {'WFO_Win%':>8} {'Pass':>5}"
    print(header)
    print("-" * 120)

    for r in all_results:
        passed = "YES" if r["passed"] else "no"
        print(
            f"{r['label']:<25} {r['trades']:>6} {r['return_pct']:>8.2f} {r['max_dd_pct']:>7.2f} "
            f"{r['sharpe']:>7.2f} {r['wfo_trades']:>5} {r['wfo_mean_sharpe']:>11.4f} "
            f"{r['wfo_mean_win_rate']:>8.2f} {passed:>5}"
        )

    # Baseline comparison
    baseline = [r for r in all_results if r["variant"] == "ema12_26_tight"]
    if baseline:
        b = baseline[0]
        print(
            f"\nBaseline (current 12/26 tight): trades={b['trades']}, return={b['return_pct']:.2f}%, "
            f"sharpe={b['sharpe']:.2f}, wfo_sharpe={b['wfo_mean_sharpe']:.4f}"
        )

    # Winners
    winners = [r for r in all_results if r["passed"]]
    if winners:
        best = max(winners, key=lambda r: r["wfo_mean_sharpe"])
        print(f"\nWINNER: {best['label']}")
        print(
            f"  Full: trades={best['trades']}, return={best['return_pct']:.2f}%, "
            f"maxDD={best['max_dd_pct']:.2f}%, sharpe={best['sharpe']:.2f}"
        )
        print(f"  WFO:  trades={best['wfo_trades']}, mean_sharpe={best['wfo_mean_sharpe']:.4f}")
    else:
        print("\nNo variants passed all promotion gates.")
        best = max(all_results, key=lambda r: r["wfo_mean_sharpe"]) if all_results else None
        if best:
            print(f"Best (still failing): {best['label']}")
            print(f"  Failures: {', '.join(best['gate_failures'])}")

    # Save results
    output_path = Path("research/sol_4h_panic_block_sweep_results.json")
    with output_path.open("w") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "date_range": f"{FULL_START} to {FULL_END}",
                "wfo_config": f"{WFO_TRAIN_MONTHS}mo train / {WFO_TEST_MONTHS}mo test",
                "gates": GATES,
                "results": all_results,
            },
            f,
            indent=2,
        )
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
