from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from typing import cast

import yaml

from src.ingest.binance import BinanceIngestor
from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics, MetricsServer
from src.features import IndicatorComputer, IndicatorWriter
from src.features.reader import IndicatorReader
from src.features.metrics import IndicatorMetrics
from src.execution import TradingExecutor, TradingConfig
from src.execution.metrics import ExecutionMetrics
from src.risk.manager import RiskManager
from src.strategy import StrategyEngine, EngineConfig, SimpleMACrossoverStrategy
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class StrategySettings:
    evaluation_interval_seconds: int


@dataclass(frozen=True)
class Settings:
    mode: str
    log_level: str
    trading_pairs: list[str]
    timeframe: str
    database: Mapping[str, object]
    prometheus_port: int
    trading_execution: TradingConfig
    strategy: StrategySettings


def load_settings(config_path: Path) -> Settings:
    with config_path.open("r", encoding="utf-8") as file_handle:
        raw = cast(object, yaml.safe_load(file_handle))

    root = _as_mapping(raw, "root configuration")
    trading = _as_mapping(root.get("trading"), "trading section")
    database = _as_mapping(root.get("database"), "database section")
    prometheus = _as_mapping(root.get("prometheus"), "prometheus section")
    trading_exec = _as_mapping(
        root.get("trading_execution"), "trading_execution section"
    )
    strategy = _as_mapping(root.get("strategy"), "strategy section")

    # Get API key from environment if not in config
    import os as _os

    api_key = _as_str(
        trading_exec.get("api_key"), "trading_execution.api_key", default=""
    )
    if not api_key:
        api_key = _os.getenv("BINANCE_API_KEY", "").strip()

    api_secret = _as_str(
        trading_exec.get("api_secret"), "trading_execution.api_secret", default=""
    )
    if not api_secret:
        api_secret = _os.getenv("BINANCE_API_SECRET", "").strip()

    trading_pairs = _as_str_list(trading.get("pairs"), "trading.pairs")
    if not trading_pairs:
        raise ValueError("No trading pairs configured in 'trading.pairs'")

    trading_config = TradingConfig(
        api_key=api_key,
        api_secret=api_secret,
        test_mode=_as_bool(
            trading_exec.get("test_mode"), "trading_execution.test_mode", default=True
        ),
        enabled=_as_bool(
            trading_exec.get("enabled"), "trading_execution.enabled", default=False
        ),
        symbols=trading_pairs,
        order_size_usdt=_as_float(
            trading_exec.get("order_size_usdt"),
            "trading_execution.order_size_usdt",
            default=100.0,
        ),
    )

    strategy_config = StrategySettings(
        evaluation_interval_seconds=_as_int(
            strategy.get("evaluation_interval_seconds"),
            "strategy.evaluation_interval_seconds",
            default=60,
        )
    )

    return Settings(
        mode=_as_str(root.get("mode"), "mode", default="paper"),
        log_level=_as_str(root.get("log_level"), "log_level", default="INFO"),
        trading_pairs=trading_pairs,
        timeframe=_as_str(trading.get("timeframe"), "trading.timeframe", default="1m"),
        database=database,
        prometheus_port=_as_int(
            prometheus.get("port"), "prometheus.port", default=8000
        ),
        trading_execution=trading_config,
        strategy=strategy_config,
    )


def _as_mapping(value: object, field: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise ValueError(f"Expected mapping for {field}")


def _as_str(value: object, field: str, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    raise ValueError(f"Expected string for {field}")


def _as_str_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError(f"Expected list of strings for {field}")


def _as_int(value: object, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"Expected integer for {field}")


def _as_float(value: object, field: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"Expected float for {field}")
    raise ValueError(f"Expected float for {field}")


def _as_bool(value: object, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(value, int):
        return value != 0
    raise ValueError(f"Expected boolean for {field}")


async def run() -> None:
    settings = load_settings(Path("config/settings.yaml"))
    configure_logger(settings.log_level)

    # Initialize risk manager
    risk_manager = RiskManager(Path("config/risk.yaml"))

    # Check if trading is allowed
    is_allowed, reason = risk_manager.is_trading_allowed()
    if not is_allowed:
        print(f"Trading blocked: {reason}")
        return

    # Initialize metrics
    ingest_metrics = IngestMetrics()
    indicator_metrics = IndicatorMetrics()
    execution_metrics = ExecutionMetrics()

    # Start Prometheus metrics server
    MetricsServer(ingest_metrics.registry).start(settings.prometheus_port)

    # Initialize OHLCV writer and ingestor
    writer = TimescaleWriter(settings.database, ingest_metrics)
    ingestor = BinanceIngestor(
        settings.trading_pairs, settings.timeframe, ingest_metrics
    )

    # Initialize indicator pipeline
    indicator_writer = IndicatorWriter(settings.database)
    indicator_computer = IndicatorComputer(
        config=settings.database,
        symbols=settings.trading_pairs,
        timeframe=settings.timeframe,
        writer=indicator_writer,
        metrics=indicator_metrics,
        compute_interval=60,  # Compute every 60 seconds
    )

    # Initialize trading executor
    trading_executor = TradingExecutor(
        config=settings.trading_execution,
        risk_manager=risk_manager,
        metrics=execution_metrics,
    )

    # Initialize indicator reader and strategy engine
    indicator_reader = IndicatorReader(settings.database)
    engine_config = EngineConfig(
        symbols=settings.trading_pairs,
        database=settings.database,
        timeframe=settings.timeframe,
        evaluation_interval_seconds=settings.strategy.evaluation_interval_seconds,
        strategy_classes=[SimpleMACrossoverStrategy],
        strategy_configs={
            "SimpleMACrossoverStrategy": {"ema_short_period": 12, "ema_long_period": 26}
        },
    )
    strategy_engine = StrategyEngine(config=engine_config, reader=indicator_reader)

    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: _handle_signal())

    async with writer:
        async with indicator_writer:
            async with indicator_reader:
                async with ingestor:
                    async with trading_executor:
                        async with strategy_engine:
                            # Start risk monitoring in background
                            risk_task = asyncio.create_task(risk_manager.monitor_loop())

                            # Start OHLCV ingestion
                            ingest_task = asyncio.create_task(
                                ingestor.run(writer.write_ohlcv)
                            )

                            # Start indicator computation
                            indicator_task = asyncio.create_task(
                                indicator_computer.run()
                            )

                            # Start trading executor
                            trading_task = asyncio.create_task(trading_executor.run())

                            # Start strategy engine
                            strategy_task = asyncio.create_task(
                                strategy_engine.run(
                                    on_signal=trading_executor.on_signal
                                )
                            )

                            await stop_event.wait()

                            # Cancel all tasks
                            ingest_task.cancel()
                            risk_task.cancel()
                            indicator_task.cancel()
                            trading_task.cancel()
                            strategy_task.cancel()

                            indicator_computer.stop()
                            trading_executor.stop()

                            with contextlib.suppress(asyncio.CancelledError):
                                await ingest_task
                                await risk_task
                                await indicator_task
                                await trading_task
                                await strategy_task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
