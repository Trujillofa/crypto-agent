from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalType(Enum):
    """Trading signal type."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Signal:
    """Trading signal from a strategy.

    Attributes:
        type: Signal type (BUY/SELL/HOLD)
        symbol: Trading pair symbol (e.g., BTCUSDT)
        price: Current price when signal was generated
        confidence: Signal confidence (0.0 to 1.0)
        reason: Human-readable reason for the signal
        indicators: Dictionary of indicator values that led to this signal
        trading_mode: "spot" or "futures" - determines how executor interprets signal
    """

    type: SignalType
    symbol: str
    price: float
    confidence: float
    reason: str
    indicators: dict[str, float]
    trading_mode: str = "spot"  # "spot" or "futures"

    def __str__(self) -> str:
        return f"Signal({self.type.value}): {self.symbol} @ {self.price:.2f} ({self.trading_mode}) - {self.reason}"
