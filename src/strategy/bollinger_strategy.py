from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class BollingerBounceStrategy(BaseStrategy):
    """Bollinger Band Mean Reversion Strategy."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        self._band_dist_threshold = float(
            self._config.get("band_distance_threshold", 0.0)
        )
        self._rsi_oversold = float(self._config.get("rsi_oversold", 30.0))
        self._rsi_overbought = float(self._config.get("rsi_overbought", 70.0))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal."""
        required_indicators = {
            "bb_upper_dist",
            "bb_lower_dist",
            "rsi_14",
            "close_price",
        }

        for k in required_indicators:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        bb_upper_dist = indicators["bb_upper_dist"]
        bb_lower_dist = indicators["bb_lower_dist"]
        rsi = indicators["rsi_14"]
        close_price = indicators["close_price"]
        if bb_upper_dist is None or bb_lower_dist is None or rsi is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for Bollinger/RSI data",
                indicators={},
            )

        signal_type = SignalType.HOLD
        confidence = 0.0
        reason = (
            f"BB Dist (L/U): {bb_lower_dist:.4f}/{bb_upper_dist:.4f}, RSI: {rsi:.2f}"
        )

        if bb_lower_dist <= self._band_dist_threshold:
            if rsi < self._rsi_oversold:
                signal_type = SignalType.BUY
                rsi_diff = max(0.0, self._rsi_oversold - rsi)
                confidence = 0.5 + min(0.5, rsi_diff * 0.05)
                reason = (
                    f"Price at Lower Band (Dist: {bb_lower_dist:.4f}) "
                    f"& RSI Oversold ({rsi:.2f}, Conf: {confidence:.2f})"
                )
            else:
                reason += " - RSI not oversold"

        elif bb_upper_dist <= self._band_dist_threshold:
            if rsi > self._rsi_overbought:
                signal_type = SignalType.SELL
                rsi_diff = max(0.0, rsi - self._rsi_overbought)
                confidence = 0.5 + min(0.5, rsi_diff * 0.05)
                reason = (
                    f"Price at Upper Band (Dist: {bb_upper_dist:.4f}) "
                    f"& RSI Overbought ({rsi:.2f}, Conf: {confidence:.2f})"
                )
            else:
                reason += " - RSI not overbought"

        signal = Signal(
            type=signal_type,
            symbol=symbol,
            price=close_price,
            confidence=confidence,
            reason=reason,
            indicators={
                "bb_upper_dist": bb_upper_dist,
                "bb_lower_dist": bb_lower_dist,
                "rsi_14": rsi,
                "close_price": close_price,
            },
        )

        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")
        return signal

    def get_name(self) -> str:
        return "BollingerBounce"
