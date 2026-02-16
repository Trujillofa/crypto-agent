from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class RSIReversalStrategy(BaseStrategy):
    """RSI Mean-Reversion Strategy.

    Logic:
    - BUY when RSI crosses above oversold threshold (default 30) from below.
    - SELL when RSI crosses below overbought threshold (default 70) from above.
    - HOLD otherwise.

    Confidence scales with the extremity of the previous RSI value.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        self._oversold_threshold = float(self._config.get("oversold_threshold", 30.0))
        self._overbought_threshold = float(
            self._config.get("overbought_threshold", 70.0)
        )
        self._rsi_period = int(self._config.get("rsi_period", 14))

        # Track previous RSI values for crossover detection
        self._previous_rsi: dict[str, float] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal."""
        rsi_key = f"rsi_{self._rsi_period}"
        required_indicators = {rsi_key, "close_price", "ema_50"}

        for k in required_indicators:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        rsi_current = indicators[rsi_key]
        close_price = indicators["close_price"]
        ema_50 = indicators["ema_50"]

        if rsi_current is None or ema_50 is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"Waiting for RSI({self._rsi_period})/EMA50 data",
                indicators={},
            )

        rsi_previous = self._previous_rsi.get(symbol, rsi_current)

        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.0,
            reason=f"RSI: {rsi_current:.2f} (prev: {rsi_previous:.2f})",
            indicators={rsi_key: rsi_current, "close_price": close_price},
        )

        # Trend gate: only allow BUY in uptrend (price > EMA50),
        # SELL in downtrend (price < EMA50). Prevents buying bounces in downtrends.
        in_uptrend = close_price > ema_50
        in_downtrend = close_price < ema_50

        # Crossover Up (Bullish Reversal): Previous < 30 and Current >= 30
        if (
            in_uptrend
            and rsi_previous < self._oversold_threshold
            and rsi_current >= self._oversold_threshold
        ):
            depth = self._oversold_threshold - rsi_previous
            confidence = 0.5 + (depth / 30.0) * 0.5
            confidence = min(0.95, confidence)

            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=f"RSI({self._rsi_period}) crossed above {self._oversold_threshold} (prev: {rsi_previous:.2f}, trend UP)",
                indicators={rsi_key: rsi_current, "close_price": close_price},
            )

        # Crossover Down (Bearish Reversal): Previous > 70 and Current <= 70
        elif (
            in_downtrend
            and rsi_previous > self._overbought_threshold
            and rsi_current <= self._overbought_threshold
        ):
            excess = rsi_previous - self._overbought_threshold
            confidence = 0.5 + (excess / 30.0) * 0.5
            confidence = min(0.95, confidence)

            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=f"RSI({self._rsi_period}) crossed below {self._overbought_threshold} (prev: {rsi_previous:.2f}, trend DOWN)",
                indicators={rsi_key: rsi_current, "close_price": close_price},
            )

        self._previous_rsi[symbol] = rsi_current
        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")

        return signal

    def get_name(self) -> str:
        return f"RSIReversal({self._rsi_period}, {self._oversold_threshold}/{self._overbought_threshold})"
