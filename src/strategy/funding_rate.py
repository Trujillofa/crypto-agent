"""Directional funding rate strategy.

Generates signals based on extreme funding rates:
- Go LONG when funding rate is very negative (< -threshold)
  (shorts paying longs excessively = sentiment too bearish, mean reversion likely)
- Go SHORT when funding rate is very positive (> threshold)
  (longs paying shorts excessively = sentiment too bullish, mean reversion likely)

Configuration:
    entry_threshold: Funding rate threshold for entry (default: 0.0005 = 0.05%)
    exit_threshold: Funding rate threshold for exit (default: 0.0001 = 0.01%)
    lookback_periods: Number of funding periods to average (default: 1 = use current)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class FundingRateConfig:
    """Configuration for funding rate strategy."""

    entry_threshold: float = 0.0005  # 0.05%
    exit_threshold: float = 0.0001  # 0.01%
    lookback_periods: int = 1


class FundingRateStrategy(BaseStrategy):
    """Directional strategy based on extreme funding rates.

    Thesis: Extreme funding rates indicate overcrowded positioning.
    When funding is very positive, longs are overextended -> go short.
    When funding is very negative, shorts are overextended -> go long.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._funding_config = FundingRateConfig(
            entry_threshold=float(self._config.get("entry_threshold", 0.0005)),
            exit_threshold=float(self._config.get("exit_threshold", 0.0001)),
            lookback_periods=int(self._config.get("lookback_periods", 1)),
        )

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate funding rate and generate signal.

        Expects indicators dict to contain:
        - funding_rate: Current funding rate (e.g., 0.0001 = 0.01%)
        - funding_rate_avg: Average funding rate over lookback (optional)

        Args:
            symbol: Trading pair symbol
            indicators: Dictionary of indicator values

        Returns:
            Signal: BUY if funding very negative, SELL if very positive, HOLD otherwise
        """
        funding_rate = indicators.get("funding_rate")

        if funding_rate is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=float(indicators.get("close_price", 0.0)),
                confidence=0.0,
                reason="no_funding_rate",
                indicators=indicators,
            )

        funding_rate = float(funding_rate)
        entry_thresh = self._funding_config.entry_threshold
        exit_thresh = self._funding_config.exit_threshold

        # Generate signal based on extreme funding
        if funding_rate <= -entry_thresh:
            # Very negative funding = shorts overpaying = too bearish = go long
            confidence = min(abs(funding_rate) / entry_thresh, 1.0)
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=float(indicators.get("close_price", 0.0)),
                confidence=confidence,
                reason="extreme_negative_funding",
                indicators=indicators,
            )
        elif funding_rate >= entry_thresh:
            # Very positive funding = longs overpaying = too bullish = go short
            confidence = min(funding_rate / entry_thresh, 1.0)
            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=float(indicators.get("close_price", 0.0)),
                confidence=confidence,
                reason="extreme_positive_funding",
                indicators=indicators,
            )
        elif abs(funding_rate) < exit_thresh:
            # Funding near zero = no signal, consider exiting
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=float(indicators.get("close_price", 0.0)),
                confidence=0.0,
                reason="funding_normalized",
                indicators=indicators,
            )
        else:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=float(indicators.get("close_price", 0.0)),
                confidence=0.0,
                reason="no_extreme_funding",
                indicators=indicators,
            )

    def get_name(self) -> str:
        return "funding_rate"
