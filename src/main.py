from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from src.db import close_pool, init_pool, is_connected
from src.execution import (
    FuturesTradingConfig,
    FuturesTradingExecutor,
    TradingConfig,
    TradingExecutor,
)
from src.execution.metrics import ExecutionMetrics
from src.execution.paper_executor import PaperExecutor, PaperTradingConfig
from src.features import IndicatorComputer, IndicatorWriter
from src.features.metrics import IndicatorMetrics
from src.features.reader import IndicatorReader
from src.ingest.binance import BinanceIngestor
from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics, MetricsServer
from src.ingest.websocket import BinanceWebSocketIngestor
from src.notifications.telegram import TelegramConfig, TelegramNotifier
from src.overseer import OverseerAgent, XAIClient
from src.portfolio import PortfolioManager
from src.risk.manager import RiskManager
from src.strategy import (
    BaseStrategy,
    BollingerBounceStrategy,
    CCIBreakoutStrategy,
    EngineConfig,
    MACDHistogramStrategy,
    MomentumStrategy,
    RSIReversalStrategy,
    SimpleMACrossoverStrategy,
    StrategyEngine,
    VWAPReversionStrategy,
)
from src.strategy.lifecycle import LifecycleManager
from src.strategy.signals import Signal
from src.utils.logger import configure_logger, get_logger


@dataclass(frozen=True)
class StrategySettings:
    evaluation_interval_seconds: int
    default_trading_mode: str = "spot"
    cooldown_candles: int = 3
    strategies: list[Mapping[str, object]] = field(default_factory=list)
    aggregator: Mapping[str, object] = field(default_factory=dict)
    # Per-symbol aggregator overrides
    per_symbol_aggregator_config: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


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
class AISettings:
    enabled: bool
    provider: str
    model: str
    polling_interval: float
    max_history: int
    allowed_chat_ids: list[str]
    api_key: str


@dataclass(frozen=True)
class Settings:
    agent_id: str
    mode: str
    log_level: str
    trading_pairs: list[str]
    timeframe: str
    database: Mapping[str, object]
    prometheus_port: int
    trading_execution: TradingConfig
    strategy: StrategySettings
    telegram: TelegramConfig
    ai: AISettings
    use_websocket: bool
    futures: FuturesSettings | None = None
    exit_rules: Mapping[str, object] = field(default_factory=dict)


def load_settings(config_path: Path) -> Settings:
    with config_path.open("r", encoding="utf-8") as file_handle:
        raw = cast(object, yaml.safe_load(file_handle))

    root = _as_mapping(raw, "root configuration")
    agent_id = _as_str(root.get("agent_id"), "agent_id", default="").strip()
    if not agent_id:
        agent_id = os.getenv("AGENT_ID", "default").strip() or "default"
    trading = _as_mapping(root.get("trading"), "trading section")
    database = _as_mapping(root.get("database"), "database section")
    prometheus = _as_mapping(root.get("prometheus"), "prometheus section")
    trading_exec = _as_mapping(root.get("trading_execution"), "trading_execution section")
    strategy = _as_mapping(root.get("strategy"), "strategy section")
    ingest = _as_mapping(root.get("ingest"), "ingest section")
    telegram = _as_mapping(root.get("telegram"), "telegram section")
    ai = _as_mapping(root.get("ai"), "ai section")

    # Check if test_mode is enabled first
    test_mode = _as_bool(trading_exec.get("test_mode"), "trading_execution.test_mode", default=True)

    # Use testnet API keys when in test mode, otherwise use production keys
    if test_mode:
        api_key = _as_str(trading_exec.get("api_key"), "trading_execution.api_key", default="")
        if not api_key:
            api_key = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()

        api_secret = _as_str(
            trading_exec.get("api_secret"), "trading_execution.api_secret", default=""
        )
        if not api_secret:
            api_secret = os.getenv("BINANCE_TESTNET_API_SECRET", "").strip()
    else:
        api_key = _as_str(trading_exec.get("api_key"), "trading_execution.api_key", default="")
        if not api_key:
            api_key = os.getenv("BINANCE_API_KEY", "").strip()

        api_secret = _as_str(
            trading_exec.get("api_secret"), "trading_execution.api_secret", default=""
        )
        if not api_secret:
            api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    db_password = _as_str(database.get("password"), "database.password", default="")
    if not db_password:
        db_password = os.getenv("POSTGRES_PASSWORD", "").strip()

    trading_pairs = _as_str_list(trading.get("pairs"), "trading.pairs")
    if not trading_pairs:
        raise ValueError("No trading pairs configured in 'trading.pairs'")

    trading_config = TradingConfig(
        api_key=api_key,
        api_secret=api_secret,
        test_mode=test_mode,
        enabled=_as_bool(trading_exec.get("enabled"), "trading_execution.enabled", default=False),
        symbols=trading_pairs,
        order_size_usdt=_as_float(
            trading_exec.get("order_size_usdt"),
            "trading_execution.order_size_usdt",
            default=100.0,
        ),
        stop_loss_pct=_as_float(
            trading_exec.get("stop_loss_pct"),
            "trading_execution.stop_loss_pct",
            default=0.01,
        ),
        take_profit_pct=_as_float(
            trading_exec.get("take_profit_pct"),
            "trading_execution.take_profit_pct",
            default=0.03,
        ),
        use_atr_sizing=_as_bool(
            trading_exec.get("use_atr_sizing"),
            "trading_execution.use_atr_sizing",
            default=False,
        ),
        atr_multiplier=_as_float(
            trading_exec.get("atr_multiplier"),
            "trading_execution.atr_multiplier",
            default=1.0,
        ),
        risk_per_trade_pct=_as_float(
            trading_exec.get("risk_per_trade_pct"),
            "trading_execution.risk_per_trade_pct",
            default=0.02,
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
        strategies=_as_list_of_mappings(strategy.get("strategies"), "strategy.strategies"),
        aggregator=_as_mapping(strategy.get("aggregator"), "strategy.aggregator"),
        per_symbol_aggregator_config=_parse_per_symbol_aggregator(
            strategy.get("per_symbol_aggregator_config"), "strategy.per_symbol_aggregator_config"
        ),
    )

    telegram_bot_token = _as_str(telegram.get("bot_token"), "telegram.bot_token", default="")
    if not telegram_bot_token:
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    telegram_chat_id = _as_str(telegram.get("chat_id"), "telegram.chat_id", default="")
    if not telegram_chat_id:
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    telegram_allowed_updates = _as_str_list(
        telegram.get("allowed_updates"),
        "telegram.allowed_updates",
    )
    if not telegram_allowed_updates:
        telegram_allowed_updates = ["message"]

    telegram_config = TelegramConfig(
        bot_token=telegram_bot_token,
        chat_id=telegram_chat_id,
        enabled=_as_bool(telegram.get("enabled"), "telegram.enabled", default=False),
        rate_limit_seconds=_as_int(
            telegram.get("rate_limit_seconds"),
            "telegram.rate_limit_seconds",
            default=5,
        ),
        allowed_updates=tuple(telegram_allowed_updates),
    )

    ai_api_key = _as_str(ai.get("api_key"), "ai.api_key", default="")
    if not ai_api_key:
        ai_api_key = os.getenv("XAI_API_KEY", "").strip()

    ai_allowed_chat_ids = _as_str_list(ai.get("allowed_chat_ids"), "ai.allowed_chat_ids")
    if not ai_allowed_chat_ids and telegram_chat_id:
        ai_allowed_chat_ids = [telegram_chat_id]

    ai_provider = _as_str(ai.get("provider"), "ai.provider", default="xai")
    if ai_provider != "xai":
        raise ValueError(f"ai.provider='{ai_provider}' is invalid. Must be 'xai'.")

    ai_settings = AISettings(
        enabled=_as_bool(ai.get("enabled"), "ai.enabled", default=False),
        provider=ai_provider,
        model=_as_str(
            ai.get("model"),
            "ai.model",
            default="grok-4-1-fast-reasoning",
        ),
        polling_interval=_as_float(
            ai.get("polling_interval"),
            "ai.polling_interval",
            default=1.0,
        ),
        max_history=_as_int(
            ai.get("max_history"),
            "ai.max_history",
            default=10,
        ),
        allowed_chat_ids=ai_allowed_chat_ids,
        api_key=ai_api_key,
    )

    # Parse and validate futures configuration
    futures = _as_mapping(root.get("futures"), "futures section")
    if futures:
        futures_enabled = _as_bool(futures.get("enabled"), "futures.enabled", default=False)
        if futures_enabled:
            # Validate leverage limits (hard cap at 20x)
            leverage = _as_int(futures.get("max_leverage"), "futures.max_leverage", default=10)
            if leverage > 20:
                raise ValueError(f"futures.max_leverage={leverage} exceeds hard safety cap of 20x")

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
                stop_loss_pct=trading_config.stop_loss_pct,
                take_profit_pct=trading_config.take_profit_pct,
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
            max_leverage=_as_int(futures.get("max_leverage"), "futures.max_leverage", default=10),
            margin_mode=_as_str(
                futures.get("margin_mode"), "futures.margin_mode", default="isolated"
            ),
            position_mode=_as_str(
                futures.get("position_mode"), "futures.position_mode", default="one-way"
            ),
            test_mode=_as_bool(futures.get("test_mode"), "futures.test_mode", default=True),
            liquidation_buffer_pct=_as_float(
                futures.get("liquidation_buffer_pct"),
                "futures.liquidation_buffer_pct",
                default=5.0,
            ),
        )

    exit_rules = _as_mapping(trading_exec.get("exit_rules"), "trading_execution.exit_rules")

    return Settings(
        agent_id=agent_id,
        mode=global_mode,
        log_level=_as_str(root.get("log_level"), "log_level", default="INFO"),
        trading_pairs=trading_pairs,
        timeframe=_as_str(trading.get("timeframe"), "trading.timeframe", default="1m"),
        database={
            **database,
            "password": db_password,
        },
        prometheus_port=_as_int(prometheus.get("port"), "prometheus.port", default=8000),
        trading_execution=trading_config,
        strategy=strategy_config,
        telegram=telegram_config,
        ai=ai_settings,
        use_websocket=_as_bool(ingest.get("use_websocket"), "ingest.use_websocket", default=False),
        futures=futures_config,
        exit_rules=exit_rules,
    )


def _as_mapping(value: object, field: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise ValueError(f"Expected mapping for {field}")


def _parse_per_symbol_aggregator(value: object, field: str) -> Mapping[str, Mapping[str, object]]:
    """Parse per-symbol aggregator config from YAML."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected mapping for {field}")
    result: dict[str, dict[str, object]] = {}
    for symbol, config in value.items():
        if not isinstance(symbol, str):
            raise ValueError(f"Expected string key for symbol in {field}")
        if not isinstance(config, Mapping):
            raise ValueError(f"Expected mapping config for symbol {symbol} in {field}")
        result[symbol] = dict(config)
    return result


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
        except ValueError as err:
            raise ValueError(f"Expected float for {field}") from err
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
) -> tuple[
    list[type[BaseStrategy]],
    list[Mapping[str, object]],
    Mapping[str, object],
    Mapping[str, Mapping[str, object]],
]:
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
        "cci_breakout": CCIBreakoutStrategy,
        "vwap_reversion": VWAPReversionStrategy,
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
        strategy_settings.aggregator if strategy_settings.aggregator else default_aggregator_config
    )
    return (
        strategy_classes,
        strategy_configs,
        aggregator_config,
        strategy_settings.per_symbol_aggregator_config,
    )


async def run() -> None:
    settings_path = Path(os.getenv("SETTINGS_PATH", "config/settings.yaml"))
    settings = load_settings(settings_path)
    configure_logger(settings.log_level)
    await init_pool(settings.database)

    auto_migrate = _as_bool(
        settings.database.get("auto_migrate"),
        "database.auto_migrate",
        default=False,
    )

    # Initialize risk manager
    risk_manager = RiskManager(
        Path("config/risk.yaml"),
        agent_id=settings.agent_id,
        paper_mode=settings.mode == "paper",
    )

    # Check if trading is allowed (warn but don't exit in paper mode —
    # the monitor_loop can auto-reset the kill switch after cooldown)
    is_allowed, reason = risk_manager.is_trading_allowed()
    if not is_allowed:
        logger = get_logger("main")
        if settings.mode == "paper":
            logger.warning(
                "Trading blocked at startup: %s (paper mode — will auto-reset if configured)",
                reason,
            )
        else:
            logger.error("Trading blocked: %s", reason)
            await close_pool()
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
            await close_pool()
            raise
        if result != 0:
            await close_pool()
            raise RuntimeError("Auto-migration failed. See logs for details.")

    # Start Prometheus metrics server
    MetricsServer(ingest_metrics.registry, is_connected_cb=is_connected).start(
        settings.prometheus_port
    )

    # Initialize OHLCV writer and ingestor
    writer = TimescaleWriter(settings.database, ingest_metrics)

    if settings.use_websocket:
        ingestor = BinanceWebSocketIngestor(
            settings.trading_pairs, settings.timeframe, ingest_metrics
        )
    else:
        ingestor = BinanceIngestor(settings.trading_pairs, settings.timeframe, ingest_metrics)

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
    portfolio_manager = PortfolioManager(settings.database, agent_id=settings.agent_id)

    telegram_notifier = TelegramNotifier(settings.telegram)

    overseer_agent = None
    if settings.ai.enabled:
        logger = get_logger("main")
        if not settings.telegram.enabled:
            logger.warning("AI overseer enabled but telegram.enabled=false; overseer disabled")
        elif not settings.telegram.bot_token:
            logger.warning("AI overseer enabled but TELEGRAM_BOT_TOKEN missing; overseer disabled")
        else:
            xai_client = None
            if settings.ai.api_key:
                xai_client = XAIClient(
                    api_key=settings.ai.api_key,
                    model=settings.ai.model,
                )
            else:
                logger.warning("AI overseer running without XAI_API_KEY; /ask will be unavailable")

            overseer_agent = OverseerAgent(
                mode=settings.mode,
                poll_interval_seconds=settings.ai.polling_interval,
                max_history=settings.ai.max_history,
                allowed_chat_ids=settings.ai.allowed_chat_ids,
                telegram=telegram_notifier,
                portfolio_manager=portfolio_manager,
                risk_manager=risk_manager,
                xai_client=xai_client,
            )

    # Initialize executors based on mode
    use_paper = settings.mode == "paper"
    paper_executor = None
    trading_executor = None
    futures_executor = None
    futures_ingestor = None

    if use_paper:
        # Internal paper trading — no Binance API calls for execution
        futures_symbols = (
            settings.futures.symbols if settings.futures and settings.futures.enabled else []
        )
        futures_leverage = (
            settings.futures.default_leverage
            if settings.futures and settings.futures.enabled
            else 3
        )
        paper_config = PaperTradingConfig(
            enabled=True,
            order_size_usdt=settings.trading_execution.order_size_usdt,
            initial_balance=10000.0,
            symbols=settings.trading_pairs,
            futures_symbols=futures_symbols,
            futures_leverage=futures_leverage,
            stop_loss_pct=settings.trading_execution.stop_loss_pct,
            take_profit_pct=settings.trading_execution.take_profit_pct,
            use_atr_sizing=settings.trading_execution.use_atr_sizing,
            atr_multiplier=settings.trading_execution.atr_multiplier,
            risk_per_trade_pct=settings.trading_execution.risk_per_trade_pct,
        )
        paper_executor = PaperExecutor(
            config=paper_config,
            risk_manager=risk_manager,
            metrics=execution_metrics,
            notifier=telegram_notifier,
            portfolio_manager=portfolio_manager,
            db_config=settings.database,
            agent_id=settings.agent_id,
        )
        get_logger("main").info("Paper mode: using internal PaperExecutor (no Binance API)")
    else:
        # Live mode — real Binance API executors
        trading_executor = TradingExecutor(
            config=settings.trading_execution,
            risk_manager=risk_manager,
            metrics=execution_metrics,
            portfolio_manager=portfolio_manager,
            notifier=telegram_notifier,
        )

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
                stop_loss_pct=settings.trading_execution.stop_loss_pct,
                take_profit_pct=settings.trading_execution.take_profit_pct,
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
    strategy_classes, strategy_configs, aggregator_config, per_symbol_agg_config = (
        _resolve_strategy_config(settings.strategy)
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
        per_symbol_aggregator_config=per_symbol_agg_config,
    )

    # Lifecycle gate: warn if strategies aren't promoted to 'live' in DB
    # Non-blocking — logs warnings but does not prevent startup
    try:
        lifecycle_db = {
            "host": settings.database.get("host", "localhost"),
            "port": int(settings.database.get("port", 5432)),
            "user": settings.database.get("user", "postgres"),
            "password": settings.database.get("password", ""),
            "database": settings.database.get("name", "trading"),
        }
        async with LifecycleManager(lifecycle_db) as lifecycle:
            strategy_names = [cls.__name__ for cls in strategy_classes]
            all_live = await lifecycle.is_live(strategy_names)
            if not all_live:
                get_logger("lifecycle").warning(
                    "Some strategies not promoted to 'live' in DB — "
                    "run migration 004 and baseline_strategies.py to enable lifecycle gating"
                )
    except Exception as exc:
        get_logger("lifecycle").debug("Lifecycle check skipped (table may not exist): %s", exc)

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
        strategy_engine,
    ]

    if paper_executor:
        context_managers.append(paper_executor)
    if trading_executor:
        context_managers.append(trading_executor)
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

        # Start executor tasks
        paper_exit_task = None
        trading_task = None
        if trading_executor:
            trading_task = asyncio.create_task(trading_executor.run())

        overseer_task = None
        if overseer_agent is not None:
            overseer_task = asyncio.create_task(overseer_agent.run())

        # Start strategy engine - route signals to appropriate executor
        if paper_executor:
            # Paper mode: all signals (spot + futures) go to PaperExecutor
            paper_futures_symbols = set(paper_executor._config.futures_symbols)
            router_logger = get_logger("signal_router")

            async def on_signal_paper(signal: Signal) -> None:
                execution_metrics.record_signal(
                    signal.symbol,
                    signal.trading_mode,
                    signal.type.value,
                )
                await paper_executor.on_signal(signal)

                # Mirror to futures if applicable
                if signal.symbol in paper_futures_symbols and signal.trading_mode != "futures":
                    mirrored = Signal(
                        type=signal.type,
                        symbol=signal.symbol,
                        price=signal.price,
                        confidence=signal.confidence,
                        reason=signal.reason,
                        indicators=signal.indicators,
                        trading_mode="futures",
                    )
                    execution_metrics.record_signal(
                        mirrored.symbol,
                        mirrored.trading_mode,
                        mirrored.type.value,
                    )
                    await paper_executor.on_signal(mirrored)

            strategy_task = asyncio.create_task(
                strategy_engine.run(on_signal=on_signal_paper, on_tick=paper_executor.on_tick)
            )
            paper_exit_task = asyncio.create_task(paper_executor.run())
            futures_task = None
            futures_ingest_task = None

        elif futures_executor:
            futures_symbols = set(settings.futures.symbols)
            router_trace_symbols = {"BTCUSDT", "ETHUSDT"}
            router_logger = get_logger("signal_router")

            async def on_signal_router(signal: Signal) -> None:
                execution_metrics.record_signal(
                    signal.symbol,
                    signal.trading_mode,
                    signal.type.value,
                )

                if signal.trading_mode == "futures":
                    await futures_executor.on_signal(signal)
                else:
                    await trading_executor.on_signal(signal)

                    if signal.symbol in futures_symbols:
                        mirrored_signal = Signal(
                            type=signal.type,
                            symbol=signal.symbol,
                            price=signal.price,
                            confidence=signal.confidence,
                            reason=signal.reason,
                            indicators=signal.indicators,
                            trading_mode="futures",
                        )
                        execution_metrics.record_signal(
                            mirrored_signal.symbol,
                            mirrored_signal.trading_mode,
                            mirrored_signal.type.value,
                        )
                        if mirrored_signal.symbol in router_trace_symbols:
                            router_logger.info(
                                "Routed mirrored signal to futures: %s %s (%s)",
                                mirrored_signal.type.value,
                                mirrored_signal.symbol,
                                mirrored_signal.trading_mode,
                            )
                        await futures_executor.on_signal(mirrored_signal)

            strategy_task = asyncio.create_task(strategy_engine.run(on_signal=on_signal_router))

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
                        "rsi_14",
                    )
                )

            risk_summary = risk_manager.get_risk_summary()
            logger.info(
                "Startup diagnostics: db_connected=%s ohlcv_rows=%d indicator_rows=%d indicator_ready=%s risk=%s telegram_configured=%s executor=%s futures=%s ai=%s",
                writer.is_connected(),
                ohlcv_rows,
                indicator_rows,
                indicator_ready,
                risk_summary,
                telegram_notifier.is_configured(),
                "paper" if paper_executor else "live",
                (
                    "enabled"
                    if futures_executor
                    or (paper_executor and paper_executor._config.futures_symbols)
                    else "disabled"
                ),
                "enabled" if overseer_agent else "disabled",
            )

        await _log_startup_diagnostics()

        await stop_event.wait()

        # Cancel all tasks
        ingest_task.cancel()
        risk_task.cancel()
        indicator_task.cancel()
        trading_task.cancel()
        strategy_task.cancel()
        if paper_exit_task:
            paper_exit_task.cancel()
        if overseer_task:
            overseer_task.cancel()
        if futures_task:
            futures_task.cancel()
        if futures_ingest_task:
            futures_ingest_task.cancel()

        indicator_computer.stop()
        if paper_executor:
            paper_executor.stop()
        if trading_executor:
            trading_executor.stop()
        if overseer_agent:
            overseer_agent.stop()
        if futures_executor:
            futures_executor.stop()

        with contextlib.suppress(asyncio.CancelledError):
            await ingest_task
            await risk_task
            await indicator_task
            if trading_task:
                await trading_task
            await strategy_task
            if paper_exit_task:
                await paper_exit_task
            if overseer_task:
                await overseer_task
            if futures_task:
                await futures_task
            if futures_ingest_task:
                await futures_ingest_task

        await close_pool()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
