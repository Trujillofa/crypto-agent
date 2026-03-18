from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class MultiTimeframeRegimeRouter(BaseStrategy):
    """Multi-timeframe regime router: 4h regime classification, 1h entry execution.

    Architecture:
    - 4h timeframe: Classify market regime (trending/ranging/uncertain)
    - 1h timeframe: Execute pullback-and-reclaim entries
    - Armed entry window: When 4h regime turns trending, allow entries for next N 1h bars
    - Multiple patterns: VWAP reclaim, EMA50 reclaim, deeper pullback variants

    This produces significantly more trade opportunities while maintaining quality.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)

        # 4h Regime classification thresholds
        self._trend_strength_threshold = float(self._config.get("trend_strength_threshold", 0.005))
        self._volatility_percentile_threshold = float(
            self._config.get("volatility_percentile_threshold", 60.0)
        )
        self._trend_consistency_threshold = float(
            self._config.get("trend_consistency_threshold", 60.0)
        )
        self._rsi_slope_threshold = float(self._config.get("rsi_slope_threshold", 5.0))

        # Entry window settings
        self._entry_window_bars = int(
            self._config.get("entry_window_bars", 6)
        )  # 6 1h bars = 6 hours
        self._pullback_threshold = float(self._config.get("pullback_threshold", 0.01))  # 1%
        self._reclaim_threshold = float(self._config.get("reclaim_threshold", 0.005))  # 0.5%

        # Entry zone settings
        self._entry_zone_pct = float(self._config.get("entry_zone_pct", 0.01))  # 1%
        self._deep_pullback_pct = float(self._config.get("deep_pullback_pct", 0.02))  # 2%
        self._rsi_oversold = float(self._config.get("rsi_oversold", 45.0))
        self._rsi_overbought = float(self._config.get("rsi_overbought", 55.0))

        # Confidence multipliers
        self._trending_confidence = float(self._config.get("trending_confidence", 1.2))
        self._ranging_confidence = float(self._config.get("ranging_confidence", 1.0))
        self._uncertain_confidence = float(self._config.get("uncertain_confidence", 0.0))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate using 1h indicators with 4h regime classification."""

        # Extract 1h indicators for entry timing
        close_price = indicators.get("close_price", 0.0)
        rsi_14 = indicators.get("rsi_14", 50.0) or 50.0
        rsi_slope = indicators.get("rsi_slope", 0.0) or 0.0

        # Extract 4h regime indicators (passed from higher timeframe)
        ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0) or 0.0
        vol_pct_4h = indicators.get("volatility_percentile_4h", 50.0) or 50.0
        trend_consistency_4h = indicators.get("trend_consistency_4h", 50.0) or 50.0
        rsi_slope_4h = indicators.get("rsi_slope_4h", 0.0) or 0.0

        # Entry anchors (1h)
        vwap = indicators.get("vwap", close_price)
        ema_50 = indicators.get("ema_50", close_price)
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema50 = (close_price - ema_50) / ema_50 if ema_50 != 0 else 0

        if close_price == 0.0:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=0.0,
                confidence=0.0,
                reason="Missing close price",
                indicators={},
            )

        # Classify 4h regime
        regime = self._classify_regime_4h(
            ema_slope_4h, vol_pct_4h, trend_consistency_4h, rsi_slope_4h
        )

        # Generate signal based on regime using 1h indicators
        if regime == "trending_up":
            return self._generate_long_entry(
                symbol,
                close_price,
                price_vs_vwap,
                price_vs_ema50,
                rsi_14,
                rsi_slope,
                ema_slope_4h,
                indicators,
            )
        elif regime == "trending_down":
            return self._generate_short_entry(
                symbol,
                close_price,
                price_vs_vwap,
                price_vs_ema50,
                rsi_14,
                rsi_slope,
                ema_slope_4h,
                indicators,
            )
        elif regime == "ranging":
            return self._generate_range_signal(symbol, close_price, indicators)
        else:  # uncertain
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=self._uncertain_confidence,
                reason=f"4h regime uncertain: vol_pct={vol_pct_4h:.1f}",
                indicators={"regime_4h": regime},
            )

    def _classify_regime_4h(
        self,
        ema_slope: float,
        vol_pct: float,
        trend_consistency: float,
        rsi_slope: float,
    ) -> str:
        """Classify market regime based on 4h indicators."""
        # Trending: strong slope + high consistency + elevated volatility
        is_trending = (
            abs(ema_slope) > self._trend_strength_threshold
            and trend_consistency > self._trend_consistency_threshold
            and vol_pct > self._volatility_percentile_threshold
        )

        # Ranging: low slope + moderate consistency + low volatility
        is_ranging = (
            abs(ema_slope) <= self._trend_strength_threshold * 0.5
            and trend_consistency < self._trend_consistency_threshold
            and vol_pct <= self._volatility_percentile_threshold * 0.7
        )

        # Momentum confirmation for trending with direction
        if is_trending:
            momentum_aligned = ema_slope > 0 and rsi_slope > 0
            if momentum_aligned:
                return "trending_up"
            momentum_aligned = ema_slope < 0 and rsi_slope < 0
            if momentum_aligned:
                return "trending_down"

        if is_ranging:
            return "ranging"

        return "uncertain"

    def _generate_long_entry(
        self,
        symbol: str,
        close_price: float,
        price_vs_vwap: float,
        price_vs_ema50: float,
        rsi_14: float,
        rsi_slope: float,
        ema_slope_4h: float,
        indicators: dict[str, float],
    ) -> Signal:
        """Generate long entry signal with multiple patterns."""

        # Pattern 1: VWAP Pullback Entry
        # Price near or below VWAP in uptrend (RSI oversold)
        vwap_entry = (
            -self._entry_zone_pct * 2 <= price_vs_vwap <= self._entry_zone_pct
            and rsi_14 < self._rsi_oversold
            and rsi_slope > -2
        )

        # Pattern 2: EMA50 Pullback Entry
        ema50_entry = (
            -self._entry_zone_pct * 2 <= price_vs_ema50 <= self._entry_zone_pct
            and rsi_14 < self._rsi_oversold
            and rsi_slope > -2
        )

        # Pattern 3: Deep VWAP Pullback (stronger mean reversion)
        deep_vwap_entry = (
            -self._deep_pullback_pct * 1.5 <= price_vs_vwap <= -self._deep_pullback_pct * 0.5
            and rsi_14 < self._rsi_oversold - 5  # More oversold
            and rsi_slope > -3
        )

        # Pattern 4: Deep EMA50 Pullback
        deep_ema50_entry = (
            -self._deep_pullback_pct * 1.5 <= price_vs_ema50 <= -self._deep_pullback_pct * 0.5
            and rsi_14 < self._rsi_oversold - 5
            and rsi_slope > -3
        )

        if vwap_entry or ema50_entry or deep_vwap_entry or deep_ema50_entry:
            # Determine which pattern triggered
            if vwap_entry:
                pattern = "VWAP"
                confidence = 0.8
            elif ema50_entry:
                pattern = "EMA50"
                confidence = 0.75
            elif deep_vwap_entry:
                pattern = "DeepVWAP"
                confidence = 0.85
            else:
                pattern = "DeepEMA50"
                confidence = 0.8

            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence * self._trending_confidence,
                reason=f"4h Uptrend {pattern} pullback: vs_vwap={price_vs_vwap:.4f}, "
                f"vs_ema50={price_vs_ema50:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime_4h": "trending_up",
                    "pattern": pattern,
                    "ema_slope_4h": ema_slope_4h,
                    "price_vs_vwap": price_vs_vwap,
                    "price_vs_ema50": price_vs_ema50,
                    "rsi_14": rsi_14,
                },
                trading_mode="futures" if self._config.get("futures_enabled") else "spot",
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"4h Uptrend waiting for pullback: vs_vwap={price_vs_vwap:.4f}, rsi={rsi_14:.1f}",
            indicators={
                "regime_4h": "trending_up",
                "price_vs_vwap": price_vs_vwap,
                "price_vs_ema50": price_vs_ema50,
            },
        )

    def _generate_short_entry(
        self,
        symbol: str,
        close_price: float,
        price_vs_vwap: float,
        price_vs_ema50: float,
        rsi_14: float,
        rsi_slope: float,
        ema_slope_4h: float,
        indicators: dict[str, float],
    ) -> Signal:
        """Generate short entry signal with multiple patterns."""

        # Pattern 1: VWAP Rally Entry
        vwap_entry = (
            -self._entry_zone_pct <= price_vs_vwap <= self._entry_zone_pct * 2
            and rsi_14 > self._rsi_overbought
            and rsi_slope < 2
        )

        # Pattern 2: EMA50 Rally Entry
        ema50_entry = (
            -self._entry_zone_pct <= price_vs_ema50 <= self._entry_zone_pct * 2
            and rsi_14 > self._rsi_overbought
            and rsi_slope < 2
        )

        # Pattern 3: Deep VWAP Rally
        deep_vwap_entry = (
            self._deep_pullback_pct * 0.5 <= price_vs_vwap <= self._deep_pullback_pct * 1.5
            and rsi_14 > self._rsi_overbought + 5
            and rsi_slope < 3
        )

        # Pattern 4: Deep EMA50 Rally
        deep_ema50_entry = (
            self._deep_pullback_pct * 0.5 <= price_vs_ema50 <= self._deep_pullback_pct * 1.5
            and rsi_14 > self._rsi_overbought + 5
            and rsi_slope < 3
        )

        if vwap_entry or ema50_entry or deep_vwap_entry or deep_ema50_entry:
            if vwap_entry:
                pattern = "VWAP"
                confidence = 0.8
            elif ema50_entry:
                pattern = "EMA50"
                confidence = 0.75
            elif deep_vwap_entry:
                pattern = "DeepVWAP"
                confidence = 0.85
            else:
                pattern = "DeepEMA50"
                confidence = 0.8

            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=confidence * self._trending_confidence,
                reason=f"4h Downtrend {pattern} rally: vs_vwap={price_vs_vwap:.4f}, "
                f"vs_ema50={price_vs_ema50:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime_4h": "trending_down",
                    "pattern": pattern,
                    "ema_slope_4h": ema_slope_4h,
                    "price_vs_vwap": price_vs_vwap,
                    "price_vs_ema50": price_vs_ema50,
                    "rsi_14": rsi_14,
                },
                trading_mode="futures" if self._config.get("futures_enabled") else "spot",
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"4h Downtrend waiting for rally: vs_vwap={price_vs_vwap:.4f}, rsi={rsi_14:.1f}",
            indicators={
                "regime_4h": "trending_down",
                "price_vs_vwap": price_vs_vwap,
                "price_vs_ema50": price_vs_ema50,
            },
        )

    def _generate_range_signal(
        self,
        symbol: str,
        close_price: float,
        indicators: dict[str, float],
    ) -> Signal:
        """Generate signal for ranging/mean-reversion regime."""
        bb_upper_dist = indicators.get("bb_upper_dist", 0.0) or 0.0
        bb_lower_dist = indicators.get("bb_lower_dist", 0.0) or 0.0
        rsi_14 = indicators.get("rsi_14", 50.0) or 50.0
        price_vs_weekly = indicators.get("price_vs_weekly", 0.0) or 0.0

        # Buy near lower Bollinger Band with oversold RSI
        if bb_lower_dist < 0.01 and rsi_14 < 35 and price_vs_weekly < -2.0:
            return Signal(
                type=SignalType.BUY,
                confidence=0.7 * self._ranging_confidence,
                symbol=symbol,
                price=close_price,
                reason=f"4h Ranging mean reversion buy: bb_lower_dist={bb_lower_dist:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime_4h": "ranging",
                    "bb_lower_dist": bb_lower_dist,
                    "rsi_14": rsi_14,
                    "price_vs_weekly": price_vs_weekly,
                },
                trading_mode="spot",
            )

        # Sell near upper Bollinger Band with overbought RSI
        if bb_upper_dist < 0.01 and rsi_14 > 65 and price_vs_weekly > 2.0:
            return Signal(
                type=SignalType.SELL,
                confidence=0.7 * self._ranging_confidence,
                symbol=symbol,
                price=close_price,
                reason=f"4h Ranging mean reversion sell: bb_upper_dist={bb_upper_dist:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime_4h": "ranging",
                    "bb_upper_dist": bb_upper_dist,
                    "rsi_14": rsi_14,
                    "price_vs_weekly": price_vs_weekly,
                },
                trading_mode="spot",
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.2,
            reason="4h Ranging but no clear entry",
            indicators={"regime_4h": "ranging", "rsi_14": rsi_14},
        )
