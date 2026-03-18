#!/usr/bin/env python3
"""Test MTF Breakout/Expansion Template"""

import asyncio
import sys
from datetime import datetime

sys.path.append(".")

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.mtf_breakout import MTFBreakoutExpansionTemplate
from src.utils.logger import configure_logger


async def run_backtest(
    symbol: str,
    volatility_threshold: float,
    breakout_threshold: float,
    trend_slope_threshold: float,
    start_date: str,
    end_date: str,
) -> dict:
    config = {
        "volatility_threshold": volatility_threshold,
        "breakout_threshold": breakout_threshold,
        "trend_slope_threshold": trend_slope_threshold,
        "reclaim_threshold": 0.003,
        "ema_period": 50,
        "confidence_boost": 1.2,
    }

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
        strategy_classes=[MTFBreakoutExpansionTemplate],
        strategy_configs=[config],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.7, "sell_threshold": -0.6},
        allow_short=True,
    )

    reader = IndicatorReader({})
    async with reader:
        engine = BacktestEngine(bt_config, reader)
        result = await engine.run()

    longs = sum(1 for t in result.trades if t.side == "BUY")
    shorts = sum(1 for t in result.trades if t.side == "SELL")

    return {
        "trades": result.total_trades,
        "longs": longs,
        "shorts": shorts,
        "win_rate": result.win_rate,
        "return": result.total_return,
        "return_pct": result.total_return_pct,
        "max_drawdown": result.max_drawdown * 100,
        "sharpe": result.sharpe_ratio,
        "profit_factor": result.profit_factor,
    }


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
        print("=" * 110)
        print("MTF BREAKOUT/EXPANSION TEMPLATE TEST")
        print("Period: 2024-01-01 to 2024-12-31")
        print("Stop conditions: PF < 1, DD > 15%, return < 0")
        print("=" * 110)

        # Test configurations
        configs = [
            # (volatility_threshold, breakout_threshold, trend_slope_threshold)
            (55, 0.02, 0.002),  # Base: expansion + near high
            (50, 0.02, 0.002),  # Lower volatility
            (55, 0.015, 0.002),  # Tighter breakout
            (60, 0.02, 0.003),  # Higher vol + slope
            (55, 0.025, 0.002),  # Wider breakout range
        ]

        print("\n### Configuration Sweep ###")
        print(
            f"{'vol':>4} | {'brk':>5} | {'slp':>5} | {'trds':>5} | {'L':>3} | {'S':>3} | {'win%':>5} | {'return$':>8} | {'DD%':>5} | {'sharpe':>6} | {'PF':>5}"
        )
        print("-" * 95)

        viable_count = 0

        for vol, brk, slp in configs:
            result = await run_backtest(
                "BTCUSDT",
                volatility_threshold=vol,
                breakout_threshold=brk,
                trend_slope_threshold=slp,
                start_date="2024-01-01T00:00:00",
                end_date="2024-12-31T00:00:00",
            )

            print(
                f"{vol:>4} | {brk:>5.3f} | {slp:>5.3f} | {result['trades']:>5} | {result['longs']:>3} | {result['shorts']:>3} | {result['win_rate']:>5.1f} | ${result['return']:>7.0f} | {result['max_drawdown']:>5.1f} | {result['sharpe']:>6.2f} | {result['profit_factor']:>5.2f}"
            )

            # Check viability
            is_viable = (
                result["profit_factor"] >= 1.0
                and result["max_drawdown"] <= 15.0
                and result["return"] > 0
            )

            if is_viable:
                viable_count += 1
                print(f"      ✅ VIABLE")
            else:
                # Check which stop condition failed
                failures = []
                if result["profit_factor"] < 1.0:
                    failures.append(f"PF={result['profit_factor']:.2f}")
                if result["max_drawdown"] > 15.0:
                    failures.append(f"DD={result['max_drawdown']:.1f}%")
                if result["return"] <= 0:
                    failures.append(f"ret=${result['return']:.0f}")
                print(f"      ❌ STOP: {', '.join(failures)}")

        print("\n" + "=" * 110)
        print(f"VIABLE CONFIGURATIONS: {viable_count}/{len(configs)}")

        if viable_count > 0:
            print("✅ AT LEAST ONE VIABLE - Breakout thesis shows promise")
        else:
            print("❌ ALL FAILED - Abandon MTF family, try different strategy class")
        print("=" * 110)

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
