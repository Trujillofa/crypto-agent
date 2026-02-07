from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.features.reader import IndicatorReader
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass(frozen=True)
class EngineConfig:
    """Strategy engine configuration."""

    symbols: list[str]
    database: Mapping[str, object] = field(default_factory=dict)
    timeframe: str = "1m"
    evaluation_interval_seconds: int = 60
    strategy_classes: list[type[BaseStrategy]] = field(default_factory=list)
    strategy_configs: list[Mapping[str, object] | None] = field(default_factory=list)


class StrategyEngine:
    """Main strategy engine that runs multiple strategies and produces signals.

    The engine:
    1. Manages multiple strategy instances
    2. Periodically fetches latest indicators
    3. Evaluates each strategy
    4. Produces trading signals for downstream execution
    """

    def __init__(
        self,
        config: EngineConfig,
        reader: IndicatorReader,
    ) -> None:
        self._config = config
        self._reader = reader
        self._logger = get_logger(self.__class__.__name__)
        self._strategies: dict[str, list[BaseStrategy]] = {}
        self._running = False

        # Initialize all strategies
        for symbol in config.symbols:
            self._strategies[symbol] = []
            for idx, strategy_class in enumerate(config.strategy_classes):
                # Get config for this strategy (if available)
                strategy_config = None
                if idx < len(config.strategy_configs):
                    strategy_config = config.strategy_configs[idx]

                strategy = strategy_class(strategy_config)
                self._strategies[symbol].append(strategy)

            self._logger.info(
                f"Initialized strategy: {strategy.get_name()} for {symbol}"
            )

    async def __aenter__(self) -> "StrategyEngine":
        self._running = True
        self._logger.info("StrategyEngine started")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._running = False
        self._logger.info("StrategyEngine stopped")

    async def run(
        self,
        on_signal: Callable[[Signal], Any] | None = None,
    ) -> None:
        """Main evaluation loop.

        Args:
            on_signal: Callback function to receive generated signals.
                Signature: (signal: Signal) -> None
        """
        if not self._strategies:
            self._logger.warning("No strategies initialized")
            return

        try:
            while self._running:
                await self._evaluate_all(on_signal)
                await asyncio.sleep(self._config.evaluation_interval_seconds)
        except asyncio.CancelledError:
            self._logger.info("StrategyEngine loop cancelled")

    async def _evaluate_all(
        self,
        on_signal: Callable[[Signal], Any] | None = None,
    ) -> None:
        """Evaluate all strategies for all symbols.

        Args:
            on_signal: Callback to receive signals
        """
        for symbol in self._config.symbols:
            # Fetch indicators from database
            indicators = await self._fetch_indicators(symbol)
            if indicators is None:
                # Warmup period - not enough data yet
                continue

            for strategy in self._strategies.get(symbol, []):
                try:
                    signal = await strategy.evaluate(symbol, indicators)
                    self._logger.info(f"{strategy.get_name()} generated {signal}")

                    # Only forward BUY/SELL signals (skip HOLD)
                    if signal.type != SignalType.HOLD and on_signal:
                        await on_signal(signal)

                except Exception as exc:  # noqa: BLE001
                    self._logger.error(f"Strategy {strategy.get_name()} failed: {exc}")

    async def _fetch_indicators(self, symbol: str) -> dict[str, float] | None:
        """Fetch latest indicators for symbol from database.

        Args:
            symbol: Trading pair symbol

        Returns:
            Dict with ema_12, ema_26, close_price keys.
            None if insufficient data (< 2 rows for crossover detection).
        """
        rows = await self._reader.fetch_latest(symbol, self._config.timeframe, limit=2)
        if len(rows) < 2:
            self._logger.info(
                "Warming up %s: need 2 indicator rows, have %d", symbol, len(rows)
            )
            return None
        return rows[-1]  # Return latest row (strategy handles crossover via state)

    def get_strategy_names(self) -> list[str]:
        """Get names of all active strategies."""
        names = []
        for symbol_strategies in self._strategies.values():
            for strategy in symbol_strategies:
                names.append(strategy.get_name())
        return names

    def stop(self) -> None:
        """Stop the engine loop."""
        self._running = False
