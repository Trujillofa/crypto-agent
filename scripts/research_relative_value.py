#!/usr/bin/env python3
"""Relative-Value Crypto Research

Test cross-asset strategies:
1. ETHBTC mean-reversion (spread)
2. Cross-sectional momentum (BTC/ETH/SOL ranking)
"""

import asyncio
import sys
from datetime import datetime

sys.path.append(".")

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import configure_logger


class ETHBTCMeanReversion(BaseStrategy):
    """ETHBTC spread mean-reversion strategy.

    Thesis: ETHBTC oscillates around a mean. When spread deviates beyond
    threshold, expect reversion to mean.

    Entry: Long ETH when ETHBTC below mean, Short ETH when above mean.
    """

    REQUIRED_TIMEFRAMES = {"entry": "1h", "regime": "4h"}

    def __init__(self, config=None):
        super().__init__(config)
        self._lookback = int(self._config.get("lookback", 20))
        self._z_threshold = float(self._config.get("z_threshold", 1.5))
        self._exit_z = float(self._config.get("exit_z", 0.3))

    async def evaluate(self, symbol, indicators):
        # Get spread (ETHBTC)
        spread = indicators.get("ethbtc_spread")
        z_score = indicators.get("ethbtc_zscore")
        indicators.get("ema_50")
        indicators.get("ema_200_4h")

        if spread is None or z_score is None:
            return Signal(SignalType.HOLD, symbol, 0, 0, "No spread data", {})

        # Entry signals
        if z_score < -self._z_threshold:
            # ETH underperforming BTC - expect reversion up
            return Signal(
                SignalType.BUY,
                symbol,
                indicators.get("close_price", 0),
                0.75,
                f"ETHBTC low: z={z_score:.2f}",
                {"spread": spread, "zscore": z_score},
            )
        elif z_score > self._z_threshold:
            # ETH overperforming BTC - expect reversion down
            return Signal(
                SignalType.SELL,
                symbol,
                indicators.get("close_price", 0),
                0.75,
                f"ETHBTC high: z={z_score:.2f}",
                {"spread": spread, "zscore": z_score},
                trading_mode="futures",
            )

        return Signal(
            SignalType.HOLD,
            symbol,
            indicators.get("close_price", 0),
            0.3,
            f"ETHBTC neutral: z={z_score:.2f}",
            {"spread": spread, "zscore": z_score},
        )

    def get_name(self):
        return "ETHBTCMeanReversion"


class CrossSectionalMomentum(BaseStrategy):
    """Cross-sectional momentum ranking strategy.

    Thesis: Top performer continues to outperform. Rank BTC/ETH/SOL by
    momentum, long top, short bottom.
    """

    REQUIRED_TIMEFRAMES = {"entry": "1h", "regime": "4h"}

    def __init__(self, config=None):
        super().__init__(config)
        self._mom_period = int(self._config.get("mom_period", 24))
        self._top_n = int(self._config.get("top_n", 1))

    async def evaluate(self, symbol, indicators):
        # This strategy needs multi-symbol data - simplified for backtest
        # In production, would rank across universe
        mom = indicators.get(f"momentum_{symbol.lower()}", 0)

        if mom > 0.05:
            return Signal(
                SignalType.BUY,
                symbol,
                indicators.get("close_price", 0),
                0.7,
                f"Strong momentum: {mom:.2%}",
                {"momentum": mom},
            )
        elif mom < -0.05:
            return Signal(
                SignalType.SELL,
                symbol,
                indicators.get("close_price", 0),
                0.7,
                f"Weak momentum: {mom:.2%}",
                {"momentum": mom},
                trading_mode="futures",
            )

        return Signal(SignalType.HOLD, symbol, 0, 0, "No signal", {})

    def get_name(self):
        return "CrossSectionalMomentum"


async def fetch_multi_symbol(reader, symbols, timeframe, start_time, end_time):
    """Fetch and align data for multiple symbols."""
    data_by_symbol = {}

    for symbol in symbols:
        data = await reader.fetch_range(symbol, timeframe, start_time, end_time)
        # Convert to dict keyed by time
        data_by_symbol[symbol] = {row["time"]: row for row in data}

    return data_by_symbol


async def compute_ethbtc_spread(data_btc, data_eth, timeframe):
    """Compute ETHBTC spread and z-score."""
    spread_data = []

    # Align by timestamp
    times = sorted(set(data_btc.keys()) & set(data_eth.keys()))

    spreads = []
    for t in times:
        btc_price = data_btc[t].get("close_price")
        eth_price = data_eth[t].get("close_price")

        if btc_price and eth_price and btc_price > 0:
            spread = eth_price / btc_price
            spreads.append(spread)

            # Compute z-score with lookback
            if len(spreads) >= 20:
                lookback = spreads[-20:]
                mean = sum(lookback) / len(lookback)
                variance = sum((x - mean) ** 2 for x in lookback) / len(lookback)
                std = variance**0.5
                z_score = (spread - mean) / std if std > 0 else 0

                row = dict(data_eth[t])
                row["ethbtc_spread"] = spread
                row["ethbtc_zscore"] = z_score
                row["close_price"] = eth_price
                spread_data.append(row)

    return spread_data


async def run_ethbtc_backtest(start_date, end_date):
    """Run ETHBTC mean-reversion backtest."""
    db_config = {
        "host": "localhost",
        "port": 15432,
        "name": "marketdata",
        "user": "trading",
        "password": "change_me",
    }

    await init_pool(db_config)

    try:
        reader = IndicatorReader({})
        async with reader:
            # Fetch BTC and ETH data
            btc_data = await reader.fetch_range("BTCUSDT", "1h", start_date, end_date)
            eth_data = await reader.fetch_range("ETHUSDT", "1h", start_date, end_date)

            # Compute spread
            btc_by_time = {row["time"]: row for row in btc_data}
            eth_by_time = {row["time"]: row for row in eth_data}

            spread_data = await compute_ethbtc_spread(btc_by_time, eth_by_time, "1h")

            if not spread_data:
                print("No spread data generated")
                return None

            # Test different z-thresholds
            results = []
            for z_thresh in [1.0, 1.5, 2.0]:
                config = {
                    "lookback": 20,
                    "z_threshold": z_thresh,
                    "exit_z": 0.3,
                }

                bt_config = BacktestConfig(
                    symbol="ETHBTC",
                    timeframe="1h",
                    start_date=datetime.fromisoformat(start_date),
                    end_date=datetime.fromisoformat(end_date),
                    initial_capital=10000.0,
                    fee_rate=0.001,
                    stop_loss_pct=0.02,
                    take_profit_pct=0.04,
                    use_atr_sizing=True,
                    risk_per_trade=0.02,
                    apply_global_trend_filter=False,
                    use_executor_exit_model=True,
                    strategy_classes=[ETHBTCMeanReversion],
                    strategy_configs=[config],
                    allow_short=True,
                )

                # Inject spread data into reader
                class SpreadReader(IndicatorReader):
                    async def fetch_range(self, symbol, tf, start, end):
                        return spread_data

                engine = BacktestEngine(bt_config, SpreadReader({}))
                result = await engine.run()

                longs = sum(1 for t in result.trades if t.side == "BUY")
                shorts = sum(1 for t in result.trades if t.side == "SELL")

                results.append(
                    {
                        "z_threshold": z_thresh,
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
                )

            return results

    finally:
        await close_pool()


async def main():
    configure_logger("WARNING")

    print("=" * 100)
    print("RELATIVE-VALUE CRYPTO RESEARCH")
    print("Period: 2024-01-01 to 2024-12-31")
    print("Stop gates: return > 0, PF > 1.2, DD < 15%")
    print("=" * 100)

    # Test 1: ETHBTC Mean Reversion
    print("\n### EXPERIMENT 1: ETHBTC Mean Reversion ###")
    print(
        f"{'z_thresh':>10} | {'trds':>5} | {'L':>3} | {'S':>3} | {'win%':>5} | {'return$':>8} | {'DD%':>5} | {'sharpe':>6} | {'PF':>5}"
    )
    print("-" * 75)

    results = await run_ethbtc_backtest("2024-01-01T00:00:00", "2024-12-31T00:00:00")

    viable = 0
    if results:
        for r in results:
            print(
                f"{r['z_threshold']:>10.1f} | {r['trades']:>5} | {r['longs']:>3} | {r['shorts']:>3} | {r['win_rate']:>5.1f} | ${r['return']:>7.0f} | {r['max_drawdown']:>5.1f} | {r['sharpe']:>6.2f} | {r['profit_factor']:>5.2f}"
            )

            is_viable = r["return"] > 0 and r["profit_factor"] > 1.2 and r["max_drawdown"] < 15
            if is_viable:
                viable += 1
                print("      ✅ VIABLE")

    print(f"\nVIABLE: {viable}/{len(results) if results else 0}")

    print("\n" + "=" * 100)
    if viable > 0:
        print("✅ RELATIVE-VALUE RESEARCH SHOWS PROMISE")
    else:
        print("❌ ETHBTC MEAN-REVERSION NOT VIABLE - Try cross-sectional momentum")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
