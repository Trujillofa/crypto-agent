"""Volatility squeeze breakout strategy.

Detects periods of compressed Bollinger Band width (low volatility) and enters
on expansion breakouts with momentum confirmation. Uses ATR trailing stop for
exit management.

Based on the TTM Squeeze concept: when BB width reaches historical lows,
volatility contraction often precedes explosive directional moves.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


@dataclass
class _OpenPosition:
    entry_price: float
    entry_bar: int
    highest_close: float


class VolatilitySqueezeStrategy(BaseStrategy):
    """Volatility squeeze breakout strategy.

    Entry conditions:
      1. BB Width percentile rank is below squeeze_percentile (compressed volatility)
      2. Close price is above SMA(20) (uptrend filter)
      3. Momentum (rate of change) is positive (directional confirmation)
      4. ATR% is above minimum threshold (sufficient volatility for profit)

    Exit conditions:
      1. Trailing stop hit: close < highest_since_entry - atr_trail_multiplier * ATR
      2. Time stop: bars held >= max_hold_bars
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)

        self._squeeze_lookback = int(self._config.get("squeeze_lookback", 50))
        self._squeeze_percentile = float(self._config.get("squeeze_percentile", 0.20))
        self._sma_period = int(self._config.get("sma_period", 20))
        self._momentum_period = int(self._config.get("momentum_period", 10))
        self._atr_trail_multiplier = float(self._config.get("atr_trail_multiplier", 3.0))
        self._max_hold_bars = int(self._config.get("max_hold_bars", 30))
        self._min_atr_pct = float(self._config.get("min_atr_pct", 0.005))

        if self._squeeze_lookback < 10:
            raise ValueError("squeeze_lookback must be >= 10")
        if not 0.0 < self._squeeze_percentile < 1.0:
            raise ValueError("squeeze_percentile must be in (0, 1)")
        if self._momentum_period < 2:
            raise ValueError("momentum_period must be >= 2")
        if self._atr_trail_multiplier <= 0:
            raise ValueError("atr_trail_multiplier must be > 0")
        if self._max_hold_bars < 1:
            raise ValueError("max_hold_bars must be >= 1")

        self._bb_width_history: dict[str, deque[float]] = {}
        self._close_history: dict[str, deque[float]] = {}
        self._position: dict[str, _OpenPosition] = {}
        self._bar_count: dict[str, int] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        close = self._coerce_float(indicators.get("close_price"))
        if close is None or close <= 0:
            return self._hold(symbol, 0.0, "Waiting for valid close price")

        bb_upper_dist = self._coerce_float(indicators.get("bb_upper_dist"))
        bb_lower_dist = self._coerce_float(indicators.get("bb_lower_dist"))
        if bb_upper_dist is None or bb_lower_dist is None:
            return self._hold(symbol, close, "Waiting for Bollinger Band data")

        atr = self._coerce_float(indicators.get("atr_14"))
        atr_pct = self._coerce_float(indicators.get("atr_pct"))
        if atr is None or atr_pct is None:
            return self._hold(symbol, close, "Waiting for ATR data")

        sma_20 = self._coerce_float(indicators.get("sma_20"))
        if sma_20 is None:
            return self._hold(symbol, close, "Waiting for SMA(20)")

        # Compute BB Width = (upper_dist + lower_dist) / close
        bb_width = (bb_upper_dist + bb_lower_dist) / close

        # Track state
        bar_count = self._bar_count.get(symbol, 0) + 1
        self._bar_count[symbol] = bar_count

        width_hist = self._bb_width_history.setdefault(symbol, deque(maxlen=self._squeeze_lookback))
        close_hist = self._close_history.setdefault(symbol, deque(maxlen=self._momentum_period + 1))

        width_hist.append(bb_width)
        close_hist.append(close)

        # Check for open position — handle exit first
        open_pos = self._position.get(symbol)
        if open_pos is not None:
            return self._handle_exit(symbol, close, atr, bar_count, open_pos)

        # Need enough history for lookback calculations
        if len(width_hist) < min(self._squeeze_lookback, 10):
            return self._hold(
                symbol,
                close,
                f"Warming up BB width history: {len(width_hist)}/{self._squeeze_lookback}",
            )

        if len(close_hist) < self._momentum_period + 1:
            return self._hold(
                symbol,
                close,
                f"Warming up momentum history: {len(close_hist)}/{self._momentum_period + 1}",
            )

        # --- Entry logic ---

        # 1. Squeeze detection: BB Width percentile rank
        pct_rank = self._percentile_rank(list(width_hist), bb_width)

        # 2. Trend filter: price above SMA(20)
        uptrend = close > sma_20

        # 3. Momentum: rate of change over momentum_period bars
        momentum = self._compute_momentum(list(close_hist))

        # 4. Minimum volatility filter
        sufficient_vol = atr_pct >= self._min_atr_pct

        if pct_rank < self._squeeze_percentile and uptrend and momentum > 0 and sufficient_vol:
            self._position[symbol] = _OpenPosition(
                entry_price=close,
                entry_bar=bar_count,
                highest_close=close,
            )
            confidence = min(1.0, (1.0 - pct_rank) / (1.0 - self._squeeze_percentile + 0.01))
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close,
                confidence=confidence,
                reason=(
                    f"Squeeze breakout: BB Width pct={pct_rank:.2f} "
                    f"(<{self._squeeze_percentile:.2f}), mom={momentum:.4f}, "
                    f"ATR%={atr_pct:.4f}, above SMA(20)={uptrend}"
                ),
                indicators={
                    "bb_width": bb_width,
                    "bb_width_pct_rank": pct_rank,
                    "momentum": momentum,
                    "atr_pct": atr_pct,
                    "sma_20": sma_20,
                },
            )

        return self._hold(
            symbol,
            close,
            (
                f"No entry: BB pct={pct_rank:.2f} (need <{self._squeeze_percentile:.2f}), "
                f"uptrend={uptrend}, mom={momentum:.4f}, vol_ok={sufficient_vol}"
            ),
            indicators={
                "bb_width": bb_width,
                "bb_width_pct_rank": pct_rank,
                "momentum": momentum,
                "atr_pct": atr_pct,
                "sma_20": sma_20,
            },
        )

    def get_name(self) -> str:
        return f"VolatilitySqueeze(LB={self._squeeze_lookback},Pct={self._squeeze_percentile})"

    def _handle_exit(
        self,
        symbol: str,
        close: float,
        atr: float,
        bar_count: int,
        pos: _OpenPosition,
    ) -> Signal:
        bars_held = bar_count - pos.entry_bar

        # Update highest close
        if close > pos.highest_close:
            pos = _OpenPosition(
                entry_price=pos.entry_price,
                entry_bar=pos.entry_bar,
                highest_close=close,
            )
            self._position[symbol] = pos

        # Trailing stop exit
        trail_stop = pos.highest_close - self._atr_trail_multiplier * atr
        exit_by_trail = close < trail_stop and bars_held > 2

        # Time stop exit
        exit_by_time = bars_held >= self._max_hold_bars

        if exit_by_trail or exit_by_time:
            del self._position[symbol]
            reason = (
                f"Exit by {'trailing stop' if exit_by_trail else 'time stop'}: "
                f"held={bars_held} bars, trail={trail_stop:.2f}, "
                f"high={pos.highest_close:.2f}"
            )
            pnl_pct = (close - pos.entry_price) / pos.entry_price * 100
            confidence = 0.8 if pnl_pct > 0 else 0.6
            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close,
                confidence=confidence,
                reason=reason,
                indicators={
                    "bars_held": float(bars_held),
                    "trail_stop": trail_stop,
                    "highest_close": pos.highest_close,
                    "pnl_pct": pnl_pct,
                },
            )

        return self._hold(
            symbol,
            close,
            f"Position open: held={bars_held}/{self._max_hold_bars}, "
            f"trail={trail_stop:.2f}, high={pos.highest_close:.2f}",
            indicators={
                "bars_held": float(bars_held),
                "trail_stop": trail_stop,
                "highest_close": pos.highest_close,
            },
        )

    @staticmethod
    def _percentile_rank(history: list[float], current: float) -> float:
        """Compute what fraction of historical values are below current."""
        if not history:
            return 1.0
        below = sum(1 for v in history if v < current)
        return below / len(history)

    @staticmethod
    def _compute_momentum(close_history: list[float]) -> float:
        """Compute rate of change: (close - close[N]) / close[N]."""
        if len(close_history) < 2:
            return 0.0
        current = close_history[-1]
        past = close_history[0]
        if past <= 0:
            return 0.0
        return (current - past) / past

    def _coerce_float(self, value: object) -> float | None:
        if isinstance(value, (int, float)):
            numeric = float(value)
            if math.isfinite(numeric):
                return numeric
            return None
        return None

    def _hold(
        self,
        symbol: str,
        price: float,
        reason: str,
        indicators: dict[str, float] | None = None,
    ) -> Signal:
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            confidence=0.0,
            reason=reason,
            indicators=indicators or {},
        )
