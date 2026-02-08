from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class MomentumStrategy(BaseStrategy):
    """Trend-Following Momentum Strategy."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        self._rsi_buy_threshold = float(self._config.get("rsi_buy_threshold", 50.0))
        self._rsi_sell_threshold = float(self._config.get("rsi_sell_threshold", 50.0))
        self._rsi_max_entry = float(self._config.get("rsi_max_entry", 70.0))
        self._rsi_min_entry = float(self._config.get("rsi_min_entry", 30.0))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal."""
        required_indicators = {
            "rsi_14",
            "ema_50",
            "close_price",
        }

        for k in required_indicators:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        rsi = indicators["rsi_14"]
        ema_50 = indicators["ema_50"]
        close_price = indicators["close_price"]

        if rsi is None or ema_50 is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for RSI/EMA data",
                indicators={},
            )

        signal_type = SignalType.HOLD
        confidence = 0.0
        reason = f"RSI: {rsi:.2f}, Price vs EMA50: {close_price:.2f}/{ema_50:.2f}"

        if close_price > ema_50:
            if rsi > self._rsi_buy_threshold:
                if rsi < self._rsi_max_entry:
                    signal_type = SignalType.BUY
                    confidence = 1.0
                    reason = (
                        f"Trend UP (Price > EMA50) & Momentum UP (RSI {rsi:.2f} > {self._rsi_buy_threshold})"
                    )
                else:
                    reason += " - RSI too high for entry"
            else:
                reason += " - RSI weak"

        elif close_price < ema_50:
            if rsi < self._rsi_sell_threshold:
                if rsi > self._rsi_min_entry:
                    signal_type = SignalType.SELL
                    confidence = 1.0
                    reason = f"Trend DOWN (Price < EMA50) & Momentum DOWN (RSI {rsi:.2f} < {self._rsi_sell_threshold})"
                else:
                    reason += " - RSI too low for entry"
            else:
                reason += " - RSI weak"

        signal = Signal(
            type=signal_type,
            symbol=symbol,
            price=close_price,
            confidence=confidence,
            reason=reason,
            indicators={
                "rsi_14": rsi,
                "ema_50": ema_50,
                "close_price": close_price,
            },
        )

        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")
        return signal

    def get_name(self) -> str:
        return "MomentumTrend"
