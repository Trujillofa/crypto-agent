"""
MTF Breakout/Expansion Strategy Template

This template adds a market-structure filter to the continuation thesis:
- 4h breakout/expansion regime (volatility rising + price near high)
- 1h reclaim entry (simple, no extreme RSI)

Key additions:
- volatility_percentile threshold (expansion only)
- breakout filter (price near 4h high)
- simpler 1h entry (reclaim only)
"""

from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class MTFBreakoutExpansionTemplate(BaseStrategy):
    """Breakout/Expansion MTF strategy.

    Thesis: Trade breakouts in expansion phases.
    - 4h: Volatility expansion + price near 4h high = breakout regime
    - 1h: Reclaim VWAP/EMA as entry trigger

    This adds a market-structure filter to avoid trading in chop.
    """

    REQUIRED_TIMEFRAMES = {
        "entry": "1h",
        "regime": "4h",
    }

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)

        # 4h regime thresholds
        self._volatility_threshold = float(
            self._config.get("volatility_threshold", 55.0)
        )  # Require expansion phase
        self._breakout_threshold = float(
            self._config.get("breakout_threshold", 0.02)
        )  # Within 2% of 4h high
        self._trend_slope_threshold = float(
            self._config.get("trend_slope_threshold", 0.002)
        )  # Direction confirmation

        # 1h entry thresholds
        self._reclaim_threshold = float(
            self._config.get("reclaim_threshold", 0.003)
        )  # Smaller reclaim for breakouts
        self._ema_period = int(self._config.get("ema_period", 50))

        # Confidence
        self._confidence_boost = float(self._config.get("confidence_boost", 1.2))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        # Extract 1h indicators
        close_price = indicators.get("close_price", 0.0)
        vwap = indicators.get("vwap", close_price)
        ema = indicators.get(f"ema_{self._ema_period}", close_price)

        # Extract 4h regime indicators
        ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0) or 0.0
        volatility_4h = indicators.get("volatility_percentile_4h", 50.0) or 50.0
        # Need high from 4h - approximate with close for now, or add to indicators
        close_4h = indicators.get("close_price_4h", close_price)

        if close_price == 0.0:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=0.0,
                confidence=0.0,
                reason="Missing close price",
                indicators={},
            )

        # Classify regime
        regime = self._classify_regime(
            ema_slope_4h, volatility_4h, close_price, close_4h or close_price
        )

        # Generate signal
        if regime == "bullish_breakout":
            return self._generate_long(symbol, close_price, vwap, ema, ema_slope_4h, volatility_4h)
        elif regime == "bearish_breakout":
            return self._generate_short(symbol, close_price, vwap, ema, ema_slope_4h, volatility_4h)
        else:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"Non-breakout: vol={volatility_4h:.1f}, slope={ema_slope_4h:.5f}",
                indicators={"regime": regime},
            )

    def _classify_regime(
        self,
        ema_slope: float,
        volatility: float,
        close_price: float,
        regime_close: float,
    ) -> str:
        """Classify breakout/expansion regime.

        Requires ALL:
        - Volatility above threshold (expansion)
        - Price within threshold of regime high (breakout positioning)
        - Slope confirms direction
        """
        # Expansion phase required
        if volatility < self._volatility_threshold:
            return "neutral"

        # Breakout positioning: close near regime high
        if regime_close > 0:
            distance_from_high = (regime_close - close_price) / regime_close
        else:
            distance_from_high = 0

        is_near_high = distance_from_high < self._breakout_threshold

        # Direction from slope
        if ema_slope > self._trend_slope_threshold and is_near_high:
            return "bullish_breakout"
        elif ema_slope < -self._trend_slope_threshold and is_near_high:
            return "bearish_breakout"

        return "neutral"

    def _generate_long(
        self,
        symbol: str,
        close_price: float,
        vwap: float,
        ema: float,
        ema_slope: float,
        volatility: float,
    ) -> Signal:
        """Long entry: breakout regime + reclaim from below."""
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema = (close_price - ema) / ema if ema != 0 else 0

        # Reclaim: price reclaiming VWAP or EMA from below
        is_reclaiming = price_vs_vwap > self._reclaim_threshold or price_vs_ema > 0

        if is_reclaiming:
            confidence = 0.75 * self._confidence_boost
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=min(confidence, 0.95),
                reason=f"Breakout long: vs_vwap={price_vs_vwap:.4f}, vol={volatility:.1f}",
                indicators={
                    "regime": "bullish_breakout",
                    "volatility": volatility,
                    "ema_slope": ema_slope,
                },
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"Breakout waiting for reclaim: vs_vwap={price_vs_vwap:.4f}",
            indicators={"regime": "bullish_breakout"},
        )

    def _generate_short(
        self,
        symbol: str,
        close_price: float,
        vwap: float,
        ema: float,
        ema_slope: float,
        volatility: float,
    ) -> Signal:
        """Short entry: breakdown regime + reject from above."""
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema = (close_price - ema) / ema if ema != 0 else 0

        # Reject: price rejecting VWAP or EMA from above
        is_rejecting = price_vs_vwap < -self._reclaim_threshold or price_vs_ema < 0

        if is_rejecting:
            confidence = 0.75 * self._confidence_boost
            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=min(confidence, 0.95),
                reason=f"Breakdown short: vs_vwap={price_vs_vwap:.4f}, vol={volatility:.1f}",
                indicators={
                    "regime": "bearish_breakout",
                    "volatility": volatility,
                    "ema_slope": ema_slope,
                },
                trading_mode="futures",
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"Breakdown waiting for reject: vs_vwap={price_vs_vwap:.4f}",
            indicators={"regime": "bearish_breakout"},
        )

    def get_name(self) -> str:
        return "MTFBreakoutExpansionTemplate"
