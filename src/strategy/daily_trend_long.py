from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class DailyTrendLong(BaseStrategy):
    """Long-only daily trend filter: hold while close > SMA(window), flat otherwise."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)
        self._sma_window = int(self._config.get("sma_window", 50))
        self._confidence_threshold = float(self._config.get("confidence_threshold", 0.6))

    def _sma_key(self) -> str:
        return f"sma_{self._sma_window}"

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        sma_key = self._sma_key()
        required = {sma_key, "close_price"}
        missing = [key for key in required if key not in indicators]
        if missing:
            raise ValueError(f"Missing required indicators for {symbol}: {', '.join(missing)}")

        close_price = indicators["close_price"]
        sma_value = indicators[sma_key]
        if sma_value is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"Waiting for SMA({self._sma_window}) history",
                indicators={},
            )

        if close_price > sma_value:
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=self._confidence_threshold,
                reason=f"Daily close above SMA({self._sma_window})",
                indicators={
                    "close_price": close_price,
                    sma_key: sma_value,
                },
            )
        else:
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=self._confidence_threshold,
                reason=f"Daily close below SMA({self._sma_window}) — exit to flat",
                indicators={
                    "close_price": close_price,
                    sma_key: sma_value,
                },
            )

        self._logger.debug("%s generated %s for %s", self.get_name(), signal, symbol)
        return signal

    def get_name(self) -> str:
        return f"DailyTrendLong(SMA{self._sma_window})"
