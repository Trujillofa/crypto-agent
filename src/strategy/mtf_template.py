"""
Multi-Timeframe Strategy Template

This template demonstrates how to build a multi-timeframe (MTF) strategy using
the MTF infrastructure. Use this as a starting point for your own MTF strategies.

Architecture Pattern:
- Higher timeframe (regime): Classify market regime (trending/ranging/uncertain)
- Entry timeframe: Execute entries based on regime classification
- Joined data: Higher timeframe indicators are suffixed (e.g., _4h)

Example Use Cases:
- 4h trend regime + 1h pullback entries
- 1d volatility regime + 4h breakout entries
- 4h support/resistance + 1h retest entries
"""

from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class MTFStrategyTemplate(BaseStrategy):
    """Template for multi-timeframe strategies.

    This shows the standard pattern for MTF strategies:
    1. Declare REQUIRED_TIMEFRAMES with regime and entry timeframes
    2. Extract suffixed regime indicators (e.g., ema_slope_50_4h)
    3. Extract base timeframe entry indicators (e.g., vwap, rsi_14)
    4. Classify regime using higher timeframe indicators
    5. Generate signals using entry timeframe indicators + regime

    The backtest engine automatically:
    - Fetches both timeframes
    - Joins them using as-of logic (no lookahead)
    - Passes joined indicators to evaluate()
    """

    # REQUIRED: Declare timeframes for MTF support
    # The engine uses this to fetch and join the correct data
    REQUIRED_TIMEFRAMES = {
        "entry": "1h",  # Base timeframe for entries
        "regime": "4h",  # Higher timeframe for regime classification
    }

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Initialize MTF strategy with configuration.

        Args:
            config: Strategy-specific configuration. Common options:
                - regime_threshold: Threshold for regime classification
                - entry_pullback_pct: Pullback distance for entry
                - confidence_boost: Multiplier for trending regime confidence
        """
        super().__init__(config)

        # Regime classification thresholds (tune these for your thesis)
        self._regime_threshold = float(
            self._config.get("regime_threshold", 0.005)
        )  # 0.5% slope threshold
        self._trend_consistency_threshold = float(
            self._config.get("trend_consistency_threshold", 50.0)
        )  # Default 50%
        self._volatility_threshold = float(
            self._config.get("volatility_percentile_threshold", 40.0)
        )  # Default 40% - lowered from 50

        # Entry parameters
        self._entry_pullback_pct = float(
            self._config.get("entry_pullback_pct", 0.01)
        )  # 1% pullback
        self._rsi_oversold = float(self._config.get("rsi_oversold", 40.0))
        self._rsi_overbought = float(self._config.get("rsi_overbought", 60.0))

        # Confidence multipliers
        self._confidence_boost = float(
            self._config.get("confidence_boost", 1.2)
        )  # 20% boost in trending regime

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate trading signal.

        The indicators dict contains BOTH timeframes:
        - Base timeframe (entry): close_price, vwap, rsi_14, etc.
        - Higher timeframe (regime): suffixed with _4h (e.g., ema_slope_50_4h)

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)
            indicators: Dictionary containing joined indicators from both timeframes.
                       Higher timeframe indicators are suffixed (e.g., _4h).

        Returns:
            Signal: Trading signal with type, confidence, and metadata
        """
        # =====================================================================
        # STEP 1: Extract indicators
        # =====================================================================

        # Entry timeframe indicators (base - no suffix)
        close_price = indicators.get("close_price", 0.0)
        vwap = indicators.get("vwap", close_price)
        rsi_14 = indicators.get("rsi_14", 50.0) or 50.0

        # Calculate price distances for entry logic
        price_vs_vwap = (close_price - vwap) / vwap if vwap != 0 else 0

        # Higher timeframe (regime) indicators - SUFFIXED with _4h
        # These are forward-filled from the most recent completed 4h bar
        ema_slope_4h = indicators.get("ema_slope_50_4h", 0.0) or 0.0
        trend_consistency_4h = indicators.get("trend_consistency_4h", 50.0) or 50.0
        volatility_percentile_4h = indicators.get("volatility_percentile_4h", 50.0) or 50.0

        # Validate required data
        if close_price == 0.0:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=0.0,
                confidence=0.0,
                reason="Missing close price",
                indicators={},
            )

        # =====================================================================
        # STEP 2: Classify regime using higher timeframe
        # =====================================================================

        regime = self._classify_regime(ema_slope_4h, trend_consistency_4h, volatility_percentile_4h)

        # =====================================================================
        # STEP 3: Generate signal based on regime + entry conditions
        # =====================================================================

        if regime == "trending_up":
            return self._generate_long_signal(
                symbol, close_price, price_vs_vwap, rsi_14, ema_slope_4h, indicators
            )
        elif regime == "trending_down":
            return self._generate_short_signal(
                symbol, close_price, price_vs_vwap, rsi_14, ema_slope_4h, indicators
            )
        elif regime == "ranging":
            return self._generate_range_signal(symbol, close_price, rsi_14, indicators)
        else:  # uncertain
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"Regime uncertain: consistency={trend_consistency_4h:.1f}",
                indicators={"regime": regime},
            )

    def _classify_regime(
        self,
        ema_slope: float,
        trend_consistency: float,
        volatility_percentile: float,
    ) -> str:
        """Classify market regime based on higher timeframe indicators.

        Args:
            ema_slope: EMA slope from higher timeframe (e.g., 4h)
            trend_consistency: Trend consistency score (0-100)
            volatility_percentile: Volatility percentile (0-100)

        Returns:
            str: One of "trending_up", "trending_down", "ranging", "uncertain"
        """
        # Trending: strong slope + configurable thresholds
        is_trending = (
            abs(ema_slope) > self._regime_threshold
            and trend_consistency > self._trend_consistency_threshold
            and volatility_percentile > self._volatility_threshold
        )

        if is_trending:
            if ema_slope > 0:
                return "trending_up"
            else:
                return "trending_down"

        # Ranging: low slope + moderate consistency
        is_ranging = abs(ema_slope) <= self._regime_threshold * 0.5 and trend_consistency < 60.0

        if is_ranging:
            return "ranging"

        return "uncertain"

    def _generate_long_signal(
        self,
        symbol: str,
        close_price: float,
        price_vs_vwap: float,
        rsi_14: float,
        ema_slope_4h: float,
        indicators: dict[str, float],
    ) -> Signal:
        """Generate long entry signal in uptrending regime.

        Entry thesis: Buy pullbacks in established uptrends.
        - Price near/below VWAP (pullback)
        - RSI oversold (mean reversion setup)
        - Higher timeframe confirming uptrend
        """
        # Entry condition: pullback to VWAP with oversold RSI
        is_pullback = -self._entry_pullback_pct * 2 <= price_vs_vwap <= self._entry_pullback_pct
        is_oversold = rsi_14 < self._rsi_oversold

        if is_pullback and is_oversold:
            confidence = 0.75 * self._confidence_boost

            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=min(confidence, 0.95),  # Cap at 95%
                reason=(
                    f"Uptrend pullback: vs_vwap={price_vs_vwap:.4f}, "
                    f"rsi={rsi_14:.1f}, 4h_slope={ema_slope_4h:.4f}"
                ),
                indicators={
                    "regime": "trending_up",
                    "price_vs_vwap": price_vs_vwap,
                    "rsi_14": rsi_14,
                    "ema_slope_4h": ema_slope_4h,
                },
            )

        # No entry - wait for better setup
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=(f"Uptrend waiting for pullback: vs_vwap={price_vs_vwap:.4f}, rsi={rsi_14:.1f}"),
            indicators={
                "regime": "trending_up",
                "price_vs_vwap": price_vs_vwap,
                "rsi_14": rsi_14,
            },
        )

    def _generate_short_signal(
        self,
        symbol: str,
        close_price: float,
        price_vs_vwap: float,
        rsi_14: float,
        ema_slope_4h: float,
        indicators: dict[str, float],
    ) -> Signal:
        """Generate short entry signal in downtrending regime.

        Entry thesis: Short rallies in established downtrends.
        - Price near/above VWAP (rally)
        - RSI overbought (mean reversion setup)
        - Higher timeframe confirming downtrend
        """
        # Entry condition: rally to VWAP with overbought RSI
        is_rally = -self._entry_pullback_pct <= price_vs_vwap <= self._entry_pullback_pct * 2
        is_overbought = rsi_14 > self._rsi_overbought

        if is_rally and is_overbought:
            confidence = 0.75 * self._confidence_boost

            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=min(confidence, 0.95),
                reason=(
                    f"Downtrend rally: vs_vwap={price_vs_vwap:.4f}, "
                    f"rsi={rsi_14:.1f}, 4h_slope={ema_slope_4h:.4f}"
                ),
                indicators={
                    "regime": "trending_down",
                    "price_vs_vwap": price_vs_vwap,
                    "rsi_14": rsi_14,
                    "ema_slope_4h": ema_slope_4h,
                },
                trading_mode="futures",  # Short requires futures
            )

        # No entry - wait for better setup
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.3,
            reason=(f"Downtrend waiting for rally: vs_vwap={price_vs_vwap:.4f}, rsi={rsi_14:.1f}"),
            indicators={
                "regime": "trending_down",
                "price_vs_vwap": price_vs_vwap,
                "rsi_14": rsi_14,
            },
        )

    def _generate_range_signal(
        self,
        symbol: str,
        close_price: float,
        rsi_14: float,
        indicators: dict[str, float],
    ) -> Signal:
        """Generate signal for ranging/mean-reversion regime.

        Entry thesis: Buy low, sell high within the range.
        Uses Bollinger Bands for overbought/oversold detection.
        """
        bb_lower_dist = indicators.get("bb_lower_dist", 0.0) or 0.0
        bb_upper_dist = indicators.get("bb_upper_dist", 0.0) or 0.0

        # Buy near lower band with oversold RSI
        if bb_lower_dist < 0.01 and rsi_14 < 35:
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=0.65,
                reason=f"Range buy: bb_lower={bb_lower_dist:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime": "ranging",
                    "bb_lower_dist": bb_lower_dist,
                    "rsi_14": rsi_14,
                },
            )

        # Sell near upper band with overbought RSI
        if bb_upper_dist < 0.01 and rsi_14 > 65:
            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close_price,
                confidence=0.65,
                reason=f"Range sell: bb_upper={bb_upper_dist:.4f}, rsi={rsi_14:.1f}",
                indicators={
                    "regime": "ranging",
                    "bb_upper_dist": bb_upper_dist,
                    "rsi_14": rsi_14,
                },
            )

        # No clear entry
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.2,
            reason=f"Ranging but no clear entry: rsi={rsi_14:.1f}",
            indicators={"regime": "ranging", "rsi_14": rsi_14},
        )

    def get_name(self) -> str:
        """Return strategy name for logging and metrics."""
        return "MTFStrategyTemplate"


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

"""
To use this template:

1. Copy this file to a new strategy file (e.g., my_mtf_strategy.py)
2. Rename the class (e.g., MyMTFStrategy)
3. Customize the REQUIRED_TIMEFRAMES for your thesis
4. Modify the regime classification logic
5. Add your entry conditions
6. Register in your backtest config:

   strategies:
     - name: MyMTFStrategy
       class: src.strategy.my_mtf_strategy.MyMTFStrategy
       params:
         regime_threshold: 0.005
         entry_pullback_pct: 0.01
         rsi_oversold: 40.0

BACKTEST CONFIGURATION EXAMPLE:

strategies:
  - name: MTFTrendPullback
    class: src.strategy.mtf_template.MTFStrategyTemplate
    params:
      # Regime classification
      regime_threshold: 0.005        # 0.5% EMA slope threshold

      # Entry conditions
      entry_pullback_pct: 0.01       # 1% pullback zone
      rsi_oversold: 40.0             # Oversold threshold
      rsi_overbought: 60.0           # Overbought threshold

      # Confidence
      confidence_boost: 1.2          # 20% boost in trending regime

timeframes:
  entry: 1h    # Must match REQUIRED_TIMEFRAMES['entry']
  regime: 4h   # Must match REQUIRED_TIMEFRAMES['regime']
"""
