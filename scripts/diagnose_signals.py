#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.append(os.getcwd())

from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings
from src.strategy.aggregator import SignalAggregator
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class CycleDiagnostics:
    row_time: Any
    price: float
    ema_200: float | None
    strategy_signals: list[tuple[str, Signal]]
    aggregate_signal: Signal
    total_score: float
    active_signals: int
    effective_buy_threshold: float
    sell_threshold: float
    min_agreement: int
    buy_blocked_by_trend: bool


def _resolve_db_config(settings: Any) -> dict[str, object]:
    database = settings.database
    db_password = (
        str(database.get("password", "")).strip() or os.getenv("POSTGRES_PASSWORD", "").strip()
    )
    return {
        "host": str(os.getenv("DB_HOST", database.get("host", "localhost"))),
        "port": int(os.getenv("DB_PORT", int(database.get("port", 5432)))),
        "name": str(os.getenv("DB_NAME", database.get("name", "marketdata"))),
        "user": str(os.getenv("DB_USER", database.get("user", "trading"))),
        "password": str(os.getenv("DB_PASSWORD", db_password)),
    }


def _score(signal: Signal) -> float:
    if signal.type == SignalType.BUY:
        return signal.confidence
    if signal.type == SignalType.SELL:
        return -signal.confidence
    return 0.0


def _format_signal(name: str, signal: Signal) -> str:
    return f"{name}: {signal.type.value:<4} conf={signal.confidence:.2f} reason={signal.reason}"


def _effective_thresholds(
    aggregator_config: dict[str, object],
    per_symbol_config: dict[str, object],
    price: float,
    ema_200: float | None,
) -> tuple[float, float, int]:
    buy_threshold = float(
        per_symbol_config.get("buy_threshold", aggregator_config.get("buy_threshold", 0.5))
    )
    buy_threshold_uptrend = float(
        per_symbol_config.get(
            "buy_threshold_uptrend",
            aggregator_config.get("buy_threshold_uptrend", buy_threshold),
        )
    )
    sell_threshold = float(
        per_symbol_config.get("sell_threshold", aggregator_config.get("sell_threshold", -0.5))
    )
    min_agreement = int(
        per_symbol_config.get("min_agreement", aggregator_config.get("min_agreement", 1))
    )
    in_uptrend = ema_200 is not None and price > ema_200
    effective_buy_threshold = buy_threshold_uptrend if in_uptrend else buy_threshold
    return effective_buy_threshold, sell_threshold, min_agreement


async def _replay_symbol(
    reader: IndicatorReader,
    settings: Any,
    symbol: str,
    rows: int,
) -> CycleDiagnostics | None:
    history = await reader.fetch_latest(symbol, settings.timeframe, limit=rows)
    if len(history) < 2:
        return None

    strategy_classes, strategy_configs, aggregator_config, per_symbol_aggregator_config = (
        _resolve_strategy_config(settings.strategy)
    )
    strategies: list[BaseStrategy] = []
    for index, strategy_class in enumerate(strategy_classes):
        config = strategy_configs[index] if index < len(strategy_configs) else {}
        strategies.append(strategy_class(config))

    aggregator = SignalAggregator(aggregator_config, settings.strategy.default_trading_mode)
    latest: CycleDiagnostics | None = None

    for row in history:
        named_signals: list[tuple[str, Signal]] = []
        for strategy in strategies:
            signal = await strategy.evaluate(symbol, row)
            named_signals.append((strategy.get_name(), signal))

        signals = [signal for _, signal in named_signals]
        aggregate = aggregator.aggregate(
            symbol,
            signals,
            ema_200=row.get("ema_200"),
            symbol_config=per_symbol_aggregator_config.get(symbol, {}),
        )
        total_score = sum(_score(signal) for signal in signals)
        active_signals = sum(1 for signal in signals if signal.type != SignalType.HOLD)
        effective_buy_threshold, sell_threshold, min_agreement = _effective_thresholds(
            dict(aggregator_config),
            dict(per_symbol_aggregator_config.get(symbol, {})),
            float(row["close_price"]),
            row.get("ema_200"),
        )
        buy_blocked_by_trend = False
        if aggregate.type == SignalType.BUY:
            ema_200 = row.get("ema_200")
            if ema_200 is not None and float(row["close_price"]) < ema_200:
                buy_blocked_by_trend = True

        latest = CycleDiagnostics(
            row_time=row.get("time", "unknown"),
            price=float(row["close_price"]),
            ema_200=row.get("ema_200"),
            strategy_signals=named_signals,
            aggregate_signal=aggregate,
            total_score=total_score,
            active_signals=active_signals,
            effective_buy_threshold=effective_buy_threshold,
            sell_threshold=sell_threshold,
            min_agreement=min_agreement,
            buy_blocked_by_trend=buy_blocked_by_trend,
        )

    return latest


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose live signal generation for configured strategies."
    )
    parser.add_argument(
        "--config",
        action="append",
        required=True,
        help="Path to a config file. Pass multiple times to inspect multiple agents.",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=50,
        help="Number of most recent indicator rows to replay per symbol.",
    )
    args = parser.parse_args()

    configure_logger("WARNING")

    settings_list = [load_settings(Path(config_path)) for config_path in args.config]
    db_config = _resolve_db_config(settings_list[0])

    await init_pool(db_config)
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            for config_path, settings in zip(args.config, settings_list, strict=True):
                print(
                    f"=== {config_path} | agent_id={settings.agent_id} | timeframe={settings.timeframe} ==="
                )
                for symbol in settings.trading_pairs:
                    diagnostics = await _replay_symbol(reader, settings, symbol, args.rows)
                    if diagnostics is None:
                        print(f"{symbol}: not enough indicator history")
                        continue

                    print(
                        f"{symbol} latest={diagnostics.row_time} price={diagnostics.price:.4f} "
                        f"ema200={diagnostics.ema_200 if diagnostics.ema_200 is not None else 'None'}"
                    )
                    for name, signal in diagnostics.strategy_signals:
                        print(f"  {_format_signal(name, signal)}")
                    print(
                        "  aggregate: "
                        f"type={diagnostics.aggregate_signal.type.value} "
                        f"score={diagnostics.total_score:.2f} "
                        f"active={diagnostics.active_signals} "
                        f"buy_threshold={diagnostics.effective_buy_threshold:.2f} "
                        f"sell_threshold={diagnostics.sell_threshold:.2f} "
                        f"min_agreement={diagnostics.min_agreement} "
                        f"trend_block={diagnostics.buy_blocked_by_trend}"
                    )
                    print(f"  aggregate_reason: {diagnostics.aggregate_signal.reason}")
                print()
    finally:
        await close_pool()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
