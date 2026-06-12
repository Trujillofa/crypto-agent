"""Dislocation event entry strategy (standalone, long-only).

Emits BUY on cross-venue basis spread (binance_usdm - bybit) >= min_spread_bps (positive only).
Uses internal bar-count cooldown (tied to horizon in the autoresearch family) to deduplicate
consecutive extreme bars into a single event entry. Exit is by time_stop (configured via
trading_execution.exit_rules in the family sampler), never emits SELL.

Indicators row must include "cross_venue_basis_spread_bps" (already joined by features/reader.py).
Returns HOLD (no signal) if the value is missing/None for either venue.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class DislocationEventStrategy(BaseStrategy):
    """Standalone long-only strategy for cross-venue dislocation events.

    BUY when cross_venue_basis_spread_bps >= min_spread_bps (positive tail extreme).
    Cooldown after fire prevents re-entry on consecutive extreme bars.
    Never emits SELL; the family configures a time stop exit at the horizon.

    Config:
        min_spread_bps: minimum |positive| spread in bps to trigger (4.5-7.0 validated range)
        cooldown_bars: bars to wait after a BUY before allowing another (family ties to horizon)
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._min_spread_bps: float = float(self._config.get("min_spread_bps", 5.0))
        self._cooldown_bars: int = int(self._config.get("cooldown_bars", 12))

        if self._min_spread_bps < 0.0:
            self._min_spread_bps = 0.0
        if self._cooldown_bars < 1:
            self._cooldown_bars = 1

        # Per-symbol bar counter and last BUY fire bar (for cooldown dedup)
        self._bar_count: dict[str, int] = {}
        self._last_fire_bar: dict[str, int] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate cross-venue basis spread and emit deduplicated long entry or HOLD.

        Returns BUY only on qualifying positive spread after cooldown.
        Returns HOLD (no trade signal) for sub-threshold, negative, or missing spread.
        Never returns SELL.
        """
        spread = indicators.get("cross_venue_basis_spread_bps")
        close_price = float(indicators.get("close_price", 0.0))

        # Always advance bar count (evaluate is invoked once per bar in sequence)
        bar = self._bar_count.get(symbol, 0) + 1
        self._bar_count[symbol] = bar

        if spread is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason="missing_cross_venue_basis_spread",
                indicators=indicators,
            )

        spread = float(spread)

        # Positive-only (v0 per probe evidence and caveats)
        if spread < self._min_spread_bps:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"spread_below_threshold ({spread:.2f} < {self._min_spread_bps:.2f})",
                indicators=indicators,
            )

        # Cooldown check (dedup consecutive extremes; cooldown_bars tied to horizon in family)
        last = self._last_fire_bar.get(symbol, -(10**9))
        bars_since = bar - last
        if bars_since < self._cooldown_bars:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=f"cooldown_active (bars_since={bars_since} < {self._cooldown_bars})",
                indicators=indicators,
            )

        # Emit BUY and record fire bar
        self._last_fire_bar[symbol] = bar
        # Confidence scales modestly with extremity (capped); threshold at 0.6
        excess = spread - self._min_spread_bps
        confidence = min(0.6 + (excess / 5.0), 1.0)

        return Signal(
            type=SignalType.BUY,
            symbol=symbol,
            price=close_price,
            confidence=confidence,
            reason=f"cross_venue_dislocation_positive min_bps={self._min_spread_bps:.2f} spread={spread:.2f}",
            indicators=indicators,
        )

    def get_name(self) -> str:
        return "dislocation_event"
