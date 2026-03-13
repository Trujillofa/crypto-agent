from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


@dataclass
class MacroEvent:
    """A scheduled macro-economic event."""

    name: str
    timestamp: float  # Unix timestamp of the event
    expected_value: float | None = None
    actual_value: float | None = None
    surprise_pct: float = 0.0  # (actual - expected) / expected * 100
    direction: str = ""  # "above" or "below" expectations


class MacroEventFeed:
    """Feed of macro-economic events (CPI, PCE, FOMC, NFP, etc.).

    In production, this connects to a data provider API (e.g., FRED, Alpha Vantage,
    or a financial calendar service). In paper/test mode, it can be driven manually.
    """

    def __init__(self) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._events: list[MacroEvent] = []
        self._active_event: MacroEvent | None = None
        self._event_window_seconds: float = 3600.0  # 1 hour reaction window

    def push_event(self, event: MacroEvent) -> None:
        """Push a new macro event (e.g., from a data feed or manual injection)."""
        self._events.append(event)
        self._active_event = event
        self._logger.info(
            "Macro event received: %s surprise=%.2f%% direction=%s",
            event.name,
            event.surprise_pct,
            event.direction,
        )

    def get_active_event(self) -> MacroEvent | None:
        """Get the currently active event within the reaction window."""
        if self._active_event is None:
            return None

        elapsed = time.time() - self._active_event.timestamp
        if elapsed > self._event_window_seconds:
            self._active_event = None
            return None

        return self._active_event

    def clear(self) -> None:
        """Clear the active event."""
        self._active_event = None


class MacroVolatilityStrategy(BaseStrategy):
    """Macro-Volatility Bridge Strategy for 2026.

    Core idea: Crypto reacts faster to Fed announcements, CPI/PCE data, and other
    macro events than any other asset class. This strategy captures the initial
    "reaction spike" (15-60 minutes) when data misses expectations.

    Signal logic:
    - When a macro event occurs with a significant surprise (above threshold):
      - If surprise is POSITIVE (data better than expected → risk-on):
        BUY with high confidence for a short-duration momentum trade
      - If surprise is NEGATIVE (data worse than expected → risk-off):
        SELL with high confidence
    - ATR filter ensures sufficient volatility for the move to be tradeable
    - Confidence scales with surprise magnitude
    - Only trades within the reaction window (default 1 hour after event)

    Without an active macro event, this strategy returns HOLD.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        # Surprise thresholds
        self._min_surprise_pct = float(self._config.get("min_surprise_pct", 0.3))
        self._strong_surprise_pct = float(self._config.get("strong_surprise_pct", 1.0))

        # Volatility gate
        self._min_atr_pct = float(self._config.get("min_atr_pct", 0.003))

        # Momentum confirmation
        self._require_momentum_confirmation = bool(
            self._config.get("require_momentum_confirmation", True)
        )

        # Event feed (injected)
        self._event_feed: MacroEventFeed | None = None

        # Track if we already traded this event
        self._traded_events: set[str] = set()

    def set_event_feed(self, feed: MacroEventFeed) -> None:
        """Inject a MacroEventFeed instance."""
        self._event_feed = feed

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate macro event context against current indicators."""
        required = {"close_price", "atr_pct"}
        for k in required:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        close = indicators["close_price"]
        atr_pct = indicators["atr_pct"]

        # No event feed or no active event → HOLD
        if self._event_feed is None:
            return self._hold_signal(symbol, close, "No event feed configured")

        event = self._event_feed.get_active_event()
        if event is None:
            return self._hold_signal(symbol, close, "No active macro event")

        # Already traded this event
        event_key = f"{event.name}_{event.timestamp}"
        if event_key in self._traded_events:
            return self._hold_signal(symbol, close, f"Already traded {event.name}")

        # Surprise below threshold
        abs_surprise = abs(event.surprise_pct)
        if abs_surprise < self._min_surprise_pct:
            return self._hold_signal(
                symbol, close,
                f"{event.name} surprise {event.surprise_pct:.2f}% below threshold",
            )

        # Volatility gate
        if atr_pct < self._min_atr_pct:
            return self._hold_signal(
                symbol, close,
                f"ATR% {atr_pct:.4f} too low for macro trade",
            )

        # Momentum confirmation (optional): check RSI aligns with direction
        if self._require_momentum_confirmation:
            rsi = indicators.get("rsi_14")
            if rsi is not None:
                if event.direction == "above" and rsi < 40:
                    return self._hold_signal(
                        symbol, close,
                        f"Positive surprise but RSI={rsi:.1f} bearish (no momentum confirmation)",
                    )
                if event.direction == "below" and rsi > 60:
                    return self._hold_signal(
                        symbol, close,
                        f"Negative surprise but RSI={rsi:.1f} bullish (no momentum confirmation)",
                    )

        # Calculate confidence based on surprise magnitude
        if abs_surprise >= self._strong_surprise_pct:
            confidence = 0.85
        else:
            ratio = abs_surprise / self._strong_surprise_pct
            confidence = 0.55 + ratio * 0.30

        confidence = min(0.95, confidence)

        # Mark event as traded
        self._traded_events.add(event_key)

        if event.direction == "above" or event.surprise_pct > 0:
            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close,
                confidence=confidence,
                reason=(
                    f"Macro {event.name}: +{event.surprise_pct:.2f}% surprise (risk-on momentum)"
                ),
                indicators={
                    "close_price": close,
                    "atr_pct": atr_pct,
                    "macro_surprise_pct": event.surprise_pct,
                },
            )
        else:
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close,
                confidence=confidence,
                reason=(
                    f"Macro {event.name}: {event.surprise_pct:.2f}% surprise (risk-off momentum)"
                ),
                indicators={
                    "close_price": close,
                    "atr_pct": atr_pct,
                    "macro_surprise_pct": event.surprise_pct,
                },
            )

        self._logger.info("%s generated %s for %s", self.get_name(), signal, symbol)
        return signal

    def _hold_signal(self, symbol: str, price: float, reason: str) -> Signal:
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            confidence=0.0,
            reason=reason,
            indicators={"close_price": price},
        )

    def get_name(self) -> str:
        return "MacroVolatility"
