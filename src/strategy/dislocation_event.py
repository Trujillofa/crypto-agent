"""Dislocation event entry strategy (standalone, long-only).

Emits BUY on cross-venue basis spread (binance_usdm - bybit) >= min_spread_bps (positive only)
or, in rolling mode, on values >= the no-lookahead trailing tail-percentile high threshold.
Uses internal bar-count cooldown (tied to horizon in the autoresearch families) to deduplicate
consecutive extreme bars into a single event entry. Exit is by time_stop (configured via
trading_execution.exit_rules in the family sampler), never emits SELL.

Supports threshold_mode "fixed" (default = exact v0 behavior with min_spread_bps) or "rolling"
(trailing 90d tail_pct high, using strictly prior bars only, min window length 2 else HOLD).
metric controls the row key: "basis_spread" -> "cross_venue_basis_spread_bps",
"premium_spread" -> "cross_venue_premium_spread". min_spread_bps ignored in rolling mode.

Indicators row must include the chosen spread key (joined by features/reader.py) and "time"
(for rolling expiry; engine always supplies it). "close_price" for Signal price.
Returns HOLD (no signal) if the value is missing/None.
"""

from __future__ import annotations

import bisect
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


class DislocationEventStrategy(BaseStrategy):
    """Standalone long-only strategy for cross-venue dislocation events.

    Fixed mode (default): BUY when chosen spread >= min_spread_bps (positive tail extreme).
    Rolling mode: BUY when chosen spread >= tail_threshold_high of strictly prior values
    in the trailing rolling_days window (maintained with deque + bisect sorted list for
    O(W) expiry/insert). Window length <2 -> HOLD reason "rolling_warmup". Confidence fixed
    at 0.75 in rolling (to ensure aggregator compatibility; premium units 4 orders smaller).

    Cooldown after fire prevents re-entry on consecutive extreme bars (shared across modes).
    Never emits SELL; the family configures a time stop exit at the horizon.

    Config:
        threshold_mode: "fixed" (default, v0 behavior) or "rolling"
        metric: "basis_spread" (default, cross_venue_basis_spread_bps) or "premium_spread"
        tail_pct: int tail for rolling high-percentile (default 5)
        rolling_days: int trailing window (default 90)
        min_spread_bps: abs threshold for fixed mode (ignored in rolling)
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

        self._threshold_mode: str = str(self._config.get("threshold_mode", "fixed")).strip().lower()
        if self._threshold_mode not in ("fixed", "rolling"):
            self._threshold_mode = "fixed"
        self._metric: str = str(self._config.get("metric", "basis_spread")).strip().lower()
        if self._metric not in ("basis_spread", "premium_spread"):
            self._metric = "basis_spread"
        self._tail_pct: int = int(self._config.get("tail_pct", 5))
        if self._tail_pct < 1:
            self._tail_pct = 1
        if self._tail_pct > 50:
            self._tail_pct = 50
        self._rolling_days: int = int(self._config.get("rolling_days", 90))
        if self._rolling_days < 1:
            self._rolling_days = 1

        # Per-symbol bar counter and last BUY fire bar (for cooldown dedup) - shared by modes
        self._bar_count: dict[str, int] = {}
        self._last_fire_bar: dict[str, int] = {}

        # Rolling state: time-ordered deque of (time, value) for expiry; bisect-maintained
        # sorted ascending list of values for O(1) rank lookup after O(W) ins/del.
        self._windows: dict[str, deque[tuple[datetime, float]]] = {}
        self._sorted_vals: dict[str, list[float]] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate cross-venue spread (basis or premium per config) and emit deduplicated long entry or HOLD.

        Fixed mode: BUY on spread >= min_spread_bps (exact v0 behavior for basis default).
        Rolling mode: maintain per-symbol trailing (time, value) window of strictly prior bars
        (expiry: time >= current - rolling_days; min 2 values else HOLD "rolling_warmup").
        Threshold = tail_threshold_high of window (high tail positive extreme). Current value
        appended AFTER the decision (no-lookahead). Confidence fixed at 0.75 in rolling.
        Cooldown (bar count) applies in both modes after the value-threshold check.
        Never returns SELL.
        """
        spread_key = (
            "cross_venue_basis_spread_bps"
            if self._metric == "basis_spread"
            else "cross_venue_premium_spread"
        )
        raw_val = indicators.get(spread_key)
        close_price = float(indicators.get("close_price", 0.0))

        # Always advance bar count (evaluate is invoked once per bar in sequence by engine)
        bar = self._bar_count.get(symbol, 0) + 1
        self._bar_count[symbol] = bar

        if raw_val is None:
            missing_reason = (
                "missing_cross_venue_basis_spread"
                if self._metric == "basis_spread"
                else "missing_cross_venue_premium_spread"
            )
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=missing_reason,
                indicators=indicators,
            )

        val = float(raw_val)
        is_rolling = self._threshold_mode == "rolling"

        # Determine eligibility from threshold (fixed min or rolling prior-window tail)
        eligible = False
        th_for_reason = 0.0
        win_len = 0
        current_time: datetime | None = None

        if is_rolling:
            current_time = self._get_row_time(indicators)
            if symbol not in self._windows:
                self._windows[symbol] = deque()
                self._sorted_vals[symbol] = []
            deq: deque[tuple[datetime, float]] = self._windows[symbol]
            sv: list[float] = self._sorted_vals[symbol]

            if current_time is not None:
                cutoff = current_time - timedelta(days=self._rolling_days)
                while deq and deq[0][0] < cutoff:
                    _, ov = deq.popleft()
                    # O(W) remove is allowed; exact match on previously inserted float
                    try:
                        sv.remove(ov)
                    except ValueError:
                        pass

            win_len = len(sv)
            if win_len >= 2:
                th_for_reason = self._tail_threshold_high_sorted(sv, self._tail_pct)
                if val >= th_for_reason:
                    eligible = True
            # else: warmup, eligible stays False; append will still happen below
        else:
            # fixed: exact v0 threshold logic (min_spread_bps); supports metric for key only
            th_for_reason = self._min_spread_bps
            if val >= self._min_spread_bps:
                eligible = True

        # Cooldown check only if value-threshold eligible (dedup; cooldown tied to horizon in families)
        cooldown_reason = ""
        if eligible:
            last = self._last_fire_bar.get(symbol, -(10**9))
            bars_since = bar - last
            if bars_since < self._cooldown_bars:
                eligible = False
                cooldown_reason = (
                    f"cooldown_active (bars_since={bars_since} < {self._cooldown_bars})"
                )

        # Build signal for all cases that had a value (append happens for every such bar in rolling)
        if not eligible:
            if is_rolling:
                if win_len < 2:
                    reason = "rolling_warmup"
                else:
                    reason = f"spread_below_rolling_threshold ({val:.4f} < {th_for_reason:.4f})"
            else:
                reason = f"spread_below_threshold ({val:.2f} < {self._min_spread_bps:.2f})"
            if cooldown_reason:
                reason = cooldown_reason
            sig = Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=close_price,
                confidence=0.0,
                reason=reason,
                indicators=indicators,
            )
        else:
            # Fire BUY
            self._last_fire_bar[symbol] = bar
            if is_rolling:
                confidence = (
                    0.75  # FIXED for rolling (see module doc for rationale vs v0 excess scaling)
                )
                reason = (
                    f"cross_venue_dislocation_rolling_{self._metric} "
                    f"tail{self._tail_pct} spread={val:.4f} th={th_for_reason:.4f}"
                )
            else:
                # v0 confidence (unchanged)
                excess = val - self._min_spread_bps
                confidence = min(0.6 + (excess / 5.0), 1.0)
                reason = f"cross_venue_dislocation_positive min_bps={self._min_spread_bps:.2f} spread={val:.2f}"

            sig = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close_price,
                confidence=confidence,
                reason=reason,
                indicators=indicators,
            )

        # Append current value AFTER evaluating (for next bar's prior window). Rolling only.
        # Must happen for *every* bar with a value (warmup, below, cooldown-blocked, or BUY) so
        # future priors include it. This matches probe _get_window_values (strictly j < i).
        if is_rolling and current_time is not None:
            deq = self._windows[symbol]
            sv = self._sorted_vals[symbol]
            deq.append((current_time, val))
            bisect.insort(sv, val)

        return sig

    def _get_row_time(self, indicators: dict[str, object]) -> datetime | None:
        """Extract time from indicators row (engine always supplies; from features reader / DB timestamptz)."""
        t = indicators.get("time")
        if isinstance(t, datetime):
            return t
        if isinstance(t, str):
            try:
                s = t.replace("Z", "+00:00")
                return datetime.fromisoformat(s)
            except Exception:  # noqa: BLE001
                return None
        return None

    def _tail_threshold_high_sorted(self, sorted_asc: list[float], tail_pct: int) -> float:
        """High-tail percentile on already-sorted ascending list (exact parity with probe _percentile).

        O(1) rank after bisect-maintained list. Mirrors scripts/probe_basis_premium.py exactly
        (no per-bar sort).
        """
        if not sorted_asc:
            return 0.0
        n = len(sorted_asc)
        if n == 1:
            return sorted_asc[0]
        pct = 100.0 - float(tail_pct)
        rank = (n - 1) * pct / 100.0
        low = int(rank)
        high = min(low + 1, n - 1)
        weight = rank - low
        return sorted_asc[low] * (1.0 - weight) + sorted_asc[high] * (weight)

    def get_name(self) -> str:
        return "dislocation_event"


def tail_threshold_high(values: Sequence[float], tail_pct: int) -> float:
    """Exact reference copy of tail_threshold_high from scripts/probe_basis_premium.py (and
    probe_dislocation_event_strategy.py). Sorts internally; used ONLY for the parity test in
    tests/test_dislocation_event_rolling.py to prove identical definition (no src layering).
    The rolling implementation uses the pre-sorted _tail_threshold_high_sorted for efficiency.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (100.0 - tail_pct) / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * (weight)
