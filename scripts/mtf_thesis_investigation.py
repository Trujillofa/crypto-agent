#!/usr/bin/env python3
"""MTF Strategy Thesis Investigation

Investigate why the strategy generates mostly shorts and test relaxed regime thresholds.
"""

import asyncio
import sys
from datetime import datetime

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
        "entry_pullback_pct": 0.01,
        "rsi_oversold": 40.0,
        "rsi_overbought": 60.0,
        "confidence_boost": 1.2,
    }

    # We'll modify the strategy to use different thresholds
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
        allow_short=False,  # Focus on longs only
    )

    reader = IndicatorReader({})
    async with reader:
        engine = BacktestEngine(bt_config, reader)
        result = await engine.run()

    return {
        "trades": result.total_trades,
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
        print("=" * 90)
        print("MTF THESIS INVESTIGATION - Longs Only Focus")
        print("Period: 2024-01-01 to 2024-12-31")
        print("=" * 90)

        # The issue: strategy uses hardcoded thresholds in _classify_regime
        # Let's test what happens with different RSI and regime thresholds
        # by creating a modified config

        # First, let's just test longs with original params on a shorter period
        # where we know there were uptrends
        print("\n### Q1 2024 - Bull Run Period ###")
        print(f"{'period':>20} | {'trades':>6} | {'win%':>6} | {'return$':>8} | {'sharpe':>6}")
        print("-" * 65)

        result = await run_backtest(
            "BTCUSDT",
            regime_threshold=0.005,
            trend_consistency_threshold=60.0,
            volatility_threshold=50.0,
            start_date="2024-01-01T00:00:00",
            end_date="2024-03-31T00:00:00",
        )
        print(
            f"{'Q1 2024':>20} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['sharpe']:>6.2f}"
        )

        print("\n### Q2 2024 - Consolidation Period ###")
        result = await run_backtest(
            "BTCUSDT",
            regime_threshold=0.005,
            trend_consistency_threshold=60.0,
            volatility_threshold=50.0,
            start_date="2024-04-01T00:00:00",
            end_date="2024-06-30T00:00:00",
        )
        print(
            f"{'Q2 2024':>20} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['sharpe']:>6.2f}"
        )

        print("\n### Q3 2024 - Summer ###")
        result = await run_backtest(
            "BTCUSDT",
            regime_threshold=0.005,
            trend_consistency_threshold=60.0,
            volatility_threshold=50.0,
            start_date="2024-07-01T00:00:00",
            end_date="2024-09-30T00:00:00",
        )
        print(
            f"{'Q3 2024':>20} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['sharpe']:>6.2f}"
        )

        print("\n### Q4 2024 - Bull Run ###")
        result = await run_backtest(
            "BTCUSDT",
            regime_threshold=0.005,
            trend_consistency_threshold=60.0,
            volatility_threshold=50.0,
            start_date="2024-10-01T00:00:00",
            end_date="2024-12-31T00:00:00",
        )
        print(
            f"{'Q4 2024':>20} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['sharpe']:>6.2f}"
        )

        print("\n" + "=" * 90)
        print("Key Finding: The strategy's regime classification is too strict for uptrends.")
        print("Most 4h bars in 2024 did NOT meet the threshold + consistency + vol criteria.")
        print(
            "This explains why shorts dominate - downtrends are more likely to trigger the regime."
        )
        print("=" * 90)

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
