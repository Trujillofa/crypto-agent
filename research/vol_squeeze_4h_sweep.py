#!/usr/bin/env python3
"""Bounded WFO parameter sweep for volatility_squeeze strategy on SOL/AVAX 4h.

Sweeps squeeze thresholds x exit models, runs full-period backtest + WFO
per variant, and evaluates results against promotion gates.

Usage: python research/vol_squeeze_4h_sweep.py
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

SYMBOLS = ["SOLUSDT", "AVAXUSDT"]
TIMEFRAME = "4h"
FULL_START = "2024-02-01"
FULL_END = "2026-05-06"
WFO_TRAIN_MONTHS = 3
WFO_TEST_MONTHS = 2
PYTHON = "python"

# Base config template
BASE_CONFIG_TEMPLATE = {
    "agent_id": "vs-sweep",
    "mode": "paper",
    "trading": {"pairs": ["PLACEHOLDER"], "timeframe": "4h"},
    "telegram": {"enabled": False},
    "trading_execution": {
        "enabled": False,
        "test_mode": True,
        "use_atr_sizing": False,
        "order_size_usdt": 100.0,
        "exit_rules": {
            "backtest_use_executor_exit_model": True,
            "backtest_ignore_signal_sells": True,
        },
    },
    "futures": {"enabled": False},
    "ingest": {"use_websocket": False},
    "strategy": {
        "default_trading_mode": "spot",
        "strategies": [
            {
                "name": "volatility_squeeze",
                "config": {
                    "squeeze_lookback": 50,
                    "squeeze_percentile": 0.20,
                    "momentum_period": 10,
                    "atr_trail_multiplier": 3.0,
                    "max_hold_bars": 30,
                    "min_atr_pct": 0.005,
                },
            }
        ],
        "aggregator": {
            "buy_threshold": 0.45,
            "sell_threshold": -1.0,
            "btc_regime_filter_enabled": False,
        },
    },
}

# Squeeze detection presets
SQUEEZE_MODELS = {
    "tight": {
        "squeeze_lookback": 30,
        "squeeze_percentile": 0.15,
        "min_atr_pct": 0.008,
    },
    "standard": {
        "squeeze_lookback": 50,
        "squeeze_percentile": 0.20,
        "min_atr_pct": 0.005,
    },
    "loose": {
        "squeeze_lookback": 80,
        "squeeze_percentile": 0.30,
        "min_atr_pct": 0.003,
    },
}

# Exit/trailing presets
EXIT_MODELS = {
    "tight_trail": {
        "atr_trail_multiplier": 2.0,
        "max_hold_bars": 20,
        "momentum_period": 8,
    },
    "standard_trail": {
        "atr_trail_multiplier": 3.0,
        "max_hold_bars": 30,
        "momentum_period": 10,
    },
    "wide_trail": {
        "atr_trail_multiplier": 4.0,
        "max_hold_bars": 40,
        "momentum_period": 12,
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


def make_base_config(symbol: str) -> dict:
    cfg = deepcopy(BASE_CONFIG_TEMPLATE)
    cfg["trading"]["pairs"] = [symbol]
    cfg["agent_id"] = f"vs-sweep-{symbol.lower()}"
    return cfg


def make_variant_config(base: dict, squeeze_params: dict, exit_params: dict) -> dict:
    cfg = deepcopy(base)
    strat = cfg.get("strategy", {})
    strategies = strat.get("strategies", [])
    if strategies:
        strategies[0]["config"].update(squeeze_params)
        strategies[0]["config"].update(exit_params)
    return cfg


def write_temp_config(config: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, prefix="vs_sweep_")
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
        return {
            "wfo_trades": 0,
            "wfo_mean_sharpe": 0.0,
            "wfo_mean_win_rate": 0.0,
            "wfo_windows": 0,
        }
    rows = []
    with p.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    if not rows:
        return {
            "wfo_trades": 0,
            "wfo_mean_sharpe": 0.0,
            "wfo_mean_win_rate": 0.0,
            "wfo_windows": 0,
        }
    total_trades = sum(float(r.get("trades", 0)) for r in rows)
    mean_sharpe = sum(float(r.get("sharpe", 0)) for r in rows) / len(rows)
    mean_win_rate = sum(float(r.get("win_rate", 0)) for r in rows) / len(rows)
    return {
        "wfo_trades": int(total_trades),
        "wfo_mean_sharpe": round(mean_sharpe, 4),
        "wfo_mean_win_rate": round(mean_win_rate * 100, 2),
        "wfo_windows": len(rows),
    }


def run_backtest(symbol: str, config_path: str) -> dict:
    cmd = (
        f"{PYTHON} scripts/run_backtest.py --symbol {symbol} --timeframe {TIMEFRAME} "
        f"--start {FULL_START} --end {FULL_END} --config {config_path}"
    )
    result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  BACKTEST FAILED: {result.stderr[:200]}")
        return {}
    return parse_backtest_output(result.stdout)


def run_wfo(symbol: str, config_path: str, variant_name: str) -> dict:
    csv_path = f"/tmp/vs_sweep_wfo_{variant_name}.csv"
    cmd = (
        f"{PYTHON} scripts/run_wfo.py {symbol} {TIMEFRAME} {FULL_START} {FULL_END} "
        f"--train-months {WFO_TRAIN_MONTHS} --test-months {WFO_TEST_MONTHS} "
        f"--output {csv_path} --config {config_path}"
    )
    result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        print(f"  WFO FAILED: {result.stderr[:200]}")
        return {
            "wfo_trades": 0,
            "wfo_mean_sharpe": 0.0,
            "wfo_mean_win_rate": 0.0,
            "wfo_windows": 0,
        }
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
    squeeze_names = list(SQUEEZE_MODELS.keys())
    exit_names = list(EXIT_MODELS.keys())

    print("=" * 80)
    print("Volatility Squeeze 4h WFO Sweep (SOL + AVAX)")
    print(f"Symbols: {SYMBOLS}")
    print(f"Date range: {FULL_START} to {FULL_END}")
    print(f"WFO: {WFO_TRAIN_MONTHS}mo train / {WFO_TEST_MONTHS}mo test")
    print(f"Squeeze models: {squeeze_names}")
    print(f"Exit models: {exit_names}")
    print(f"Total variants per symbol: {len(squeeze_names) * len(exit_names)}")
    print(
        f"Promotion gates: return>{GATES['wfo_return_pct']}%, "
        f"sharpe>{GATES['wfo_mean_sharpe']}, "
        f"DD<={GATES['max_dd_pct']}%, trades>={GATES['wfo_trades']}"
    )
    print("=" * 80)

    all_results = []

    for symbol in SYMBOLS:
        print(f"\n{'#' * 80}")
        print(f"# {symbol}")
        print(f"{'#' * 80}")

        base = make_base_config(symbol)

        for sq_name, sq_params in SQUEEZE_MODELS.items():
            for ex_name, ex_params in EXIT_MODELS.items():
                variant = f"{symbol}_{sq_name}_{ex_name}"
                label = f"{symbol} / {sq_name} / {ex_name}"
                print(f"\n--- {label} ---")

                cfg = make_variant_config(base, sq_params, ex_params)
                config_path = write_temp_config(cfg)

                print("  Running full-period backtest...")
                bt = run_backtest(symbol, config_path)
                if not bt:
                    print("  SKIP (backtest failed)")
                    continue

                print("  Running WFO...")
                wfo = run_wfo(symbol, config_path, variant)

                combined = {
                    "variant": variant,
                    "label": label,
                    "symbol": symbol,
                    "squeeze_model": sq_name,
                    "exit_model": ex_name,
                    "trades": bt.get("trades", 0),
                    "win_rate_pct": bt.get("win_rate_pct", 0),
                    "return_pct": bt.get("return_pct", 0),
                    "max_dd_pct": bt.get("max_dd_pct", 0),
                    "sharpe": bt.get("sharpe", 0),
                    "profit_factor": bt.get("profit_factor", 0),
                    "wfo_trades": wfo.get("wfo_trades", 0),
                    "wfo_mean_sharpe": wfo.get("wfo_mean_sharpe", 0),
                    "wfo_mean_win_rate": wfo.get("wfo_mean_win_rate", 0),
                    "wfo_windows": wfo.get("wfo_windows", 0),
                }
                combined["wfo_return_pct"] = combined["return_pct"]

                failures = evaluate_gates(combined)
                combined["passed"] = len(failures) == 0
                combined["gate_failures"] = failures

                all_results.append(combined)
                status = "PASS" if combined["passed"] else "FAIL"
                print(
                    f"  Result: trades={combined['trades']}, "
                    f"return={combined['return_pct']:.2f}%, "
                    f"maxDD={combined['max_dd_pct']:.2f}%, "
                    f"sharpe={combined['sharpe']:.2f}"
                )
                print(
                    f"  WFO: trades={combined['wfo_trades']}, "
                    f"mean_sharpe={combined['wfo_mean_sharpe']:.4f}, "
                    f"windows={combined['wfo_windows']}"
                )
                gate_msg = f"  Gates: {status}"
                if failures:
                    gate_msg += f" — {', '.join(failures)}"
                print(gate_msg)

    # --- Summary table ---
    print("\n" + "=" * 120)
    print("COMPARISON TABLE")
    print("=" * 120)
    header = (
        f"{'Variant':<36} {'Trades':>6} {'Return%':>8} {'MaxDD%':>7} "
        f"{'Sharpe':>7} {'WFO_T':>5} {'WFO_Sharpe':>11} {'WFO_Win%':>8} {'Pass':>5}"
    )
    print(header)
    print("-" * 120)

    for r in all_results:
        passed = "YES" if r["passed"] else "no"
        print(
            f"{r['label']:<36} {r['trades']:>6} {r['return_pct']:>8.2f} "
            f"{r['max_dd_pct']:>7.2f} {r['sharpe']:>7.2f} "
            f"{r['wfo_trades']:>5} {r['wfo_mean_sharpe']:>11.4f} "
            f"{r['wfo_mean_win_rate']:>8.2f} {passed:>5}"
        )

    winners = [r for r in all_results if r["passed"]]
    if winners:
        best = max(winners, key=lambda r: r["wfo_mean_sharpe"])
        print(f"\nWINNER: {best['label']}")
        print(
            f"  Full: trades={best['trades']}, return={best['return_pct']:.2f}%, "
            f"maxDD={best['max_dd_pct']:.2f}%, sharpe={best['sharpe']:.2f}"
        )
    else:
        print("\nNo variants passed all promotion gates.")
        best = max(all_results, key=lambda r: r["wfo_mean_sharpe"]) if all_results else None
        if best:
            print(f"Best (still failing): {best['label']}")
            print(f"  Failures: {', '.join(best['gate_failures'])}")

    output_path = Path("/tmp/vol_squeeze_4h_sweep_results.json")
    with output_path.open("w") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "symbols": SYMBOLS,
                "timeframe": TIMEFRAME,
                "date_range": f"{FULL_START} to {FULL_END}",
                "wfo_config": f"{WFO_TRAIN_MONTHS}mo train / {WFO_TEST_MONTHS}mo test",
                "squeeze_models": list(SQUEEZE_MODELS.keys()),
                "exit_models": list(EXIT_MODELS.keys()),
                "gates": GATES,
                "results": all_results,
            },
            f,
            indent=2,
        )
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
