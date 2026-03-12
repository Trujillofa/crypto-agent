from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class TrendPullbackStrategy(BaseStrategy):
    """Long-biased trend pullback strategy.

    Entry thesis:
    - only buy when the market is already in an uptrend
    - enter either on a shallow pullback recovery or a strong-trend continuation
    - use only trend-consistent long entries with simple stateful recovery checks

    Exit thesis:
    - normal exits are handled by the executor via ATR SL/TP/trailing
    - this strategy emits BUY or HOLD only
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._rsi_reclaim_level = float(self._config.get("rsi_reclaim_level", 50.0))
        self._min_trend_strength_pct = float(self._config.get("min_trend_strength_pct", 0.01))
        self._max_pullback_distance_pct = float(
            self._config.get("max_pullback_distance_pct", 0.015)
        )
        self._vwap_pullback_distance_pct = float(
            self._config.get("vwap_pullback_distance_pct", 0.01)
        )
        self._min_atr_pct = float(self._config.get("min_atr_pct", 0.01))
        self._min_macd_hist = float(self._config.get("min_macd_hist", 0.0))
        self._strong_trend_strength_pct = float(
            self._config.get("strong_trend_strength_pct", 0.015)
        )
        self._continuation_rsi_level = float(self._config.get("continuation_rsi_level", 54.0))
        self._continuation_max_vwap_distance_pct = float(
            self._config.get("continuation_max_vwap_distance_pct", 0.04)
        )
        self._continuation_max_ema50_extension_pct = float(
            self._config.get("continuation_max_ema50_extension_pct", 0.03)
        )
        self._continuation_min_macd_hist = float(
            self._config.get("continuation_min_macd_hist", -0.01)
        )
        self._deep_reclaim_enabled = bool(self._config.get("deep_reclaim_enabled", False))
        self._deep_reclaim_rsi_level = float(self._config.get("deep_reclaim_rsi_level", 50.0))
        self._deep_reclaim_min_macd_hist = float(
            self._config.get("deep_reclaim_min_macd_hist", -0.005)
        )
        self._deep_reclaim_max_ema50_extension_pct = float(
            self._config.get("deep_reclaim_max_ema50_extension_pct", 0.03)
        )
        self._deep_reclaim_max_vwap_distance_pct = float(
            self._config.get("deep_reclaim_max_vwap_distance_pct", 0.03)
        )
        self._deep_reclaim_arm_window_bars = int(
            self._config.get("deep_reclaim_arm_window_bars", 3)
        )
        self._deep_reclaim_arm_max_ema50_distance_pct = float(
            self._config.get("deep_reclaim_arm_max_ema50_distance_pct", 0.03)
        )
        self._deep_reclaim_arm_max_vwap_distance_pct = float(
            self._config.get("deep_reclaim_arm_max_vwap_distance_pct", 0.05)
        )

        self._previous_rsi: dict[str, float] = {}
        self._previous_macd_hist: dict[str, float] = {}
        self._previous_close: dict[str, float] = {}
        self._deep_reclaim_armed_bars_remaining: dict[str, int] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        required_indicators = {
            "close_price",
            "ema_50",
            "ema_200",
            "rsi_14",
            "atr_pct",
            "macd_hist",
            "vwap",
        }
        for key in required_indicators:
            if key not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {key}")

        close_price = indicators["close_price"]
        ema_50 = indicators["ema_50"]
        ema_200 = indicators["ema_200"]
        rsi_14 = indicators["rsi_14"]
        atr_pct = indicators["atr_pct"]
        macd_hist = indicators["macd_hist"]
        vwap = indicators["vwap"]

        if any(value is None for value in (ema_50, ema_200, rsi_14, atr_pct, macd_hist, vwap)):
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="Waiting for trend pullback indicators",
                indicators={},
            )

        previous_rsi = self._previous_rsi.get(symbol, rsi_14)
        previous_macd_hist = self._previous_macd_hist.get(symbol, macd_hist)
        previous_close = self._previous_close.get(symbol, close_price)
        self._previous_rsi[symbol] = rsi_14
        self._previous_macd_hist[symbol] = macd_hist
        self._previous_close[symbol] = close_price

        trend_strength_pct = (ema_50 - ema_200) / ema_200 if ema_200 > 0 else 0.0
        distance_from_ema50_pct = abs(close_price - ema_50) / ema_50 if ema_50 > 0 else 0.0
        distance_from_vwap_pct = abs(close_price - vwap) / vwap if vwap > 0 else 0.0
        ema50_extension_pct = ((close_price - ema_50) / ema_50) if ema_50 > 0 else 0.0

        in_uptrend = (
            close_price > ema_200
            and ema_50 > ema_200
            and trend_strength_pct >= self._min_trend_strength_pct
            and atr_pct >= self._min_atr_pct
        )
        strong_trend = in_uptrend and trend_strength_pct >= self._strong_trend_strength_pct
        near_ema50 = distance_from_ema50_pct <= self._max_pullback_distance_pct
        near_vwap = distance_from_vwap_pct <= self._vwap_pullback_distance_pct
        rsi_reclaimed = rsi_14 >= self._rsi_reclaim_level and rsi_14 > previous_rsi
        macd_recovered = macd_hist >= self._min_macd_hist and macd_hist > previous_macd_hist
        price_recovered = close_price > previous_close
        recovery_ok = rsi_reclaimed and macd_recovered and price_recovered
        pullback_entry = in_uptrend and near_ema50 and near_vwap and recovery_ok

        continuation_near_vwap = distance_from_vwap_pct <= self._continuation_max_vwap_distance_pct
        continuation_not_extended = (
            0.0 <= ema50_extension_pct <= self._continuation_max_ema50_extension_pct
        )
        vwap_reclaim = previous_close <= vwap < close_price
        continuation_momentum = (
            rsi_14 >= self._continuation_rsi_level
            and macd_hist >= self._continuation_min_macd_hist
            and price_recovered
            and (rsi_14 > previous_rsi or macd_hist > previous_macd_hist or vwap_reclaim)
        )
        continuation_entry = (
            strong_trend
            and close_price >= ema_50
            and continuation_near_vwap
            and continuation_not_extended
            and continuation_momentum
        )
        armed_bars_remaining = self._deep_reclaim_armed_bars_remaining.get(symbol, 0)
        deep_reclaim_pullback_zone = (
            self._deep_reclaim_enabled
            and strong_trend
            and (close_price <= ema_50 or close_price <= vwap)
            and distance_from_ema50_pct <= self._deep_reclaim_arm_max_ema50_distance_pct
            and distance_from_vwap_pct <= self._deep_reclaim_arm_max_vwap_distance_pct
        )
        deep_reclaim_setup = armed_bars_remaining > 0 and not deep_reclaim_pullback_zone
        deep_reclaim_reclaimed = close_price >= ema_50 and close_price >= vwap
        deep_reclaim_not_extended = (
            0.0 <= ema50_extension_pct <= self._deep_reclaim_max_ema50_extension_pct
            and distance_from_vwap_pct <= self._deep_reclaim_max_vwap_distance_pct
        )
        deep_reclaim_momentum = (
            rsi_14 >= self._deep_reclaim_rsi_level
            and macd_hist >= self._deep_reclaim_min_macd_hist
            and price_recovered
            and rsi_14 > previous_rsi
            and macd_hist >= previous_macd_hist
        )
        deep_reclaim_entry = (
            self._deep_reclaim_enabled
            and strong_trend
            and deep_reclaim_setup
            and deep_reclaim_reclaimed
            and deep_reclaim_not_extended
            and deep_reclaim_momentum
        )
        next_armed_bars_remaining = 0
        if self._deep_reclaim_enabled and in_uptrend:
            if deep_reclaim_pullback_zone:
                next_armed_bars_remaining = self._deep_reclaim_arm_window_bars
            elif armed_bars_remaining > 0:
                next_armed_bars_remaining = armed_bars_remaining - 1
        self._deep_reclaim_armed_bars_remaining[symbol] = next_armed_bars_remaining

        if pullback_entry:
            self._deep_reclaim_armed_bars_remaining[symbol] = 0
            trend_bonus = min(0.2, max(0.0, trend_strength_pct) * 5)
            atr_bonus = min(0.1, max(0.0, atr_pct - self._min_atr_pct) * 8)
            confidence = min(0.9, 0.55 + trend_bonus + atr_bonus)
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=(
                    "Trend pullback recovered: "
                    f"price={close_price:.2f} ema50={ema_50:.2f} ema200={ema_200:.2f} "
                    f"vwap={vwap:.2f} rsi={rsi_14:.2f} prev_rsi={previous_rsi:.2f} "
                    f"macd_hist={macd_hist:.4f} prev_macd_hist={previous_macd_hist:.4f} "
                    f"atr_pct={atr_pct:.4f}"
                ),
                indicators={
                    "ema_50": ema_50,
                    "ema_200": ema_200,
                    "vwap": vwap,
                    "rsi_14": rsi_14,
                    "atr_pct": atr_pct,
                    "macd_hist": macd_hist,
                },
            )

        if continuation_entry:
            self._deep_reclaim_armed_bars_remaining[symbol] = 0
            trend_bonus = min(
                0.2, max(0.0, trend_strength_pct - self._strong_trend_strength_pct) * 6
            )
            momentum_bonus = min(0.1, max(0.0, rsi_14 - self._continuation_rsi_level) / 20)
            confidence = min(0.85, 0.5 + trend_bonus + momentum_bonus)
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=(
                    "Trend continuation confirmed: "
                    f"price={close_price:.2f} ema50={ema_50:.2f} ema200={ema_200:.2f} "
                    f"vwap={vwap:.2f} rsi={rsi_14:.2f} prev_rsi={previous_rsi:.2f} "
                    f"macd_hist={macd_hist:.4f} prev_macd_hist={previous_macd_hist:.4f} "
                    f"trend_strength={trend_strength_pct:.4f}"
                ),
                indicators={
                    "ema_50": ema_50,
                    "ema_200": ema_200,
                    "vwap": vwap,
                    "rsi_14": rsi_14,
                    "atr_pct": atr_pct,
                    "macd_hist": macd_hist,
                },
            )

        if deep_reclaim_entry:
            self._deep_reclaim_armed_bars_remaining[symbol] = 0
            trend_bonus = min(
                0.2, max(0.0, trend_strength_pct - self._strong_trend_strength_pct) * 5
            )
            momentum_bonus = min(0.1, max(0.0, rsi_14 - self._deep_reclaim_rsi_level) / 20)
            confidence = min(0.85, 0.53 + trend_bonus + momentum_bonus)
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=(
                    "Trend deep reclaim confirmed: "
                    f"price={close_price:.2f} ema50={ema_50:.2f} ema200={ema_200:.2f} "
                    f"vwap={vwap:.2f} prev_close={previous_close:.2f} "
                    f"rsi={rsi_14:.2f} prev_rsi={previous_rsi:.2f} "
                    f"macd_hist={macd_hist:.4f} prev_macd_hist={previous_macd_hist:.4f} "
                    f"armed_bars={armed_bars_remaining}"
                ),
                indicators={
                    "ema_50": ema_50,
                    "ema_200": ema_200,
                    "vwap": vwap,
                    "rsi_14": rsi_14,
                    "atr_pct": atr_pct,
                    "macd_hist": macd_hist,
                },
            )

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close_price,
            confidence=0.0,
            reason=(
                f"No trend pullback setup: trend={in_uptrend} near_ema50={near_ema50} "
                f"near_vwap={near_vwap} recovery_ok={recovery_ok} "
                f"strong_trend={strong_trend} continuation_entry={continuation_entry} "
                f"deep_reclaim_pullback_zone={deep_reclaim_pullback_zone} "
                f"deep_reclaim_entry={deep_reclaim_entry} "
                f"deep_reclaim_armed_bars={next_armed_bars_remaining} "
                f"rsi_reclaimed={rsi_reclaimed} macd_recovered={macd_recovered} "
                f"price_recovered={price_recovered}"
            ),
            indicators={
                "ema_50": ema_50,
                "ema_200": ema_200,
                "vwap": vwap,
                "rsi_14": rsi_14,
                "atr_pct": atr_pct,
                "macd_hist": macd_hist,
            },
        )

    def get_name(self) -> str:
        return "TrendPullback"
