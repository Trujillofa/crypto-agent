#!/usr/bin/env python3
"""Test MTF Continuation Template"""

import asyncio
import sys
from datetime import datetime

sys.path.append(".")

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.mtf_continuation import MTFContinuationTemplate
from src.utils.logger import configure_logger


async def run_backtest(
    symbol: str,
    regime_slope_threshold: float,
    trend_consistency_threshold: float,
    start_date: str,
    end_date: str,
) -> dict:
    config = {
        "regime_slope_threshold": regime_slope_threshold,
        "trend_consistency_threshold": trend_consistency_threshold,
        "reclaim_threshold": 0.005,
        "ema_period": 50,
        "rsi_long_min": 45.0,
        "rsi_short_max": 55.0,
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
        strategy_classes=[MTFContinuationTemplate],
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
        print("=" * 100)
        print("MTF CONTINUATION TEMPLATE TEST")
        print("Period: 2024-01-01 to 2024-12-31")
        print("=" * 100)

        print("\n### Parameter Sweep ###")
        print(
            f"{'slope':>6} | {'tc':>4} | {'trds':>5} | {'L':>3} | {'S':>3} | {'win%':>5} | {'return$':>8} | {'return%':>7} | {'DD%':>5} | {'sharpe':>6}"
        )
        print("-" * 85)

        best_result = None
        best_score = -999999

        for slope in [0.002, 0.003, 0.005]:
            for tc in [40, 45, 50]:
                result = await run_backtest(
                    "BTCUSDT",
                    regime_slope_threshold=slope,
                    trend_consistency_threshold=tc,
                    start_date="2024-01-01T00:00:00",
                    end_date="2024-12-31T00:00:00",
                )

                print(
                    f"{slope:>6.3f} | {tc:>4} | {result['trades']:>5} | {result['longs']:>3} | {result['shorts']:>3} | {result['win_rate']:>5.1f} | ${result['return']:>7.0f} | {result['return_pct']:>6.1f}% | {result['max_drawdown']:>5.1f} | {result['sharpe']:>6.2f}"
                )

                if result["return"] > 0:
                    score = result["return"] + result["sharpe"] * 500 + result["trades"] * 5
                else:
                    score = result["return"]

                if score > best_score:
                    best_score = score
                    best_result = (slope, tc, result)

        print("\n" + "=" * 100)
        print("BEST RESULT:")
        if best_result:
            slope, tc, r = best_result
            print(f"  regime_slope_threshold: {slope}")
            print(f"  trend_consistency_threshold: {tc}")
            print(f"  Trades: {r['trades']} (L:{r['longs']} S:{r['shorts']})")
            print(f"  Win Rate: {r['win_rate']:.1f}%")
            print(f"  Return: ${r['return']:.0f} ({r['return_pct']:.1f}%)")
            print(f"  Max DD: {r['max_drawdown']:.1f}%")
            print(f"  Sharpe: {r['sharpe']:.2f}")
            print(f"  PF: {r['profit_factor']:.2f}")

            # Evaluate long/short balance
            total = r["longs"] + r["shorts"]
            if total > 0:
                long_pct = r["longs"] / total * 100
                print(f"  Long/Short Balance: {long_pct:.0f}% / {100 - long_pct:.0f}%")

        print("=" * 100)

        if best_result and best_result[2]["return"] > 0:
            print("\n✅ VIABLE - Positive return achieved")
        else:
            print("\n❌ NOT VIABLE - Strategy loses money")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
