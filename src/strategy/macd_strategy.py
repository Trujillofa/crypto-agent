from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class MACDHistogramStrategy(BaseStrategy):
    """MACD Histogram Momentum Strategy.

    Logic:
    - BUY when MACD Histogram crosses above zero (momentum shifts bullish).
    - SELL when MACD Histogram crosses below zero (momentum shifts bearish).
    - Optional: Filter signals in low volatility (low ATR) to avoid chop.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        self._min_hist_threshold = float(self._config.get("min_histogram_threshold", 0.0))
        self._use_atr_filter = bool(self._config.get("use_atr_filter", True))
        self._atr_min_pct = float(self._config.get("atr_min_pct", 0.005))  # 0.5%

        self._previous_hist: dict[str, float] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal."""
        required_indicators = {"macd_hist", "ema_50", "close_price"}
        if self._use_atr_filter:
            required_indicators.add("atr_pct")

        for k in required_indicators:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        hist_current = indicators["macd_hist"]
        ema_50 = indicators["ema_50"]
        close_price = indicators["close_price"]
        atr_pct = indicators["atr_pct"] if self._use_atr_filter else None

        if hist_current is None or ema_50 is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for MACD data",
                indicators={},
            )

        # Determine if we should filter based on ATR
        is_low_volatility = False
        if self._use_atr_filter and atr_pct is not None:
            if atr_pct < self._atr_min_pct:
                is_low_volatility = True

        hist_previous = self._previous_hist.get(symbol, hist_current)

        signal_type = SignalType.HOLD
        confidence = 0.0
        reason = f"MACD Hist: {hist_current:.4f} (prev: {hist_previous:.4f})"

        # Calculate confidence scaling factor based on histogram strength relative to price
        # We assume a histogram value > 0.2% of price indicates strong momentum
        hist_ratio = abs(hist_current) / close_price
        strength_bonus = min(0.5, hist_ratio * 250)  # 0.002 * 250 = 0.5
        base_confidence = 0.5

        # EMA(50) trend gate: only gate BUY (avoid buying into downtrends)
        # SELL has no gate — bearish crossovers fire regardless of trend direction
        in_uptrend = close_price > ema_50

        if hist_previous < 0 and hist_current > 0:
            if abs(hist_current) >= self._min_hist_threshold:
                if not is_low_volatility:
                    if in_uptrend:
                        signal_type = SignalType.BUY
                        confidence = base_confidence + strength_bonus
                        reason = f"Bullish MACD Crossover (Hist: {hist_current:.4f}, Conf: {confidence:.2f}) [price > EMA50]"
                    else:
                        reason += " - Counter-trend (price < EMA50)"
                else:
                    reason += " - Low Volatility"
            else:
                reason += " - Below Threshold"

        elif hist_previous > 0 and hist_current < 0:
            if abs(hist_current) >= self._min_hist_threshold:
                if not is_low_volatility:
                    signal_type = SignalType.SELL
                    confidence = base_confidence + strength_bonus
                    reason = (
                        f"Bearish MACD Crossover (Hist: {hist_current:.4f}, Conf: {confidence:.2f})"
                    )
                else:
                    reason += " - Low Volatility"
            else:
                reason += " - Below Threshold"

        signal = Signal(
            type=signal_type,
            symbol=symbol,
            price=close_price,
            confidence=confidence,
            reason=reason,
            indicators={"macd_hist": hist_current, "close_price": close_price},
        )

        self._previous_hist[symbol] = hist_current
        self._logger.debug(f"{self.get_name()} generated {signal} for {symbol}")
        return signal

    def get_name(self) -> str:
        return "MACDHistogram"
