#!/usr/bin/env python3
"""Sweep min_confidence parameter to measure its impact on signal quality.

Compares baseline (no filter) vs various min_confidence thresholds
across the 5-strategy ensemble on SOLUSDT 4h.
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings
from src.utils.logger import configure_logger


@dataclass
class SweepResult:
    min_confidence: float
    trades: int
    win_rate: float
    return_pct: float
    max_drawdown: float
    sharpe: float
    profit_factor: float
    wins: int
    losses: int


async def run_single(
    reader: IndicatorReader,
    strategy_classes: list,
    strategy_configs: list,
    base_aggregator_config: dict,
    min_confidence: float,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    exit_config: dict,
) -> SweepResult:
    agg_config = {**base_aggregator_config, "min_confidence": min_confidence}

    config = BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        initial_capital=10000.0,
        fee_rate=0.001,
        stop_loss_pct=float(exit_config.get("stop_loss_pct", 0.02)),
        take_profit_pct=float(exit_config.get("take_profit_pct", 0.05)),
        sl_atr_multiplier=float(exit_config.get("sl_atr_multiplier", 2.0)),
        tp_atr_multiplier=float(exit_config.get("tp_atr_multiplier", 4.5)),
        trailing_activate_atr=float(exit_config.get("trailing_activate_atr", 1.5)),
        trailing_offset_atr=float(exit_config.get("trailing_offset_atr", 1.0)),
        use_executor_exit_model=bool(exit_config.get("backtest_use_executor_exit_model", False)),
        ignore_signal_sells=bool(exit_config.get("backtest_ignore_signal_sells", False)),
        apply_global_trend_filter=True,
        allow_short=True,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=agg_config,
    )

    engine = BacktestEngine(config, reader)
    result = await engine.run()

    wins = len([t for t in result.trades if t.pnl > 0])
    losses = len([t for t in result.trades if t.pnl <= 0])

    return SweepResult(
        min_confidence=min_confidence,
        trades=result.total_trades,
        win_rate=result.win_rate,
        return_pct=result.total_return_pct,
        max_drawdown=result.max_drawdown * 100,
        sharpe=result.sharpe_ratio,
        profit_factor=result.profit_factor,
        wins=wins,
        losses=losses,
    )


async def main():
    configure_logger("WARNING")

    config_path = Path("config/settings.yaml")
    settings = load_settings(config_path)
    result = _resolve_strategy_config(settings.strategy)
    strategy_classes = result[0]
    strategy_configs = result[1]
    base_aggregator_config = dict(result[2])

    import yaml

    with config_path.open() as f:
        raw = yaml.safe_load(f) or {}
    exit_config = raw.get("trading_execution", {}).get("exit_rules", {}) or {}
    # Also grab top-level SL/TP if exit_rules doesn't have them
    te = raw.get("trading_execution", {})
    if "stop_loss_pct" not in exit_config:
        exit_config["stop_loss_pct"] = te.get("stop_loss_pct", 0.02)
    if "take_profit_pct" not in exit_config:
        exit_config["take_profit_pct"] = te.get("take_profit_pct", 0.05)

    db_config = {
        "host": str(os.getenv("DB_HOST", settings.database.get("host", "localhost"))),
        "port": int(os.getenv("DB_PORT", int(settings.database.get("port", 5432)))),
        "name": str(os.getenv("DB_NAME", settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("DB_USER", settings.database.get("user", "trading"))),
        "password": str(os.getenv("DB_PASSWORD", settings.database.get("password", ""))),
    }

    await init_pool(db_config)

    # Sweep parameters
    symbol = "SOLUSDT"
    timeframe = "4h"
    periods = [
        ("2024-01-01", "2024-12-31", "2024 Full Year"),
        ("2025-01-01", "2025-12-31", "2025 Full Year"),
        ("2024-01-01", "2025-12-31", "2024-2025 Combined"),
    ]
    min_confidence_values = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7]

    print(f"Strategies: {[c.__name__ for c in strategy_classes]}")
    print(
        f"Base aggregator: buy_threshold={base_aggregator_config.get('buy_threshold')}, "
        f"sell_threshold={base_aggregator_config.get('sell_threshold')}, "
        f"min_agreement={base_aggregator_config.get('min_agreement')}"
    )
    print(f"Exit: SL={exit_config.get('stop_loss_pct')}, TP={exit_config.get('take_profit_pct')}")
    print()

    try:
        reader = IndicatorReader(db_config)
        async with reader:
            for start, end, label in periods:
                print(f"{'=' * 70}")
                print(f"  {symbol} {timeframe} — {label} ({start} to {end})")
                print(f"{'=' * 70}")
                print(
                    f"{'min_conf':>8} | {'Trades':>6} | {'W':>3}/{'L':>3} | {'Win%':>6} | {'Return%':>8} | {'MaxDD%':>6} | {'Sharpe':>7} | {'PF':>5}"
                )
                print(f"{'-' * 70}")

                results = []
                for mc in min_confidence_values:
                    r = await run_single(
                        reader,
                        strategy_classes,
                        strategy_configs,
                        base_aggregator_config,
                        mc,
                        symbol,
                        timeframe,
                        start,
                        end,
                        exit_config,
                    )
                    results.append(r)

                    pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 100 else "inf"
                    print(
                        f"{r.min_confidence:>8.1f} | {r.trades:>6} | {r.wins:>3}/{r.losses:>3} | "
                        f"{r.win_rate:>5.1f}% | {r.return_pct:>+7.2f}% | {r.max_drawdown:>5.1f}% | "
                        f"{r.sharpe:>+6.2f} | {pf_str:>5}"
                    )

                # Summary
                baseline = results[0]
                best = max(results, key=lambda r: r.sharpe if r.trades > 0 else -999)
                if best.min_confidence != baseline.min_confidence and best.trades > 0:
                    print(
                        f"\n  Best: min_confidence={best.min_confidence} "
                        f"(Sharpe {best.sharpe:+.2f} vs baseline {baseline.sharpe:+.2f})"
                    )
                print()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
