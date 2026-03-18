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
    # Per-symbol aggregator overrides: {"SOLUSDT": {"min_agreement": 1, "buy_threshold": 1.1, "sell_threshold": -1.0}, ...}
    per_symbol_aggregator_config: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    global_trend_filter_enabled: bool = True
    global_trend_filter_buffer_pct: float = 0.05  # Allow buys within 5% below EMA200


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
        self._primed_symbols: set[str] = set()
        self._aggregator = SignalAggregator(config.aggregator_config, config.default_trading_mode)
        self._mtf_timeframes: dict[str, str] | None = None

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

        if config.symbols:
            self._mtf_timeframes = self._validate_strategy_timeframes(
                self._strategies[config.symbols[0]]
            )
            if self._mtf_timeframes:
                self._logger.info(
                    "Runtime multi-timeframe mode enabled: entry=%s regime=%s",
                    self._mtf_timeframes.get("entry", config.timeframe),
                    self._mtf_timeframes.get("regime", "4h"),
                )

    async def __aenter__(self) -> StrategyEngine:
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
        market_context = await self._fetch_market_context()

        for symbol in self._config.symbols:
            indicator_rows = await self._fetch_indicator_rows(symbol)
            if indicator_rows is None:
                skipped += 1
                continue
            if symbol not in self._primed_symbols:
                previous_row = indicator_rows[0]
                for strategy in self._strategies.get(symbol, []):
                    try:
                        await strategy.evaluate(symbol, previous_row)
                    except Exception as exc:  # noqa: BLE001
                        self._logger.error(
                            "Strategy %s warm-start failed: %s",
                            strategy.get_name(),
                            exc,
                        )
                self._primed_symbols.add(symbol)

            indicators = indicator_rows[-1]

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
                # Get per-symbol aggregator config if available
                symbol_config = self._config.per_symbol_aggregator_config.get(symbol, {})

                final_signal = self._aggregator.aggregate(
                    symbol,
                    generated_signals,
                    ema_200=indicators.get("ema_200"),
                    symbol_config=symbol_config,
                    market_context=market_context,
                )
                if final_signal.type == SignalType.BUY and self._config.global_trend_filter_enabled:
                    price = indicators["close_price"]
                    ema_200 = indicators["ema_200"]
                    buffer_pct = self._config.global_trend_filter_buffer_pct
                    if ema_200 is not None and price < ema_200 * (1 - buffer_pct):
                        threshold = ema_200 * (1 - buffer_pct)
                        self._logger.info(
                            "Blocked by Global Trend Filter (Price < %.1f%% of EMA200) for %s: price=%.2f threshold=%.2f ema_200=%.2f",
                            buffer_pct * 100,
                            symbol,
                            price,
                            threshold,
                            ema_200,
                        )
                        final_signal = Signal(
                            type=SignalType.HOLD,
                            symbol=symbol,
                            price=price,
                            confidence=0.0,
                            reason=f"Blocked by Global Trend Filter (Price < {buffer_pct*100:.0f}% of EMA200)",
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
                    timeframe_seconds = _TIMEFRAME_SECONDS.get(self._runtime_timeframe)
                    if timeframe_seconds is None:
                        self._logger.warning(
                            "Cooldown disabled: unknown timeframe %s",
                            self._runtime_timeframe,
                        )
                    else:
                        cooldown_seconds = self._config.cooldown_candles * timeframe_seconds
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
                elif non_hold_votes:
                    self._logger.info(
                        "Consensus HOLD for %s: %s",
                        symbol,
                        final_signal.reason,
                    )
                else:
                    self._logger.debug(f"Consensus HOLD: {final_signal.reason}")

        self._logger.info(
            "Strategy cycle: evaluated=%d skipped=%d signals=%d",
            evaluated,
            skipped,
            signals_fired,
        )

    async def _fetch_market_context(self) -> Mapping[str, float | bool | str | None]:
        reference_symbol = self._aggregator.get_market_reference_symbol()
        if not reference_symbol:
            return {}
        rows = await self._reader.fetch_latest(reference_symbol, self._runtime_timeframe, limit=2)
        if len(rows) < 2:
            return {}

        previous_row = rows[0]
        latest_row = rows[-1]
        previous_price = previous_row.get("close_price")
        latest_price = latest_row.get("close_price")
        if not isinstance(previous_price, float) or not isinstance(latest_price, float):
            return {}
        if previous_price == 0.0:
            return {}

        change_pct = ((latest_price - previous_price) / previous_price) * 100.0
        return {
            "btc_symbol": reference_symbol,
            "btc_price": latest_price,
            "btc_ema_200": latest_row.get("ema_200"),
            "btc_change_pct": change_pct,
        }

    async def _fetch_indicator_rows(self, symbol: str) -> list[dict[str, float]] | None:
        """Fetch the two latest indicator rows for symbol from the database.

        Args:
            symbol: Trading pair symbol

        Returns:
            Two oldest-first rows for crossover-aware strategy evaluation.
            None if insufficient data (< 2 rows for crossover detection).
        """
        if self._mtf_timeframes:
            rows = await self._reader.fetch_latest_multi_timeframe(
                symbol,
                self._mtf_timeframes.get("entry", self._config.timeframe),
                self._mtf_timeframes.get("regime", "4h"),
                limit=2,
            )
        else:
            rows = await self._reader.fetch_latest(symbol, self._config.timeframe, limit=2)
        if len(rows) < 2:
            self._logger.info("Warming up %s: need 2 indicator rows, have %d", symbol, len(rows))
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
        return rows

    async def _fetch_indicators(self, symbol: str) -> dict[str, float] | None:
        """Fetch the latest indicator row for symbol from the database."""
        rows = await self._fetch_indicator_rows(symbol)
        if rows is None:
            return None
        return rows[-1]

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

    @property
    def _runtime_timeframe(self) -> str:
        """Timeframe used for live evaluation cadence and market context."""
        if self._mtf_timeframes:
            return self._mtf_timeframes.get("entry", self._config.timeframe)
        return self._config.timeframe

    @staticmethod
    def _get_required_timeframes(strategy: BaseStrategy) -> dict[str, str]:
        required = getattr(strategy, "REQUIRED_TIMEFRAMES", {})
        return required if required else {}

    def _validate_strategy_timeframes(
        self,
        strategies: list[BaseStrategy],
    ) -> dict[str, str] | None:
        mtf_requirements = [
            self._get_required_timeframes(strategy)
            for strategy in strategies
            if self._get_required_timeframes(strategy)
        ]

        if not mtf_requirements:
            return None

        if len(mtf_requirements) != len(strategies):
            raise ValueError(
                "Mixed multi-timeframe and single-timeframe runtime strategy sets are not supported"
            )

        first = mtf_requirements[0]
        for requirement in mtf_requirements[1:]:
            if requirement != first:
                raise ValueError(
                    "Runtime strategies must declare identical REQUIRED_TIMEFRAMES when using multi-timeframe mode"
                )

        return first
