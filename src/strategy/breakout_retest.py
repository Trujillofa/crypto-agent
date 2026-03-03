from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class BreakoutRetestStrategy(BaseStrategy):
    """Long-only breakout and retest strategy.

    Thesis:
    - arm after a strong breakout impulse in an existing uptrend
    - buy only if price later reclaims VWAP or EMA50 within a short window
    - let the executor manage exits via ATR SL/TP/trailing
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._min_trend_strength_pct = float(self._config.get("min_trend_strength_pct", 0.008))
        self._min_atr_pct = float(self._config.get("min_atr_pct", 0.008))
        self._breakout_rsi_level = float(self._config.get("breakout_rsi_level", 58.0))
        self._breakout_min_macd_hist = float(self._config.get("breakout_min_macd_hist", 0.0))
        self._breakout_band_distance_threshold = float(
            self._config.get("breakout_band_distance_threshold", 0.01)
        )
        self._breakout_min_vwap_extension_pct = float(
            self._config.get("breakout_min_vwap_extension_pct", 0.01)
        )
        self._retest_window_bars = int(self._config.get("retest_window_bars", 4))
        self._retest_vwap_distance_pct = float(self._config.get("retest_vwap_distance_pct", 0.015))
        self._retest_ema50_distance_pct = float(
            self._config.get("retest_ema50_distance_pct", 0.015)
        )
        self._reclaim_rsi_level = float(self._config.get("reclaim_rsi_level", 50.0))
        self._retest_min_macd_hist = float(self._config.get("retest_min_macd_hist", -0.01))
        self._max_extension_after_retest_pct = float(
            self._config.get("max_extension_after_retest_pct", 0.025)
        )
        self._min_reclaim_above_levels_pct = float(
            self._config.get("min_reclaim_above_levels_pct", 0.002)
        )

        self._previous_close: dict[str, float] = {}
        self._previous_rsi: dict[str, float] = {}
        self._previous_macd_hist: dict[str, float] = {}
        self._armed_bars_remaining: dict[str, int] = {}
        self._entered_this_arm: dict[str, bool] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        required_indicators = {
            "close_price",
            "ema_50",
            "ema_200",
            "vwap",
            "bb_upper_dist",
            "rsi_14",
            "macd_hist",
            "atr_pct",
        }
        for key in required_indicators:
            if key not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {key}")

        close_price = indicators["close_price"]
        ema_50 = indicators["ema_50"]
        ema_200 = indicators["ema_200"]
        vwap = indicators["vwap"]
        bb_upper_dist = indicators["bb_upper_dist"]
        rsi_14 = indicators["rsi_14"]
        macd_hist = indicators["macd_hist"]
        atr_pct = indicators["atr_pct"]

        if any(
            value is None
            for value in (ema_50, ema_200, vwap, bb_upper_dist, rsi_14, macd_hist, atr_pct)
        ):
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for breakout/retest indicators",
                indicators={},
            )

        previous_close = self._previous_close.get(symbol, close_price)
        previous_rsi = self._previous_rsi.get(symbol, rsi_14)
        previous_macd_hist = self._previous_macd_hist.get(symbol, macd_hist)
        armed_bars_remaining = self._armed_bars_remaining.get(symbol, 0)

        trend_strength_pct = (ema_50 - ema_200) / ema_200 if ema_200 > 0 else 0.0
        vwap_extension_pct = (close_price - vwap) / vwap if vwap > 0 else 0.0
        vwap_distance_pct = abs(close_price - vwap) / vwap if vwap > 0 else 0.0
        ema50_distance_pct = abs(close_price - ema_50) / ema_50 if ema_50 > 0 else 0.0
        ema50_extension_pct = (close_price - ema_50) / ema_50 if ema_50 > 0 else 0.0

        in_uptrend = (
            close_price > ema_200
            and ema_50 > ema_200
            and trend_strength_pct >= self._min_trend_strength_pct
            and atr_pct >= self._min_atr_pct
        )

        breakout_impulse = (
            in_uptrend
            and close_price > previous_close
            and rsi_14 >= self._breakout_rsi_level
            and macd_hist >= self._breakout_min_macd_hist
            and (
                bb_upper_dist <= self._breakout_band_distance_threshold
                or vwap_extension_pct >= self._breakout_min_vwap_extension_pct
            )
        )

        if breakout_impulse:
            armed_bars_remaining = self._retest_window_bars
            self._entered_this_arm[symbol] = False
        # FAIL-FAST: Allow only ONE entry per breakout arm
        # Prevents clustered bad entries when multiple retests fire within the same arm
        elif armed_bars_remaining > 0 and not self._entered_this_arm.get(symbol, False):
            armed_bars_remaining -= 1

        self._armed_bars_remaining[symbol] = armed_bars_remaining
        self._previous_close[symbol] = close_price
        self._previous_rsi[symbol] = rsi_14
        self._previous_macd_hist[symbol] = macd_hist

        retest_zone = (
            vwap_distance_pct <= self._retest_vwap_distance_pct
            or ema50_distance_pct <= self._retest_ema50_distance_pct
        )
        # Stricter reclaim confirmation: require momentum improvement AND minimum price above levels
        reclaim_above_vwap = (close_price - vwap) / vwap if vwap > 0 else 0.0
        reclaim_above_ema50 = (close_price - ema_50) / ema_50 if ema_50 > 0 else 0.0
        reclaim_confirmed = (
            close_price >= ema_50
            and close_price >= vwap
            and close_price > previous_close
            and rsi_14 >= self._reclaim_rsi_level
            and macd_hist >= self._retest_min_macd_hist
            # Both RSI and MACD must show momentum improvement (was: or)
            and rsi_14 > previous_rsi
            and macd_hist > previous_macd_hist
            # Must reclaim above levels by minimum margin
            and reclaim_above_vwap >= self._min_reclaim_above_levels_pct
            and reclaim_above_ema50 >= self._min_reclaim_above_levels_pct
            and ema50_extension_pct <= self._max_extension_after_retest_pct
        )

        if (
            in_uptrend
            and armed_bars_remaining > 0
            and retest_zone
            and reclaim_confirmed
            and not self._entered_this_arm.get(symbol, False)
        ):
            self._entered_this_arm[symbol] = True
            trend_bonus = min(0.2, max(0.0, trend_strength_pct) * 5)
            momentum_bonus = min(0.1, max(0.0, rsi_14 - self._reclaim_rsi_level) / 20)
            confidence = min(0.85, 0.55 + trend_bonus + momentum_bonus)
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=(
                    "Breakout retest confirmed: "
                    f"price={close_price:.2f} ema50={ema_50:.2f} ema200={ema_200:.2f} "
                    f"vwap={vwap:.2f} rsi={rsi_14:.2f} prev_rsi={previous_rsi:.2f} "
                    f"macd_hist={macd_hist:.4f} armed={armed_bars_remaining}"
                ),
                indicators={
                    "ema_50": ema_50,
                    "ema_200": ema_200,
                    "vwap": vwap,
                    "rsi_14": rsi_14,
                    "atr_pct": atr_pct,
                    "macd_hist": macd_hist,
                    "bb_upper_dist": bb_upper_dist,
                },
            )
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.0,
            reason=(
                f"No breakout retest setup: trend={in_uptrend} armed={armed_bars_remaining} "
                f"breakout_impulse={breakout_impulse} retest_zone={retest_zone} "
                f"reclaim_confirmed={reclaim_confirmed}"
            ),
            indicators={
                "ema_50": ema_50,
                "ema_200": ema_200,
                "vwap": vwap,
                "rsi_14": rsi_14,
                "atr_pct": atr_pct,
                "macd_hist": macd_hist,
                "bb_upper_dist": bb_upper_dist,
            },
        )

    def get_name(self) -> str:
        return "BreakoutRetest"
