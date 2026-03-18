"""
MTF Continuation Strategy Template

This template demonstrates a CONTINUATION (reclaim) thesis instead of reversal:
- 4h regime for direction
- 1h reclaim/rally-failure for entries

Key difference from pullback template:
- Longs: Price reclaims VWAP/EMA from below (not pullback to oversold)
- Shorts: Price rejects below VWAP/EMA (not rally to overbought)
- RSI: >50 for longs, <50 for shorts (not extreme readings)
"""

from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class MTFContinuationTemplate(BaseStrategy):
    """Continuation-based MTF strategy.

    Thesis: Trade with the trend using reclaim/reject entries.
    - Long: 4h uptrend + 1h price reclaims VWAP/EMA from below
    - Short: 4h downtrend + 1h price rejects below VWAP/EMA

    This fixes the structural bias in pullback strategies where
    shorts fire on common overbought readings while longs require rare oversold.
    """

    REQUIRED_TIMEFRAMES = {
        "entry": "1h",
        "regime": "4h",
    }

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)

        # Regime thresholds
        self._regime_slope_threshold = float(
            self._config.get("regime_slope_threshold", 0.003)
        )  # Lowered from 0.005 for more trends
        self._trend_consistency_threshold = float(
            self._config.get("trend_consistency_threshold", 45.0)
        )  # Lowered from 50

        # Entry thresholds
        self._reclaim_threshold = float(
            self._config.get("reclaim_threshold", 0.005)
        )  # 0.5% reclaim distance
        self._ema_period = int(self._config.get("ema_period", 50))

        # RSI filter (use middle ground, not extremes)
        self._rsi_long_min = float(self._config.get("rsi_long_min", 45.0))  # > 45
        self._rsi_short_max = float(self._config.get("rsi_short_max", 55.0))  # < 55

        # Confidence
        self._confidence_boost = float(self._config.get("confidence_boost", 1.2))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        # Extract entry timeframe indicators
        close_price = indicators.get("close_price", 0.0)
        vwap = indicators.get("vwap", close_price)
        ema = indicators.get(f"ema_{self._ema_period}", close_price)
        rsi_14 = indicators.get("rsi_14", 50.0) or 50.0

        # Extract regime indicators
        ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0) or 0.0
        trend_consistency_4h = indicators.get("trend_consistency_4h", 50.0) or 50.0

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
        regime = self._classify_regime(ema_slope_4h, trend_consistency_4h)

        # Generate signal
        if regime == "uptrend":
            return self._generate_long(
                symbol, close_price, vwap, ema, rsi_14, ema_slope_4h, indicators
            )
        elif regime == "downtrend":
            return self._generate_short(
                symbol, close_price, vwap, ema, rsi_14, ema_slope_4h, indicators
            )
        else:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"No trend: slope={ema_slope_4h:.5f}, consistency={trend_consistency_4h:.1f}",
                indicators={"regime": regime},
            )

    def _classify_regime(self, ema_slope: float, trend_consistency: float) -> str:
        """Classify market regime based on 4h indicators."""
        is_trending = (
            abs(ema_slope) > self._regime_slope_threshold
            and trend_consistency > self._trend_consistency_threshold
        )

        if is_trending:
            return "uptrend" if ema_slope > 0 else "downtrend"
        return "neutral"

    def _generate_long(
        self,
        symbol: str,
        close_price: float,
        vwap: float,
        ema: float,
        rsi: float,
        ema_slope: float,
        indicators: dict,
    ) -> Signal:
        """Long entry: price reclaims VWAP/EMA from below in uptrend.

        Entry conditions:
        - Uptrend regime (4h)
        - Price above VWAP and EMA (reclaiming)
        - RSI in favorable zone (> 45)
        """
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema = (close_price - ema) / ema if ema != 0 else 0

        # Reclaim: price above both VWAP and EMA
        is_reclaiming = price_vs_vwap > self._reclaim_threshold and price_vs_ema > 0
        rsi_favorable = rsi > self._rsi_long_min

        if is_reclaiming and rsi_favorable:
            confidence = 0.7 * self._confidence_boost
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=min(confidence, 0.95),
                reason=f"Uptrend reclaim: vs_vwap={price_vs_vwap:.4f}, vs_ema={price_vs_ema:.4f}, rsi={rsi:.1f}",
                indicators={
                    "regime": "uptrend",
                    "price_vs_vwap": price_vs_vwap,
                    "price_vs_ema": price_vs_ema,
                    "rsi": rsi,
                    "ema_slope_4h": ema_slope,
                },
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"Uptrend waiting for reclaim: vs_vwap={price_vs_vwap:.4f}, rsi={rsi:.1f}",
            indicators={"regime": "uptrend"},
        )

    def _generate_short(
        self,
        symbol: str,
        close_price: float,
        vwap: float,
        ema: float,
        rsi: float,
        ema_slope: float,
        indicators: dict,
    ) -> Signal:
        """Short entry: price rejects below VWAP/EMA in downtrend.

        Entry conditions:
        - Downtrend regime (4h)
        - Price below VWAP and EMA (rejecting)
        - RSI in favorable zone (< 55)
        """
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema = (close_price - ema) / ema if ema != 0 else 0

        # Reject: price below both VWAP and EMA
        is_rejecting = price_vs_vwap < -self._reclaim_threshold and price_vs_ema < 0
        rsi_favorable = rsi < self._rsi_short_max

        if is_rejecting and rsi_favorable:
            confidence = 0.7 * self._confidence_boost
            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=min(confidence, 0.95),
                reason=f"Downtrend reject: vs_vwap={price_vs_vwap:.4f}, vs_ema={price_vs_ema:.4f}, rsi={rsi:.1f}",
                indicators={
                    "regime": "downtrend",
                    "price_vs_vwap": price_vs_vwap,
                    "price_vs_ema": price_vs_ema,
                    "rsi": rsi,
                    "ema_slope_4h": ema_slope,
                },
                trading_mode="futures",
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"Downtrend waiting for reject: vs_vwap={price_vs_vwap:.4f}, rsi={rsi:.1f}",
            indicators={"regime": "downtrend"},
        )

    def get_name(self) -> str:
        return "MTFContinuationTemplate"
