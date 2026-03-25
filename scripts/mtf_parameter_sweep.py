#!/usr/bin/env python3
"""MTF Parameter Sweep Script

Run systematic parameter sweeps for MTF strategy research.
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime

sys.path.append(".")

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.mtf_template import MTFStrategyTemplate
from src.utils.logger import configure_logger


@dataclass
class SweepResult:
    params: dict[str, float]
    trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    sharpe: float


async def run_sweep(
    symbol: str,
    regime_threshold: float,
    entry_pullback_pct: float,
    start_date: str,
    end_date: str,
) -> SweepResult:
    config = {
        "regime_threshold": regime_threshold,
        "entry_pullback_pct": entry_pullback_pct,
        "rsi_oversold": 40.0,
        "rsi_overbought": 60.0,
        "confidence_boost": 1.2,
    }
    MTFStrategyTemplate(config=config)

    bt_config = BacktestConfig(
        symbol=symbol,
        timeframe="1h",
        start_date=datetime.fromisoformat(start_date),
        end_date=datetime.fromisoformat(end_date),
        initial_capital=10000.0,
        fee_rate=0.001,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        use_atr_sizing=True,
        risk_per_trade=0.02,
        apply_global_trend_filter=False,
        use_executor_exit_model=True,
        strategy_classes=[MTFStrategyTemplate],
        strategy_configs=[config],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.7, "sell_threshold": -0.6},
        allow_short=True,
    )

    reader = IndicatorReader({})
    async with reader:
        engine = BacktestEngine(bt_config, reader)
        result = await engine.run()

    return SweepResult(
        params={"regime_threshold": regime_threshold, "entry_pullback_pct": entry_pullback_pct},
        trades=result.total_trades,
        win_rate=result.win_rate,
        total_return=result.total_return,
        max_drawdown=result.max_drawdown * 100,
        sharpe=result.sharpe_ratio,
    )


async def main():
    configure_logger("WARNING")

    db_config = {
        "host": "localhost",
        "port": 15432,
        "name": "marketdata",
        "user": "trading",
        "password": "change_me",
    }

    await init_pool(db_config)

    try:
        print("=" * 80)
        print("MTF PARAMETER SWEEP - BTCUSDT")
        print("Period: 2024-06-01 to 2024-09-01")
        print("=" * 80)

        # Sweep regime_threshold
        print("\n### EXPERIMENT 1: Regime Threshold ###")
        print(
            f"{'threshold':>10} | {'trades':>6} | {'win%':>6} | {'return':>8} | {'DD%':>6} | {'sharpe':>6}"
        )
        print("-" * 60)

        for threshold in [0.003, 0.005, 0.007, 0.01]:
            result = await run_sweep(
                "BTCUSDT",
                regime_threshold=threshold,
                entry_pullback_pct=0.01,
                start_date="2024-06-01T00:00:00",
                end_date="2024-09-01T00:00:00",
            )
            print(
                f"{threshold:>10.3f} | {result.trades:>6} | {result.win_rate:>6.1f} | ${result.total_return:>7.0f} | {result.max_drawdown:>5.1f} | {result.sharpe:>6.2f}"
            )

        # Sweep entry_pullback_pct
        print("\n### EXPERIMENT 2: Entry Pullback Depth ###")
        print(
            f"{'pullback':>10} | {'trades':>6} | {'win%':>6} | {'return':>8} | {'DD%':>6} | {'sharpe':>6}"
        )
        print("-" * 60)

        for pullback in [0.005, 0.01, 0.015, 0.02]:
            result = await run_sweep(
                "BTCUSDT",
                regime_threshold=0.005,
                entry_pullback_pct=pullback,
                start_date="2024-06-01T00:00:00",
                end_date="2024-09-01T00:00:00",
            )
            print(
                f"{pullback:>10.3f} | {result.trades:>6} | {result.win_rate:>6.1f} | ${result.total_return:>7.0f} | {result.max_drawdown:>5.1f} | {result.sharpe:>6.2f}"
            )

        # Best params test on ETH
        print("\n### EXPERIMENT 3: Best Params on ETHUSDT ###")
        print(
            f"{'symbol':>10} | {'trades':>6} | {'win%':>6} | {'return':>8} | {'DD%':>6} | {'sharpe':>6}"
        )
        print("-" * 60)

        result = await run_sweep(
            "ETHUSDT",
            regime_threshold=0.005,
            entry_pullback_pct=0.01,
            start_date="2024-06-01T00:00:00",
            end_date="2024-09-01T00:00:00",
        )
        print(
            f"{'ETHUSDT':>10} | {result.trades:>6} | {result.win_rate:>6.1f} | ${result.total_return:>7.0f} | {result.max_drawdown:>5.1f} | {result.sharpe:>6.2f}"
        )

        print("\n" + "=" * 80)
        print("SWEEP COMPLETE")
        print("=" * 80)

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
