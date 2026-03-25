#!/usr/bin/env python3
"""Relative-Value Crypto Research - Debugged

Test ETHBTC mean-reversion with better spread alignment.
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
    """ETHBTC spread mean-reversion strategy."""

    REQUIRED_TIMEFRAMES = {"entry": "1h", "regime": "4h"}

    def __init__(self, config=None):
        super().__init__(config)
        self._z_threshold = float(self._config.get("z_threshold", 1.5))

    async def evaluate(self, symbol, indicators):
        z_score = indicators.get("ethbtc_zscore", 0)
        close = indicators.get("close_price", 0)

        if z_score < -self._z_threshold:
            return Signal(
                SignalType.BUY,
                symbol,
                close,
                0.75,
                f"ETHBTC low z={z_score:.2f}",
                {"zscore": z_score},
            )
        elif z_score > self._z_threshold:
            return Signal(
                SignalType.SELL,
                symbol,
                close,
                0.75,
                f"ETHBTC high z={z_score:.2f}",
                {"zscore": z_score},
                trading_mode="futures",
            )

        return Signal(
            SignalType.HOLD, symbol, close, 0.3, f"neutral z={z_score:.2f}", {"zscore": z_score}
        )

    def get_name(self):
        return "ETHBTCMeanReversion"


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
        reader = IndicatorReader({})
        async with reader:
            # Fetch raw data
            btc_data = await reader.fetch_range(
                "BTCUSDT", "1h", "2024-01-01T00:00:00", "2024-03-01T00:00:00"
            )
            eth_data = await reader.fetch_range(
                "ETHUSDT", "1h", "2024-01-01T00:00:00", "2024-03-01T00:00:00"
            )

            print(f"BTC data points: {len(btc_data)}")
            print(f"ETH data points: {len(eth_data)}")

            if btc_data:
                print(f"BTC first time: {btc_data[0].get('time')}")
                print(f"BTC last time: {btc_data[-1].get('time')}")

            if eth_data:
                print(f"ETH first time: {eth_data[0].get('time')}")
                print(f"ETH last time: {eth_data[-1].get('time')}")

            # Build spread using approximate time alignment
            btc_by_time = {}
            for row in btc_data:
                t = row.get("time")
                if t:
                    # Round to hour for alignment
                    if isinstance(t, str):
                        t = t[:13]  # Take YYYY-MM-DD HH
                    btc_by_time[t] = row.get("close_price", 0)

            print(f"BTC mapped: {len(btc_by_time)}")

            spread_rows = []
            spreads = []
            for row in eth_data:
                t = row.get("time")
                if t and isinstance(t, str):
                    t = t[:13]

                eth_price = row.get("close_price", 0)
                btc_price = btc_by_time.get(t, 0)

                if btc_price and btc_price > 0 and eth_price:
                    spread = eth_price / btc_price
                    spreads.append(spread)

                    if len(spreads) >= 10:
                        lookback = spreads[-20:] if len(spreads) >= 20 else spreads
                        mean = sum(lookback) / len(lookback)
                        variance = sum((x - mean) ** 2 for x in lookback) / len(lookback)
                        std = variance**0.5
                        z_score = (spread - mean) / std if std > 0 else 0

                        new_row = dict(row)
                        new_row["ethbtc_spread"] = spread
                        new_row["ethbtc_zscore"] = z_score
                        spread_rows.append(new_row)

            print(f"Spread rows generated: {len(spread_rows)}")

            if spread_rows:
                # Test one config
                config = {"z_threshold": 1.5}
                bt_config = BacktestConfig(
                    symbol="ETHBTC",
                    timeframe="1h",
                    start_date=datetime(2024, 2, 1),
                    end_date=datetime(2024, 3, 1),
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

                class SpreadReader(IndicatorReader):
                    async def fetch_range(self, symbol, tf, start, end):
                        return spread_rows

                engine = BacktestEngine(bt_config, SpreadReader({}))
                result = await engine.run()

                print("\nBacktest result:")
                print(f"  Trades: {result.total_trades}")
                print(f"  Win rate: {result.win_rate:.1f}%")
                print(f"  Return: ${result.total_return:.0f}")
                print(f"  DD: {result.max_drawdown * 100:.1f}%")
                print(f"  Sharpe: {result.sharpe_ratio:.2f}")
                print(f"  PF: {result.profit_factor:.2f}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
