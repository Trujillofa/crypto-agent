from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class CCIBreakoutStrategy(BaseStrategy):
    """CCI Breakout Strategy.

    Logic:
    - BUY when CCI crosses above +100 (overbought breakout into uptrend)
      AND price is above EMA50 (trend gate)
      AND ATR% >= minimum (volatility gate)
    - SELL when CCI crosses below -100 (oversold breakdown)
      AND ATR% >= minimum (volatility gate, no trend gate)
    - HOLD otherwise.

    Confidence scales with how far CCI is past the threshold.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        self._cci_buy_threshold = float(self._config.get("cci_buy_threshold", 100.0))
        self._cci_sell_threshold = float(self._config.get("cci_sell_threshold", -100.0))
        self._atr_min_pct = float(self._config.get("atr_min_pct", 0.005))

        self._previous_cci: dict[str, float | None] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal."""
        required_indicators = {"cci", "ema_50", "close_price", "atr_pct"}

        for k in required_indicators:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        cci_current = indicators["cci"]
        ema_50 = indicators["ema_50"]
        close_price = indicators["close_price"]
        atr_pct = indicators["atr_pct"]

        if cci_current is None or ema_50 is None or atr_pct is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for CCI/EMA50/ATR data",
                indicators={},
            )

        cci_previous = self._previous_cci.get(symbol, cci_current)
        self._previous_cci[symbol] = cci_current

        signal_type = SignalType.HOLD
        confidence = 0.0
        reason = f"CCI: {cci_current:.1f} (prev: {cci_previous:.1f})"

        # BUY: CCI crosses above +100, trend gate, volatility gate
        if cci_previous < self._cci_buy_threshold and cci_current >= self._cci_buy_threshold:
            if close_price <= ema_50:
                reason += f" - Counter-trend (price {close_price:.2f} < EMA50 {ema_50:.2f})"
            elif atr_pct < self._atr_min_pct:
                reason += f" - Low volatility (ATR%: {atr_pct:.4f} < {self._atr_min_pct})"
            else:
                signal_type = SignalType.BUY
                confidence = 0.5 + min(0.4, abs(cci_current - self._cci_buy_threshold) / 200.0)
                reason = (
                    f"CCI crossed above {self._cci_buy_threshold:.0f} "
                    f"(CCI: {cci_current:.1f}, price > EMA50)"
                )

        # SELL: CCI crosses below -100, volatility gate only (no trend gate)
        elif cci_previous > self._cci_sell_threshold and cci_current <= self._cci_sell_threshold:
            if atr_pct < self._atr_min_pct:
                reason += f" - Low volatility (ATR%: {atr_pct:.4f} < {self._atr_min_pct})"
            else:
                signal_type = SignalType.SELL
                confidence = 0.5 + min(0.4, abs(cci_current - self._cci_sell_threshold) / 200.0)
                reason = (
                    f"CCI crossed below {self._cci_sell_threshold:.0f} " f"(CCI: {cci_current:.1f})"
                )

        signal = Signal(
            type=signal_type,
            symbol=symbol,
            price=close_price,
            confidence=confidence,
            reason=reason,
            indicators={"cci": cci_current, "ema_50": ema_50, "atr_pct": atr_pct},
        )

        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")
        return signal

    def get_name(self) -> str:
        return f"CCIBreakout({self._cci_buy_threshold:.0f}/{self._cci_sell_threshold:.0f})"
