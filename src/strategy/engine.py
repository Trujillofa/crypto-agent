from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.features.reader import IndicatorReader
from src.strategy.aggregator import SignalAggregator
from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}


@dataclass(frozen=True)
class EngineConfig:
    """Strategy engine configuration."""

    symbols: list[str]
    database: Mapping[str, object] = field(default_factory=dict)
    timeframe: str = "1m"
    evaluation_interval_seconds: int = 60
    default_trading_mode: str = "spot"
    cooldown_candles: int = 3
    strategy_classes: list[type[BaseStrategy]] = field(default_factory=list)
    strategy_configs: list[Mapping[str, object] | None] = field(default_factory=list)
    aggregator_config: Mapping[str, object] = field(default_factory=dict)


class StrategyEngine:
    """Main strategy engine that runs multiple strategies and produces signals."""

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
        self._last_signal_time: dict[str, float] = {}
        self._aggregator = SignalAggregator(
            config.aggregator_config, config.default_trading_mode
        )

        for symbol in config.symbols:
            self._strategies[symbol] = []
            for idx, strategy_class in enumerate(config.strategy_classes):
                strategy_config = None
                if idx < len(config.strategy_configs):
                    strategy_config = config.strategy_configs[idx]

                strategy = strategy_class(strategy_config)
                self._strategies[symbol].append(strategy)

            strategy_names = [s.get_name() for s in self._strategies[symbol]]
            self._logger.info(f"Initialized strategies for {symbol}: {strategy_names}")

    async def __aenter__(self) -> "StrategyEngine":
        self._running = True
        self._logger.info("StrategyEngine started")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._running = False
        self._logger.info("StrategyEngine stopped")

    async def run(
        self,
        on_signal: Callable[[Signal], Awaitable[Any]] | None = None,
        on_tick: Callable[[str, float, dict[str, float]], Awaitable[Any]] | None = None,
    ) -> None:
        """Main evaluation loop.

        Args:
            on_signal: Called when a BUY/SELL consensus signal fires.
            on_tick: Called every cycle for every symbol with (symbol, price, indicators).
                     Used to check SL/TP/trailing independently of signal generation.
        """
        if not self._strategies:
            self._logger.warning("No strategies initialized")
            return

        try:
            while self._running:
                await self._evaluate_all(on_signal, on_tick)
                await asyncio.sleep(self._config.evaluation_interval_seconds)
        except asyncio.CancelledError:
            self._logger.info("StrategyEngine loop cancelled")

    async def _evaluate_all(
        self,
        on_signal: Callable[[Signal], Awaitable[Any]] | None = None,
        on_tick: Callable[[str, float, dict[str, float]], Awaitable[Any]] | None = None,
    ) -> None:
        """Evaluate all strategies for all symbols and aggregate results."""
        evaluated = 0
        skipped = 0
        signals_fired = 0

        for symbol in self._config.symbols:
            indicators = await self._fetch_indicators(symbol)
            if indicators is None:
                skipped += 1
                continue

            evaluated += 1

            # Emit tick for position monitoring (SL/TP/trailing) every cycle
            if on_tick and "close_price" in indicators:
                try:
                    await on_tick(symbol, indicators["close_price"], indicators)
                except Exception as exc:  # noqa: BLE001
                    self._logger.error("on_tick failed for %s: %s", symbol, exc)

            generated_signals = []
            named_signals: list[tuple[str, Signal]] = []
            for strategy in self._strategies.get(symbol, []):
                try:
                    signal = await strategy.evaluate(symbol, indicators)
                    generated_signals.append(signal)
                    named_signals.append((strategy.get_name(), signal))
                    self._logger.debug(f"{strategy.get_name()} generated {signal}")
                except Exception as exc:  # noqa: BLE001
                    self._logger.error(f"Strategy {strategy.get_name()} failed: {exc}")

            # Log per-strategy attribution at INFO when any strategy votes non-HOLD
            non_hold_votes = [
                f"{name}→{sig.type.value}({sig.confidence:.2f})"
                for name, sig in named_signals
                if sig.type != SignalType.HOLD
            ]
            if non_hold_votes:
                self._logger.info(
                    "Strategy votes for %s: %s",
                    symbol,
                    " | ".join(non_hold_votes),
                )

            if generated_signals:
                final_signal = self._aggregator.aggregate(symbol, generated_signals)

                if final_signal.type == SignalType.BUY:
                    price = indicators["close_price"]
                    ema_200 = indicators["ema_200"]
                    if ema_200 is not None and price < ema_200:
                        self._logger.info(
                            "Blocked by Global Trend Filter (Price < EMA200) for %s: price=%.2f ema_200=%.2f",
                            symbol,
                            price,
                            ema_200,
                        )
                        final_signal = Signal(
                            type=SignalType.HOLD,
                            symbol=symbol,
                            price=price,
                            confidence=0.0,
                            reason="Blocked by Global Trend Filter (Price < EMA200)",
                            indicators=final_signal.indicators,
                            trading_mode=final_signal.trading_mode,
                        )

                # Inject atr_14 from indicators into the signal if not already present
                if "atr_14" in indicators and "atr_14" not in final_signal.indicators:
                    final_signal = Signal(
                        type=final_signal.type,
                        symbol=final_signal.symbol,
                        price=final_signal.price,
                        confidence=final_signal.confidence,
                        reason=final_signal.reason,
                        indicators={**final_signal.indicators, "atr_14": indicators["atr_14"]},
                        trading_mode=final_signal.trading_mode,
                    )

                if final_signal.type != SignalType.HOLD:
                    timeframe_seconds = _TIMEFRAME_SECONDS.get(self._config.timeframe)
                    if timeframe_seconds is None:
                        self._logger.warning(
                            "Cooldown disabled: unknown timeframe %s",
                            self._config.timeframe,
                        )
                    else:
                        cooldown_seconds = (
                            self._config.cooldown_candles * timeframe_seconds
                        )
                        last_signal_time = self._last_signal_time.get(symbol)
                        now = time.time()
                        if last_signal_time is not None:
                            elapsed = now - last_signal_time
                            if elapsed < cooldown_seconds:
                                remaining = cooldown_seconds - elapsed
                                self._logger.info(
                                    "Cooldown active for %s: %.0fs remaining",
                                    symbol,
                                    remaining,
                                )
                                continue

                        self._last_signal_time[symbol] = now

                    signals_fired += 1
                    self._logger.info(f"Consensus Signal: {final_signal}")
                    if on_signal:
                        await on_signal(final_signal)
                else:
                    self._logger.debug(f"Consensus HOLD: {final_signal.reason}")

        self._logger.info(
            "Strategy cycle: evaluated=%d skipped=%d signals=%d",
            evaluated,
            skipped,
            signals_fired,
        )

    async def _fetch_indicators(self, symbol: str) -> dict[str, float] | None:
        """Fetch latest indicators for symbol from database.

        Args:
            symbol: Trading pair symbol

        Returns:
            Dict with ema_12, ema_26, ema_200, close_price keys.
            None if insufficient data (< 2 rows for crossover detection).
        """
        rows = await self._reader.fetch_latest(symbol, self._config.timeframe, limit=2)
        if len(rows) < 2:
            self._logger.info(
                "Warming up %s: need 2 indicator rows, have %d", symbol, len(rows)
            )
            return None
        latest = rows[-1]
        required_keys = ("ema_12", "ema_26", "ema_200", "close_price")
        missing = [key for key in required_keys if key not in latest]
        if missing:
            self._logger.warning(
                "Indicators missing keys for %s: %s",
                symbol,
                ", ".join(missing),
            )
            return None
        return latest  # Return latest row (strategy handles crossover via state)

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
