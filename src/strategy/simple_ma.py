from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class SimpleMACrossoverStrategy(BaseStrategy):
    """Simple Moving Average Crossover Strategy.

    Generates signals based on EMA crossovers:
    - BUY: EMA(12) crosses above EMA(26)
    - SELL: EMA(12) crosses below EMA(26)
    - HOLD: No crossover
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        # Strategy parameters from config or defaults
        self._ema_short_period = int(self._config.get("ema_short_period", 12))
        self._ema_long_period = int(self._config.get("ema_long_period", 26))
        self._confidence_threshold = float(
            self._config.get("confidence_threshold", 0.6)
        )

        # Track previous EMAs for crossover detection
        self._previous_ema_short: dict[str, float] = {}
        self._previous_ema_long: dict[str, float] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal.

        Args:
            symbol: Trading pair symbol
            indicators: Dictionary of latest indicator values

        Returns:
            Signal: Trading signal (BUY/SELL/HOLD) with metadata

        Raises:
            ValueError: If required indicators are missing
        """
        # Check for required indicators
        required_indicators = {
            f"ema_{self._ema_short_period}",
            f"ema_{self._ema_long_period}",
            "ema_50",
            "close_price",
        }

        missing = [k for k in required_indicators if k not in indicators]
        if missing:
            raise ValueError(
                f"Missing required indicators for {symbol}: {', '.join(missing)}"
            )

        # Get current values
        ema_short_current = indicators[f"ema_{self._ema_short_period}"]
        ema_long_current = indicators[f"ema_{self._ema_long_period}"]
        ema_50 = indicators["ema_50"]
        close_price = indicators["close_price"]

        # Check for None values (not enough data yet)
        if ema_short_current is None or ema_long_current is None or ema_50 is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"Waiting for EMA data ({self._ema_short_period}/{self._ema_long_period} periods)",
                indicators={},
            )

        # Get previous values for crossover detection
        ema_short_previous = self._previous_ema_short.get(symbol, ema_short_current)
        ema_long_previous = self._previous_ema_long.get(symbol, ema_long_current)

        # Detect crossovers
        # Crossover up: short EMA was below long, now above
        crossover_up = (
            ema_short_previous < ema_long_previous
            and ema_short_current > ema_long_current
        )

        # Crossover down: short EMA was above long, now below
        crossover_down = (
            ema_short_previous > ema_long_previous
            and ema_short_current < ema_long_current
        )

        # EMA(50) trend gate: only allow signals in trend direction
        in_uptrend = close_price > ema_50
        in_downtrend = close_price < ema_50

        # Generate signal based on crossover + trend gate
        if crossover_up and in_uptrend:
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=self._confidence_threshold,
                reason=f"EMA({self._ema_short_period}) crossed above EMA({self._ema_long_period}) [price > EMA50]",
                indicators={
                    f"ema_{self._ema_short_period}": ema_short_current,
                    f"ema_{self._ema_long_period}": ema_long_current,
                    "ema_50": ema_50,
                    "close_price": close_price,
                },
            )
        elif crossover_down and in_downtrend:
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=self._confidence_threshold,
                reason=f"EMA({self._ema_short_period}) crossed below EMA({self._ema_long_period}) [price < EMA50]",
                indicators={
                    f"ema_{self._ema_short_period}": ema_short_current,
                    f"ema_{self._ema_long_period}": ema_long_current,
                    "ema_50": ema_50,
                    "close_price": close_price,
                },
            )
        else:
            signal = Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"No EMA crossover (short: {ema_short_current:.2f}, long: {ema_long_current:.2f})",
                indicators={
                    f"ema_{self._ema_short_period}": ema_short_current,
                    f"ema_{self._ema_long_period}": ema_long_current,
                    "close_price": close_price,
                },
            )

        # Update previous values
        self._previous_ema_short[symbol] = ema_short_current
        self._previous_ema_long[symbol] = ema_long_current

        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")

        return signal

    def get_name(self) -> str:
        """Return the name of this strategy."""
        return f"SimpleMACrossover({self._ema_short_period}/{self._ema_long_period})"
