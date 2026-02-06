from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Ohlcv:
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float

    @property
    def open_time_utc(self) -> datetime:
        return self.open_time.astimezone(timezone.utc)

    @property
    def close_time_utc(self) -> datetime:
        return self.close_time.astimezone(timezone.utc)

    @property
    def change_percent(self) -> float:
        """Calculate price change percentage."""
        if self.open_price == 0:
            return 0.0
        return ((self.close_price - self.open_price) / self.open_price) * 100

    @property
    def body_size(self) -> float:
        """Calculate the size of the candle body."""
        return abs(self.close_price - self.open_price)

    @property
    def upper_wick(self) -> float:
        """Calculate the upper wick size."""
        return self.high_price - max(self.open_price, self.close_price)

    @property
    def lower_wick(self) -> float:
        """Calculate the lower wick size."""
        return min(self.open_price, self.close_price) - self.low_price
