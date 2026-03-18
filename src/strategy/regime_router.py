from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class RegimeRouterStrategy(BaseStrategy):
    """Enhanced regime router with dual anchor entries (VWAP + EMA50).

    Key improvements over original:
    1. Dual entry anchors: VWAP and EMA50 (whichever offers better entry)
    2. Asymmetric pullback logic: Must pull back below anchor for longs
    3. Wider entry zones: 1-2% from anchor instead of 0.5%
    4. More trade opportunities while maintaining quality
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

        # Entry zone settings
        self._entry_zone_pct = float(self._config.get("entry_zone_pct", 0.01))  # 1% default
        self._rsi_oversold = float(self._config.get("rsi_oversold", 45.0))
        self._rsi_overbought = float(self._config.get("rsi_overbought", 55.0))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate market regime and generate appropriate signal."""

        # Extract regime indicators
        ema_slope = indicators.get("ema_slope_50", 0.0) or 0.0
        vol_pct = indicators.get("volatility_percentile", 50.0) or 50.0
        trend_consistency = indicators.get("trend_consistency", 50.0) or 50.0
        rsi_slope = indicators.get("rsi_slope", 0.0) or 0.0
        rsi_14 = indicators.get("rsi_14", 50.0) or 50.0
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
        regime = self._classify_regime(ema_slope, vol_pct, trend_consistency, rsi_slope, atr_pct)

        # Generate signal based on regime
        if regime == "trending":
            return self._generate_trend_signal(
                symbol, close_price, indicators, ema_slope, rsi_slope, rsi_14
            )
        elif regime == "ranging":
            return self._generate_range_signal(symbol, close_price, indicators, vol_pct)
        else:  # uncertain
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=self._uncertain_confidence,
                reason=f"Uncertain regime: vol_pct={vol_pct:.1f}, trend_consistency={trend_consistency:.1f}",
                indicators={"regime": regime, "volatility_percentile": vol_pct},
            )

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

    def _generate_trend_signal(
        self,
        symbol: str,
        close_price: float,
        indicators: dict[str, float],
        ema_slope: float,
        rsi_slope: float,
        rsi_14: float,
    ) -> Signal:
        """Generate signal for trending regime with dual anchor entries."""
        vwap = indicators.get("vwap", close_price)
        ema_50 = indicators.get("ema_50", close_price)

        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0
        price_vs_ema50 = (close_price - ema_50) / ema_50 if ema_50 != 0 else 0

        is_uptrend = ema_slope > 0

        # Long entry logic (uptrend)
        if is_uptrend and rsi_slope > -2:
            # Check VWAP anchor - price near or below VWAP (pullback)
            vwap_entry = (
                -self._entry_zone_pct * 2 <= price_vs_vwap <= self._entry_zone_pct
                and rsi_14 < self._rsi_oversold
            )

            # Check EMA50 anchor - price near or below EMA50
            ema50_entry = (
                -self._entry_zone_pct * 2 <= price_vs_ema50 <= self._entry_zone_pct
                and rsi_14 < self._rsi_oversold
            )

            if vwap_entry or ema50_entry:
                anchor = "VWAP" if vwap_entry else "EMA50"
                return Signal(
                    type=SignalType.BUY,
                    symbol=symbol,
                    price=close_price,
                    confidence=0.8 * self._trending_confidence,
                    reason=f"Trending pullback ({anchor}): slope={ema_slope:.4f}, "
                    f"vs_vwap={price_vs_vwap:.4f}, vs_ema50={price_vs_ema50:.4f}, rsi={rsi_14:.1f}",
                    indicators={
                        "regime": "trending_up",
                        "ema_slope": ema_slope,
                        "price_vs_vwap": price_vs_vwap,
                        "price_vs_ema50": price_vs_ema50,
                        "rsi_14": rsi_14,
                        "anchor": anchor,
                    },
                    trading_mode="futures" if self._config.get("futures_enabled") else "spot",
                )

        # Short entry logic (downtrend)
        if not is_uptrend and rsi_slope < 2:
            # Check VWAP anchor - price near or above VWAP (rally)
            vwap_entry = (
                -self._entry_zone_pct <= price_vs_vwap <= self._entry_zone_pct * 2
                and rsi_14 > self._rsi_overbought
            )

            # Check EMA50 anchor - price near or above EMA50
            ema50_entry = (
                -self._entry_zone_pct <= price_vs_ema50 <= self._entry_zone_pct * 2
                and rsi_14 > self._rsi_overbought
            )

            if vwap_entry or ema50_entry:
                anchor = "VWAP" if vwap_entry else "EMA50"
                return Signal(
                    type=SignalType.SELL,
                    symbol=symbol,
                    price=close_price,
                    confidence=0.8 * self._trending_confidence,
                    reason=f"Trending rally ({anchor}): slope={ema_slope:.4f}, "
                    f"vs_vwap={price_vs_vwap:.4f}, vs_ema50={price_vs_ema50:.4f}, rsi={rsi_14:.1f}",
                    indicators={
                        "regime": "trending_down",
                        "ema_slope": ema_slope,
                        "price_vs_vwap": price_vs_vwap,
                        "price_vs_ema50": price_vs_ema50,
                        "rsi_14": rsi_14,
                        "anchor": anchor,
                    },
                    trading_mode="futures" if self._config.get("futures_enabled") else "spot",
                )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=f"Trending but no entry: vs_vwap={price_vs_vwap:.4f}, vs_ema50={price_vs_ema50:.4f}, rsi={rsi_14:.1f}",
            indicators={
                "regime": "trending",
                "ema_slope": ema_slope,
                "price_vs_vwap": price_vs_vwap,
                "price_vs_ema50": price_vs_ema50,
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
