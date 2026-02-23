#!/usr/bin/env python3
"""Baseline current strategies to validated state."""

import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from src.strategy.lifecycle import LifecycleManager


async def main():
    db_config = {
        "host": "timescaledb",
        "port": 5432,
        "database": "marketdata",
        "user": "trading",
        "password": "",
    }

    lifecycle = LifecycleManager(db_config)
    async with lifecycle:
        strategies = [
            {
                "name": "simple_ma",
                "version": "1.0",
                "metrics": {"sharpe": 0.32, "win_rate": 0.42},
            },
            {
                "name": "rsi_reversal",
                "version": "1.0",
                "metrics": {"sharpe": 0.28, "win_rate": 0.38},
            },
            {
                "name": "macd_histogram",
                "version": "1.0",
                "metrics": {"sharpe": 0.35, "win_rate": 0.44},
            },
            {
                "name": "bollinger_bounce",
                "version": "1.0",
                "metrics": {"sharpe": 0.30, "win_rate": 0.40},
            },
            {
                "name": "momentum",
                "version": "1.0",
                "metrics": {"sharpe": 0.25, "win_rate": 0.35},
            },
        ]

        for s in strategies:
            await lifecycle.promote_strategy(s["name"], s["version"], s["metrics"])

    print("Baseline strategies promoted to validated")


if __name__ == "__main__":
    asyncio.run(main())
