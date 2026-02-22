from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class VWAPReversionStrategy(BaseStrategy):
    """VWAP Mean-Reversion Strategy.

    Logic:
    - BUY when price is below VWAP by more than (vwap_atr_multiplier × ATR14)
      AND RSI14 < rsi_oversold (default 40) — confirming oversold condition
    - SELL when price is above VWAP by more than (vwap_atr_multiplier × ATR14)
      AND RSI14 > rsi_overbought (default 60) — confirming overbought condition
    - HOLD otherwise.

    Confidence scales with how far price deviation exceeds the threshold.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        self._vwap_atr_multiplier = float(self._config.get("vwap_atr_multiplier", 1.5))
        self._rsi_oversold = float(self._config.get("rsi_oversold", 40.0))
        self._rsi_overbought = float(self._config.get("rsi_overbought", 60.0))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal."""
        required_indicators = {"vwap", "close_price", "atr_14", "rsi_14"}

        for k in required_indicators:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        vwap = indicators["vwap"]
        close_price = indicators["close_price"]
        atr_14 = indicators["atr_14"]
        rsi_14 = indicators["rsi_14"]

        if vwap is None or atr_14 is None or rsi_14 is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for VWAP/ATR14/RSI14 data",
                indicators={},
            )

        deviation = abs(close_price - vwap)
        threshold = self._vwap_atr_multiplier * atr_14

        signal_type = SignalType.HOLD
        confidence = 0.0
        reason = (
            f"VWAP: {vwap:.4f}, price: {close_price:.4f}, "
            f"dev: {deviation:.4f}, threshold: {threshold:.4f}, RSI14: {rsi_14:.1f}"
        )

        if deviation <= threshold:
            reason += " - Deviation below threshold"
        elif vwap > close_price:
            # Price is below VWAP — potential BUY (reversion up)
            if rsi_14 < self._rsi_oversold:
                signal_type = SignalType.BUY
                excess_ratio = deviation / threshold - 1.0
                confidence = 0.5 + min(0.4, excess_ratio * 0.4)
                reason = (
                    f"Price {deviation:.4f} below VWAP (threshold {threshold:.4f}), "
                    f"RSI14: {rsi_14:.1f} < {self._rsi_oversold}"
                )
            else:
                reason += f" - RSI14 {rsi_14:.1f} not oversold (>= {self._rsi_oversold})"
        else:
            # Price is above VWAP — potential SELL (reversion down)
            if rsi_14 > self._rsi_overbought:
                signal_type = SignalType.SELL
                excess_ratio = deviation / threshold - 1.0
                confidence = 0.5 + min(0.4, excess_ratio * 0.4)
                reason = (
                    f"Price {deviation:.4f} above VWAP (threshold {threshold:.4f}), "
                    f"RSI14: {rsi_14:.1f} > {self._rsi_overbought}"
                )
            else:
                reason += f" - RSI14 {rsi_14:.1f} not overbought (<= {self._rsi_overbought})"

        signal = Signal(
            type=signal_type,
            symbol=symbol,
            price=close_price,
            confidence=confidence,
            reason=reason,
            indicators={"vwap": vwap, "atr_14": atr_14, "rsi_14": rsi_14},
        )

        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")
        return signal

    def get_name(self) -> str:
        return f"VWAPReversion({self._vwap_atr_multiplier}x, RSI {self._rsi_oversold}/{self._rsi_overbought})"
