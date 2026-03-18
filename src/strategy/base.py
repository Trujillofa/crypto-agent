from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from src.strategy.signals import Signal


class BaseStrategy(ABC):
    """Abstract base class for trading strategies.

    All strategies must implement these methods:
    - evaluate(): Generate trading signals based on indicators
    - get_name(): Return strategy name for logging/metrics
    """

    # Optional: Declare required timeframes for multi-timeframe (MTF) strategies.
    # Single-timeframe strategies should leave this empty (default).
    # Example for 4h regime + 1h entry:
    #   REQUIRED_TIMEFRAMES = {
    #       'entry': '1h',
    #       'regime': '4h',
    #   }
    REQUIRED_TIMEFRAMES: dict[str, str] = {}

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Initialize strategy with configuration.

        Args:
            config: Strategy-specific configuration dictionary
        """
        self._config = config or {}

    @abstractmethod
    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators and generate a trading signal.

        Args:
            symbol: Trading pair symbol
            indicators: Dictionary of latest indicator values for the symbol

        Returns:
            Signal: Trading signal (BUY/SELL/HOLD) with metadata

        Raises:
            ValueError: If required indicators are missing
        """
        pass

    def get_name(self) -> str:
        """Return the name of this strategy."""
        return self.__class__.__name__

    def get_config(self) -> Mapping[str, object]:
        """Return the strategy configuration."""
        return self._config
