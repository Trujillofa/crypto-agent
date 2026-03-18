#!/usr/bin/env python3
"""MTF Bounded Sweep - Configurable Regime Thresholds

Sweep over:
- trend_consistency_threshold: 40, 50, 60
- volatility_percentile_threshold: 30, 40, 50
"""

import asyncio
import sys
from datetime import datetime
from dataclasses import dataclass

sys.path.append(".")

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.mtf_template import MTFStrategyTemplate
from src.utils.logger import configure_logger


async def run_backtest(
    symbol: str,
    regime_threshold: float,
    trend_consistency_threshold: float,
    volatility_threshold: float,
    start_date: str,
    end_date: str,
) -> dict:
    config = {
        "regime_threshold": regime_threshold,
        "trend_consistency_threshold": trend_consistency_threshold,
        "volatility_percentile_threshold": volatility_threshold,
        "entry_pullback_pct": 0.01,
        "rsi_oversold": 40.0,
        "rsi_overbought": 60.0,
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
        strategy_classes=[MTFStrategyTemplate],
        strategy_configs=[config],
        aggregator_config={"min_agreement": 1, "buy_threshold": 0.7, "sell_threshold": -0.6},
        allow_short=True,
    )

    reader = IndicatorReader({})
    async with reader:
        engine = BacktestEngine(bt_config, reader)
        result = await engine.run()

    # Count longs vs shorts
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
        print("MTF BOUNDED SWEEP - Configurable Regime Thresholds")
        print("Period: 2024-01-01 to 2024-12-31")
        print("=" * 100)

        print("\n### SWEEP: trend_consistency_threshold x volatility_percentile_threshold ###")
        print(
            f"{'tc':>4} | {'vol':>4} | {'trds':>5} | {'L':>3} | {'S':>3} | {'win%':>5} | {'return$':>8} | {'return%':>7} | {'DD%':>5} | {'sharpe':>6} | {'PF':>5}"
        )
        print("-" * 95)

        best_result = None
        best_score = -999999

        for tc in [40, 50, 60]:
            for vol in [30, 40, 50]:
                result = await run_backtest(
                    "BTCUSDT",
                    regime_threshold=0.005,
                    trend_consistency_threshold=tc,
                    volatility_threshold=vol,
                    start_date="2024-01-01T00:00:00",
                    end_date="2024-12-31T00:00:00",
                )

                print(
                    f"{tc:>4} | {vol:>4} | {result['trades']:>5} | {result['longs']:>3} | {result['shorts']:>3} | {result['win_rate']:>5.1f} | ${result['return']:>7.0f} | {result['return_pct']:>6.1f}% | {result['max_drawdown']:>5.1f} | {result['sharpe']:>6.2f} | {result['profit_factor']:>5.2f}"
                )

                # Score: prioritize positive return, then sharpe, then trade count
                if result["return"] > 0:
                    score = result["return"] * 0.5 + result["sharpe"] * 1000 + result["trades"] * 10
                else:
                    score = result["return"]  # Just return

                if score > best_score:
                    best_score = score
                    best_result = (tc, vol, result)

        print("\n" + "=" * 100)
        print("BEST RESULT:")
        if best_result:
            tc, vol, r = best_result
            print(f"  trend_consistency_threshold: {tc}")
            print(f"  volatility_percentile_threshold: {vol}")
            print(f"  Trades: {r['trades']} (L:{r['longs']} S:{r['shorts']})")
            print(f"  Win Rate: {r['win_rate']:.1f}%")
            print(f"  Return: ${r['return']:.0f} ({r['return_pct']:.1f}%)")
            print(f"  Max DD: {r['max_drawdown']:.1f}%")
            print(f"  Sharpe: {r['sharpe']:.2f}")
            print(f"  Profit Factor: {r['profit_factor']:.2f}")
        print("=" * 100)

        # Decision
        if best_result and best_result[2]["return"] > 0 and best_result[2]["sharpe"] > 0.5:
            print("\n✅ RESULT IS VIABLE - Strategy shows positive return with acceptable Sharpe")
        else:
            print(
                "\n❌ RESULT NOT VIABLE - Even with configurable thresholds, strategy loses money"
            )
            print("   Recommendation: Stop tuning this template, move to different MTF thesis")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
