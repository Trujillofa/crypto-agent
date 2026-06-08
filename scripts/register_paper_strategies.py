#!/usr/bin/env python3
"""Register paper-only strategy versions in lifecycle metadata."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.append(os.getcwd())

from src.strategy.lifecycle import LifecycleManager

PAPER_STRATEGIES = [
    {
        "name": "SentimentMeanReversionStrategy",
        "version": "1.0",
        "metrics": {
            "notes": "Paper-only validation strategy wired through settings.sentiment_macro.yaml",
        },
    },
    {
        "name": "MTFStrategyTemplate",
        "version": "1.0",
        "metrics": {
            "notes": "Paper-only validation strategy (config file removed with disabled BTC agent)",
        },
    },
    {
        "name": "SimpleMACrossoverStrategy",
        "version": "1.0",
        "metrics": {
            "notes": "Paper-only validation strategy (config file removed with disabled AVAX agent)",
        },
    },
]


async def main() -> None:
    db_config = {
        "host": os.getenv("DB_HOST", "timescaledb"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "database": os.getenv("DB_NAME", "marketdata"),
        "user": os.getenv("DB_USER", "trading"),
        "password": os.getenv("DB_PASSWORD", ""),
    }

    async with LifecycleManager(db_config) as lifecycle:
        for strategy in PAPER_STRATEGIES:
            await lifecycle.register_paper_strategy(
                strategy["name"],
                strategy["version"],
                strategy["metrics"],
            )

    print("Paper strategies registered")


if __name__ == "__main__":
    asyncio.run(main())
