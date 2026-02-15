from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from typing import cast

import yaml

from src.ingest.binance import BinanceIngestor
from src.ingest.websocket import BinanceWebSocketIngestor
from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics, MetricsServer
from src.features import IndicatorComputer, IndicatorWriter
from src.features.reader import IndicatorReader
from src.features.metrics import IndicatorMetrics
from src.execution import (
    TradingExecutor,
    TradingConfig,
    FuturesTradingExecutor,
    FuturesTradingConfig,
)
from src.execution.metrics import ExecutionMetrics
from src.notifications.telegram import TelegramConfig, TelegramNotifier
from src.portfolio import PortfolioManager
from src.risk.manager import RiskManager
from src.strategy import (
    StrategyEngine,
    EngineConfig,
    BaseStrategy,
    SimpleMACrossoverStrategy,
    RSIReversalStrategy,
    MACDHistogramStrategy,
    BollingerBounceStrategy,
    MomentumStrategy,
)
from src.utils.logger import configure_logger, get_logger


@dataclass(frozen=True)
class StrategySettings:
    evaluation_interval_seconds: int
    default_trading_mode: str = "spot"
    cooldown_candles: int = 3
    strategies: list[Mapping[str, object]] = field(default_factory=list)
    aggregator: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class FuturesSettings:
    """Futures trading configuration."""

    enabled: bool
    symbols: list[str]
    default_leverage: int
    max_leverage: int
    margin_mode: str
    position_mode: str
    test_mode: bool
    liquidation_buffer_pct: float


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
    telegram: TelegramConfig
    use_websocket: bool
    futures: FuturesSettings | None = None


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
    ingest = _as_mapping(root.get("ingest"), "ingest section")
    telegram = _as_mapping(root.get("telegram"), "telegram section")

    # Get API key from environment if not in config
    import os as _os

    # Check if test_mode is enabled first
    test_mode = _as_bool(
        trading_exec.get("test_mode"), "trading_execution.test_mode", default=True
    )

    # Use testnet API keys when in test mode, otherwise use production keys
    if test_mode:
        api_key = _as_str(
            trading_exec.get("api_key"), "trading_execution.api_key", default=""
        )
        if not api_key:
            api_key = _os.getenv("BINANCE_TESTNET_API_KEY", "").strip()

        api_secret = _as_str(
            trading_exec.get("api_secret"), "trading_execution.api_secret", default=""
        )
        if not api_secret:
            api_secret = _os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()
    else:
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

    db_password = _as_str(database.get("password"), "database.password", default="")
    if not db_password:
        db_password = _os.getenv("POSTGRES_PASSWORD", "").strip()

    trading_pairs = _as_str_list(trading.get("pairs"), "trading.pairs")
    if not trading_pairs:
        raise ValueError("No trading pairs configured in 'trading.pairs'")

    trading_config = TradingConfig(
        api_key=api_key,
        api_secret=api_secret,
        test_mode=test_mode,
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

    # Parse default trading mode (spot or futures)
    default_trading_mode = _as_str(
        strategy.get("default_trading_mode"),
        "strategy.default_trading_mode",
        default="spot",
    )
    if default_trading_mode not in ("spot", "futures"):
        raise ValueError(
            f"strategy.default_trading_mode='{default_trading_mode}' is invalid. "
            "Must be 'spot' or 'futures'."
        )

    strategy_config = StrategySettings(
        evaluation_interval_seconds=_as_int(
            strategy.get("evaluation_interval_seconds"),
            "strategy.evaluation_interval_seconds",
            default=60,
        ),
        default_trading_mode=default_trading_mode,
        cooldown_candles=_as_int(
            strategy.get("cooldown_candles"),
            "strategy.cooldown_candles",
            default=3,
        ),
        strategies=_as_list_of_mappings(
            strategy.get("strategies"), "strategy.strategies"
        ),
        aggregator=_as_mapping(strategy.get("aggregator"), "strategy.aggregator"),
    )

    telegram_bot_token = _as_str(
        telegram.get("bot_token"), "telegram.bot_token", default=""
    )
    if not telegram_bot_token:
        telegram_bot_token = _os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    telegram_chat_id = _as_str(telegram.get("chat_id"), "telegram.chat_id", default="")
    if not telegram_chat_id:
        telegram_chat_id = _os.getenv("TELEGRAM_CHAT_ID", "").strip()

    telegram_config = TelegramConfig(
        bot_token=telegram_bot_token,
        chat_id=telegram_chat_id,
        enabled=_as_bool(telegram.get("enabled"), "telegram.enabled", default=False),
        rate_limit_seconds=_as_int(
            telegram.get("rate_limit_seconds"),
            "telegram.rate_limit_seconds",
            default=5,
        ),
    )

    # Parse and validate futures configuration
    futures = _as_mapping(root.get("futures"), "futures section")
    if futures:
        futures_enabled = _as_bool(
            futures.get("enabled"), "futures.enabled", default=False
        )
        if futures_enabled:
            # Validate leverage limits (hard cap at 20x)
            leverage = _as_int(
                futures.get("max_leverage"), "futures.max_leverage", default=10
            )
            if leverage > 20:
                raise ValueError(
                    f"futures.max_leverage={leverage} exceeds hard safety cap of 20x"
                )

            # Validate margin mode (isolated only for MVP)
            margin_mode = _as_str(
                futures.get("margin_mode"), "futures.margin_mode", default="isolated"
            )
            if margin_mode != "isolated":
                raise ValueError(
                    f"futures.margin_mode='{margin_mode}' not supported. MVP requires 'isolated' margin."
                )

            # Validate position mode (one-way only for MVP)
            position_mode = _as_str(
                futures.get("position_mode"), "futures.position_mode", default="one-way"
            )
            if position_mode != "one-way":
                raise ValueError(
                    f"futures.position_mode='{position_mode}' not supported. MVP requires 'one-way' mode."
                )

            # Log futures configuration
            _logger = get_logger("load_settings")
            _logger.info(
                f"Futures trading enabled: symbols={_as_str_list(futures.get('symbols'), 'futures.symbols')}, "
                f"leverage={leverage}x, margin={margin_mode}, mode={position_mode}"
            )

    # BLOCKER R-1 FIX: Enforce mode: paper safety
    global_mode = _as_str(root.get("mode"), "mode", default="paper")
    if global_mode == "paper":
        if not trading_config.test_mode:
            import logging

            logging.getLogger("settings").warning(
                "Configured mode='paper' but trading_execution.test_mode=False. "
                "Forcing test_mode=True for safety."
            )
            # Create new config with test_mode=True (dataclass is frozen, so use replace or new init)
            trading_config = TradingConfig(
                api_key=trading_config.api_key,
                api_secret=trading_config.api_secret,
                test_mode=True,  # FORCE TRUE
                enabled=trading_config.enabled,
                symbols=trading_config.symbols,
                order_size_usdt=trading_config.order_size_usdt,
            )

    # Parse futures configuration
    futures_enabled = _as_bool(futures.get("enabled"), "futures.enabled", default=False)
    futures_config = None
    if futures_enabled:
        futures_config = FuturesSettings(
            enabled=True,
            symbols=_as_str_list(futures.get("symbols"), "futures.symbols"),
            default_leverage=_as_int(
                futures.get("default_leverage"), "futures.default_leverage", default=5
            ),
            max_leverage=_as_int(
                futures.get("max_leverage"), "futures.max_leverage", default=10
            ),
            margin_mode=_as_str(
                futures.get("margin_mode"), "futures.margin_mode", default="isolated"
            ),
            position_mode=_as_str(
                futures.get("position_mode"), "futures.position_mode", default="one-way"
            ),
            test_mode=_as_bool(
                futures.get("test_mode"), "futures.test_mode", default=True
            ),
            liquidation_buffer_pct=_as_float(
                futures.get("liquidation_buffer_pct"),
                "futures.liquidation_buffer_pct",
                default=5.0,
            ),
        )

    return Settings(
        mode=global_mode,
        log_level=_as_str(root.get("log_level"), "log_level", default="INFO"),
        trading_pairs=trading_pairs,
        timeframe=_as_str(trading.get("timeframe"), "trading.timeframe", default="1m"),
        database={
            **database,
            "password": db_password,
        },
        prometheus_port=_as_int(
            prometheus.get("port"), "prometheus.port", default=8000
        ),
        trading_execution=trading_config,
        strategy=strategy_config,
        telegram=telegram_config,
        use_websocket=_as_bool(
            ingest.get("use_websocket"), "ingest.use_websocket", default=False
        ),
        futures=futures_config,
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


def _as_list_of_mappings(value: object, field: str) -> list[Mapping[str, object]]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
        return [cast(Mapping[str, object], item) for item in value]
    raise ValueError(f"Expected list of mappings for {field}")


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


def _resolve_strategy_config(
    strategy_settings: StrategySettings,
) -> tuple[list[type[BaseStrategy]], list[Mapping[str, object]], Mapping[str, object]]:
    default_strategy_classes = [
        SimpleMACrossoverStrategy,
        RSIReversalStrategy,
        MACDHistogramStrategy,
        BollingerBounceStrategy,
        MomentumStrategy,
    ]
    default_strategy_configs = [
        {"ema_short_period": 12, "ema_long_period": 26},
        {"rsi_period": 14, "oversold_threshold": 30, "overbought_threshold": 70},
        {"min_histogram_threshold": 0.0, "use_atr_filter": True},
        {"band_distance_threshold": 0.0, "rsi_oversold": 30, "rsi_overbought": 70},
        {"rsi_buy_threshold": 50, "rsi_sell_threshold": 50},
    ]
    default_aggregator_config = {
        "min_agreement": 2,
        "buy_threshold": 1.5,
        "sell_threshold": -1.5,
    }

    if not strategy_settings.strategies:
        return (
            default_strategy_classes,
            default_strategy_configs,
            default_aggregator_config,
        )

    strategy_registry: dict[str, type[BaseStrategy]] = {
        "simple_ma": SimpleMACrossoverStrategy,
        "rsi_reversal": RSIReversalStrategy,
        "macd_histogram": MACDHistogramStrategy,
        "bollinger_bounce": BollingerBounceStrategy,
        "momentum": MomentumStrategy,
    }

    strategy_classes: list[type[BaseStrategy]] = []
    strategy_configs: list[Mapping[str, object]] = []
    for entry in strategy_settings.strategies:
        name = _as_str(entry.get("name"), "strategy.strategies[].name", default="")
        if not name:
            raise ValueError("Strategy entry missing name")
        strategy_class = strategy_registry.get(name)
        if strategy_class is None:
            raise ValueError(f"Unknown strategy name: {name}")
        config = _as_mapping(entry.get("config"), f"strategy.strategies.{name}.config")
        strategy_classes.append(strategy_class)
        strategy_configs.append(config)

    aggregator_config = (
        strategy_settings.aggregator
        if strategy_settings.aggregator
        else default_aggregator_config
    )
    return strategy_classes, strategy_configs, aggregator_config


async def run() -> None:
    settings = load_settings(Path("config/settings.yaml"))
    configure_logger(settings.log_level)

    auto_migrate = _as_bool(
        settings.database.get("auto_migrate"),
        "database.auto_migrate",
        default=False,
    )

    # Initialize risk manager
    risk_manager = RiskManager(Path("config/risk.yaml"))

    # Check if trading is allowed
    is_allowed, reason = risk_manager.is_trading_allowed()
    if not is_allowed:
        logger = get_logger("main")
        logger.error("Trading blocked: %s", reason)
        return

    # Initialize metrics
    ingest_metrics = IngestMetrics()
    indicator_metrics = IndicatorMetrics()
    execution_metrics = ExecutionMetrics()

    if auto_migrate:
        from scripts import migrate

        logger = get_logger("migrations")
        try:
            result = await asyncio.to_thread(migrate.run_migrations)
        except Exception as exc:  # noqa: BLE001
            logger.error("Auto-migration failed: %s", exc)
            raise
        if result != 0:
            raise RuntimeError("Auto-migration failed. See logs for details.")

    # Start Prometheus metrics server
    MetricsServer(ingest_metrics.registry).start(settings.prometheus_port)

    # Initialize OHLCV writer and ingestor
    writer = TimescaleWriter(settings.database, ingest_metrics)

    if settings.use_websocket:
        spot_ws_url = (
            BinanceWebSocketIngestor.SPOT_TESTNET_WS_URL
            if settings.test_mode
            else BinanceWebSocketIngestor.SPOT_WS_URL
        )
        ingestor = BinanceWebSocketIngestor(
            settings.trading_pairs, settings.timeframe, ingest_metrics,
            base_url=spot_ws_url,
        )
    else:
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

    # Initialize portfolio manager for position tracking
    portfolio_manager = PortfolioManager(settings.database)

    telegram_notifier = TelegramNotifier(settings.telegram)

    # Initialize trading executor (spot)
    trading_executor = TradingExecutor(
        config=settings.trading_execution,
        risk_manager=risk_manager,
        metrics=execution_metrics,
        portfolio_manager=portfolio_manager,
        notifier=telegram_notifier,
    )

    # Initialize futures executor if enabled
    futures_executor = None
    futures_ingestor = None
    if settings.futures and settings.futures.enabled:
        logger = get_logger("main")
        logger.info("Futures trading enabled - initializing futures executor")

        futures_config = FuturesTradingConfig(
            api_key=settings.trading_execution.api_key,
            api_secret=settings.trading_execution.api_secret,
            test_mode=settings.futures.test_mode,
            enabled=True,
            symbols=settings.futures.symbols,
            default_leverage=settings.futures.default_leverage,
            max_leverage=settings.futures.max_leverage,
            margin_mode=settings.futures.margin_mode,
            position_mode=settings.futures.position_mode,
            order_size_usdt=settings.trading_execution.order_size_usdt,
            liquidation_buffer_pct=settings.futures.liquidation_buffer_pct,
        )

        futures_executor = FuturesTradingExecutor(
            config=futures_config,
            risk_manager=risk_manager,
            metrics=execution_metrics,
            portfolio_manager=portfolio_manager,
            notifier=telegram_notifier,
        )

        # Initialize futures mark price WebSocket
        futures_ingestor = BinanceWebSocketIngestor(
            symbols=settings.futures.symbols,
            timeframe=settings.timeframe,
            metrics=ingest_metrics,
            base_url=(
                BinanceWebSocketIngestor.FUTURES_WS_URL
                if not settings.futures.test_mode
                else BinanceWebSocketIngestor.FUTURES_DEMO_WS_URL
            ),
            stream_type="mark_price",
        )

    # Initialize indicator reader and strategy engine
    indicator_reader = IndicatorReader(settings.database)
    strategy_classes, strategy_configs, aggregator_config = _resolve_strategy_config(
        settings.strategy
    )
    engine_config = EngineConfig(
        symbols=settings.trading_pairs,
        database=settings.database,
        timeframe=settings.timeframe,
        evaluation_interval_seconds=settings.strategy.evaluation_interval_seconds,
        default_trading_mode=settings.strategy.default_trading_mode,
        cooldown_candles=settings.strategy.cooldown_candles,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )
    strategy_engine = StrategyEngine(config=engine_config, reader=indicator_reader)

    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _signum, _frame: _handle_signal())

    # Prepare async context managers - conditionally add futures if enabled
    context_managers = [
        writer,
        indicator_writer,
        indicator_reader,
        portfolio_manager,
        ingestor,
        trading_executor,
        strategy_engine,
    ]

    if futures_executor:
        context_managers.extend([futures_executor, futures_ingestor])

    # Nested async context managers
    async with contextlib.AsyncExitStack() as stack:
        for cm in context_managers:
            await stack.enter_async_context(cm)

        # Start risk monitoring in background
        risk_task = asyncio.create_task(risk_manager.monitor_loop())

        # Start OHLCV ingestion
        ingest_task = asyncio.create_task(ingestor.run(writer.write_ohlcv))

        # Start indicator computation
        indicator_task = asyncio.create_task(indicator_computer.run())

        # Start trading executor (spot)
        trading_task = asyncio.create_task(trading_executor.run())

        # Start strategy engine - route signals to appropriate executor
        if futures_executor:
            # Strategy signals go to both spot and futures executors
            # Executor decides based on signal.trading_mode
            async def on_signal_router(signal):
                if signal.trading_mode == "futures":
                    await futures_executor.on_signal(signal)
                else:
                    await trading_executor.on_signal(signal)

            strategy_task = asyncio.create_task(
                strategy_engine.run(on_signal=on_signal_router)
            )

            # Start futures executor and mark price monitoring
            futures_task = asyncio.create_task(futures_executor.run())
            futures_ingest_task = asyncio.create_task(
                futures_ingestor.run(lambda x: None)  # Mark price handled internally
            )
        else:
            strategy_task = asyncio.create_task(
                strategy_engine.run(on_signal=trading_executor.on_signal)
            )
            futures_task = None
            futures_ingest_task = None

        async def _log_startup_diagnostics() -> None:
            logger = get_logger("startup")
            try:
                ohlcv_rows = await writer.count_rows("ohlcv")
            except Exception:
                ohlcv_rows = 0

            try:
                indicator_rows = await indicator_writer.count_rows("indicators")
            except Exception:
                indicator_rows = 0
            latest_row = None
            if settings.trading_pairs:
                try:
                    rows = await indicator_reader.fetch_latest(
                        settings.trading_pairs[0],
                        settings.timeframe,
                        limit=1,
                    )
                    if rows:
                        latest_row = rows[-1]
                except Exception:
                    latest_row = None

            indicator_ready = False
            if latest_row is not None:
                indicator_ready = all(
                    key in latest_row
                    for key in (
                        "ema_12",
                        "ema_26",
                        "close_price",
                    )
                )

            risk_summary = risk_manager.get_risk_summary()
            logger.info(
                "Startup diagnostics: db_connected=%s ohlcv_rows=%d indicator_rows=%d indicator_ready=%s risk=%s telegram_configured=%s futures=%s",
                writer.is_connected(),
                ohlcv_rows,
                indicator_rows,
                indicator_ready,
                risk_summary,
                telegram_notifier.is_configured(),
                "enabled" if futures_executor else "disabled",
            )

        await _log_startup_diagnostics()

        await stop_event.wait()

        # Cancel all tasks
        ingest_task.cancel()
        risk_task.cancel()
        indicator_task.cancel()
        trading_task.cancel()
        strategy_task.cancel()
        if futures_task:
            futures_task.cancel()
        if futures_ingest_task:
            futures_ingest_task.cancel()

        indicator_computer.stop()
        trading_executor.stop()
        if futures_executor:
            futures_executor.stop()

        with contextlib.suppress(asyncio.CancelledError):
            await ingest_task
            await risk_task
            await indicator_task
            await trading_task
            await strategy_task
            if futures_task:
                await futures_task
            if futures_ingest_task:
                await futures_ingest_task


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
