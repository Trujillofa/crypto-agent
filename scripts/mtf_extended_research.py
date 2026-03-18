#!/usr/bin/env python3
"""MTF Extended Research Script

Run extended MTF research with longer date range and more symbols.
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
    entry_pullback_pct: float,
    start_date: str,
    end_date: str,
    allow_short: bool = True,
) -> dict:
    config = {
        "regime_threshold": regime_threshold,
        "entry_pullback_pct": entry_pullback_pct,
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
        allow_short=allow_short,
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
        print("MTF EXTENDED RESEARCH - Longer Date Range (2024-01-01 to 2024-12-31)")
        print("=" * 90)

        # Test BTC with best params over longer period
        print("\n### BTCUSDT - Full Year 2024 ###")
        print(
            f"{'sym':>10} | {'trades':>6} | {'win%':>6} | {'return$':>8} | {'return%':>7} | {'DD%':>5} | {'sharpe':>6} | {'PF':>5}"
        )
        print("-" * 85)

        result = await run_backtest(
            "BTCUSDT",
            regime_threshold=0.005,
            entry_pullback_pct=0.01,
            start_date="2024-01-01T00:00:00",
            end_date="2024-12-31T00:00:00",
        )
        print(
            f"{'BTCUSDT':>10} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['return_pct']:>6.1f}% | {result['max_drawdown']:>5.1f} | {result['sharpe']:>6.2f} | {result['profit_factor']:>5.2f}"
        )

        # Test different threshold on longer period
        print("\n### Threshold Comparison - Full Year ###")
        print(
            f"{'thresh':>8} | {'trades':>6} | {'win%':>6} | {'return$':>8} | {'return%':>7} | {'DD%':>5} | {'sharpe':>6}"
        )
        print("-" * 70)

        for threshold in [0.003, 0.005, 0.007, 0.01]:
            result = await run_backtest(
                "BTCUSDT",
                regime_threshold=threshold,
                entry_pullback_pct=0.01,
                start_date="2024-01-01T00:00:00",
                end_date="2024-12-31T00:00:00",
            )
            print(
                f"{threshold:>8.3f} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['return_pct']:>6.1f}% | {result['max_drawdown']:>5.1f} | {result['sharpe']:>6.2f}"
            )

        # Test longs only
        print("\n### Longs Only vs Shorts Enabled ###")
        print(
            f"{'mode':>10} | {'trades':>6} | {'win%':>6} | {'return$':>8} | {'return%':>7} | {'DD%':>5} | {'sharpe':>6}"
        )
        print("-" * 70)

        for allow_short in [False, True]:
            result = await run_backtest(
                "BTCUSDT",
                regime_threshold=0.005,
                entry_pullback_pct=0.01,
                start_date="2024-01-01T00:00:00",
                end_date="2024-12-31T00:00:00",
                allow_short=allow_short,
            )
            mode = "SHORTS" if allow_short else "LONGSONLY"
            print(
                f"{mode:>10} | {result['trades']:>6} | {result['win_rate']:>6.1f} | ${result['return']:>7.0f} | {result['return_pct']:>6.1f}% | {result['max_drawdown']:>5.1f} | {result['sharpe']:>6.2f}"
            )

        print("\n" + "=" * 90)
        print("RESEARCH COMPLETE")
        print("=" * 90)

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
