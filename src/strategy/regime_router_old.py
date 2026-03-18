from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class RegimeRouterStrategy(BaseStrategy):
    """Enhanced regime router with armed pullback windows and directional reclaim entries.

    Key improvements:
    1. Armed pullback window: When regime turns trending, arm a 6-bar window
    2. Directional reclaim: Enter on reclaim pattern, not just proximity
    3. Dual anchors: VWAP and EMA50 as entry zones
    4. State tracking: Track regime changes and pullback phases

    This produces more trade opportunities while maintaining quality.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        # Thresholds for regime classification
        self._trend_strength_threshold = float(self._config.get("trend_strength_threshold", 0.005))
        self._volatility_percentile_threshold = float(
            self._config.get("volatility_percentile_threshold", 60.0)
        )
        self._trend_consistency_threshold = float(
            self._config.get("trend_consistency_threshold", 60.0)
        )
        self._rsi_slope_threshold = float(self._config.get("rsi_slope_threshold", 5.0))

        # Signal confidence multipliers by regime
        self._trending_confidence = float(self._config.get("trending_confidence", 1.2))
        self._ranging_confidence = float(self._config.get("ranging_confidence", 1.0))
        self._uncertain_confidence = float(self._config.get("uncertain_confidence", 0.0))

        # Pullback window settings
        self._pullback_window_bars = int(self._config.get("pullback_window_bars", 6))
        self._pullback_threshold = float(self._config.get("pullback_threshold", 0.01))  # 1%
        self._reclaim_threshold = float(self._config.get("reclaim_threshold", 0.005))  # 0.5%

        # State tracking (initialized per evaluation)
        self._regime_state = "uncertain"
        self._armed_regime = None
        self._arm_bar_count = 0
        self._pullback_detected = False
        self._pullback_anchor = None  # "vwap" or "ema50"
        self._previous_price_vs_vwap = 0.0
        self._previous_price_vs_ema50 = 0.0

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate market regime and generate appropriate signal."""

        # Extract regime indicators
        ema_slope = indicators.get("ema_slope_50", 0.0) or 0.0
        vol_pct = indicators.get("volatility_percentile", 50.0) or 50.0
        trend_consistency = indicators.get("trend_consistency", 50.0) or 50.0
        rsi_slope = indicators.get("rsi_slope", 0.0) or 0.0
        atr_pct = indicators.get("atr_pct", 0.0) or 0.0
        close_price = indicators.get("close_price", 0.0)

        if close_price == 0.0:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=0.0,
                confidence=0.0,
                reason="Missing close price",
                indicators={},
            )

        # Classify market regime
        new_regime = self._classify_regime(
            ema_slope, vol_pct, trend_consistency, rsi_slope, atr_pct
        )

        # Update regime state and arm pullback window if regime changed
        self._update_regime_state(new_regime, ema_slope)

        # Generate signal based on regime
        if self._regime_state in ["trending_up", "trending_down"]:
            return self._generate_trend_signal_with_pullback(
                symbol, close_price, indicators, ema_slope, rsi_slope
            )
        elif self._regime_state == "ranging":
            return self._generate_range_signal(symbol, close_price, indicators, vol_pct)
        else:  # uncertain
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=self._uncertain_confidence,
                reason=f"Uncertain regime: vol_pct={vol_pct:.1f}, trend_consistency={trend_consistency:.1f}",
                indicators={"regime": self._regime_state, "volatility_percentile": vol_pct},
            )

    def _update_regime_state(self, new_regime: str, ema_slope: float) -> None:
        """Track regime changes and arm pullback windows."""
        # Determine directional regime
        if new_regime == "trending":
            directional_regime = "trending_up" if ema_slope > 0 else "trending_down"
        else:
            directional_regime = new_regime

        # Check if regime changed
        if directional_regime != self._regime_state:
            # Regime change - arm new window
            self._regime_state = directional_regime
            self._armed_regime = directional_regime
            self._arm_bar_count = 0
            self._pullback_detected = False
            self._pullback_anchor = None
        else:
            # Same regime - increment bar count
            if self._armed_regime is not None:
                self._arm_bar_count += 1

        # Reset if window expired
        if self._arm_bar_count >= self._pullback_window_bars:
            self._armed_regime = None
            self._pullback_detected = False
            self._pullback_anchor = None

    def _classify_regime(
        self,
        ema_slope: float,
        vol_pct: float,
        trend_consistency: float,
        rsi_slope: float,
        atr_pct: float,
    ) -> str:
        """Classify market regime based on indicators.

        Returns:
            "trending", "ranging", or "uncertain"
        """
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

        # Momentum confirmation for trending
        if is_trending:
            momentum_aligned = (ema_slope > 0 and rsi_slope > 0) or (
                ema_slope < 0 and rsi_slope < 0
            )
            if momentum_aligned:
                return "trending"

        if is_ranging:
            return "ranging"

        return "uncertain"

    def _generate_trend_signal_with_pullback(
        self,
        symbol: str,
        close_price: float,
        indicators: dict[str, float],
        ema_slope: float,
        rsi_slope: float,
    ) -> Signal:
        """Generate signal for trending regime with pullback/reclaim logic."""
        vwap = indicators.get("vwap", close_price)
        ema_50 = indicators.get("ema_50", close_price)

        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema50 = (close_price - ema_50) / ema_50 if ema_50 != 0 else 0

        is_uptrend = self._regime_state == "trending_up"

        # Check for pullback detection
        if not self._pullback_detected and self._armed_regime is not None:
            # Uptrend: look for pullback below VWAP or EMA50
            if is_uptrend:
                if price_vs_vwap < -self._pullback_threshold:
                    self._pullback_detected = True
                    self._pullback_anchor = "vwap"
                elif price_vs_ema50 < -self._pullback_threshold:
                    self._pullback_detected = True
                    self._pullback_anchor = "ema50"
            # Downtrend: look for rally above VWAP or EMA50
            else:
                if price_vs_vwap > self._pullback_threshold:
                    self._pullback_detected = True
                    self._pullback_anchor = "vwap"
                elif price_vs_ema50 > self._pullback_threshold:
                    self._pullback_detected = True
                    self._pullback_anchor = "ema50"

        # Check for reclaim entry
        if self._pullback_detected and self._armed_regime is not None:
            entry_signal = None

            if is_uptrend and rsi_slope > -2:
                # Long entry: reclaim above anchor
                if self._pullback_anchor == "vwap":
                    # Check if price reclaimed VWAP (was below, now near/above)
                    if (
                        self._previous_price_vs_vwap < -self._reclaim_threshold
                        and price_vs_vwap > -self._reclaim_threshold
                    ):
                        entry_signal = SignalType.BUY
                        anchor_name = "VWAP"
                elif self._pullback_anchor == "ema50":
                    # Check if price reclaimed EMA50
                    if (
                        self._previous_price_vs_ema50 < -self._reclaim_threshold
                        and price_vs_ema50 > -self._reclaim_threshold
                    ):
                        entry_signal = SignalType.BUY
                        anchor_name = "EMA50"

            elif not is_uptrend and rsi_slope < 2:
                # Short entry: reclaim below anchor
                if self._pullback_anchor == "vwap":
                    # Check if price reclaimed below VWAP (was above, now near/below)
                    if (
                        self._previous_price_vs_vwap > self._reclaim_threshold
                        and price_vs_vwap < self._reclaim_threshold
                    ):
                        entry_signal = SignalType.SELL
                        anchor_name = "VWAP"
                elif self._pullback_anchor == "ema50":
                    if (
                        self._previous_price_vs_ema50 > self._reclaim_threshold
                        and price_vs_ema50 < self._reclaim_threshold
                    ):
                        entry_signal = SignalType.SELL
                        anchor_name = "EMA50"

            if entry_signal:
                # Reset state after entry
                self._armed_regime = None
                self._pullback_detected = False
                self._pullback_anchor = None

                return Signal(
                    type=entry_signal,
                    symbol=symbol,
                    price=close_price,
                    confidence=0.8 * self._trending_confidence,
                    reason=f"Trending reclaim: {self._regime_state}, anchor={anchor_name}, "
                    f"price_vs_vwap={price_vs_vwap:.4f}",
                    indicators={
                        "regime": self._regime_state,
                        "ema_slope": ema_slope,
                        "price_vs_vwap": price_vs_vwap,
                        "price_vs_ema50": price_vs_ema50,
                        "anchor": anchor_name,
                    },
                    trading_mode="futures" if self._config.get("futures_enabled") else "spot",
                )

        # Update previous values for next bar
        self._previous_price_vs_vwap = price_vs_vwap
        self._previous_price_vs_ema50 = price_vs_ema50

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"Trending - pullback phase: detected={self._pullback_detected}, "
            f"bars={self._arm_bar_count}/{self._pullback_window_bars}",
            indicators={
                "regime": self._regime_state,
                "ema_slope": ema_slope,
                "price_vs_vwap": price_vs_vwap,
                "pullback_detected": self._pullback_detected,
            },
        )

    def _generate_range_signal(
        self,
        symbol: str,
        close_price: float,
        indicators: dict[str, float],
        vol_pct: float,
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
                reason=f"Mean reversion buy: bb_lower_dist={bb_lower_dist:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime": "ranging",
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
                reason=f"Mean reversion sell: bb_upper_dist={bb_upper_dist:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime": "ranging",
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
            reason="Ranging but no clear entry",
            indicators={"regime": "ranging", "rsi_14": rsi_14},
        )
