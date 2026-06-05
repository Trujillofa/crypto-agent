"""Long-only funding normalization strategy (primary trigger).

Enters when funding normalizes from an extreme negative (crowded shorts),
not when funding first hits the extreme. One entry per extreme→normalize cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType

if TYPE_CHECKING:
    from collections.abc import Mapping


class _FundingState(StrEnum):
    IDLE = "idle"
    EXTREME_NEGATIVE = "extreme_negative"
    COOLDOWN_NEGATIVE = "cooldown_negative"


@dataclass
class FundingNormalizationConfig:
    entry_threshold: float = 0.00016
    exit_threshold: float = 0.00015
    long_only: bool = True


class FundingNormalizationStrategy(BaseStrategy):
    """Mean-reversion long after negative funding crowding unwinds."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._norm_config = FundingNormalizationConfig(
            entry_threshold=float(self._config.get("entry_threshold", 0.00016)),
            exit_threshold=float(self._config.get("exit_threshold", 0.00015)),
            long_only=bool(self._config.get("long_only", True)),
        )
        self._state: dict[str, _FundingState] = {}
        self._prior_extreme: dict[str, float] = {}

    def _state_for(self, symbol: str) -> _FundingState:
        return self._state.get(symbol, _FundingState.IDLE)

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        funding_rate = indicators.get("funding_rate")
        price = float(indicators.get("close_price", 0.0))

        if funding_rate is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=price,
                confidence=0.0,
                reason="no_funding_rate",
                indicators=indicators,
            )

        rate = float(funding_rate)
        entry_thresh = self._norm_config.entry_threshold
        exit_thresh = self._norm_config.exit_threshold
        normalized = abs(rate) < exit_thresh
        state = self._state_for(symbol)

        if rate <= -entry_thresh:
            if state in {_FundingState.IDLE, _FundingState.COOLDOWN_NEGATIVE}:
                self._state[symbol] = _FundingState.EXTREME_NEGATIVE
                self._prior_extreme[symbol] = rate
            elif state == _FundingState.EXTREME_NEGATIVE:
                self._prior_extreme[symbol] = min(self._prior_extreme.get(symbol, rate), rate)
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=price,
                confidence=0.0,
                reason="funding_extreme_negative",
                indicators=indicators,
            )

        if state == _FundingState.EXTREME_NEGATIVE and normalized:
            self._state[symbol] = _FundingState.COOLDOWN_NEGATIVE
            confidence = min(abs(self._prior_extreme.get(symbol, rate)) / entry_thresh, 1.0)
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=price,
                confidence=confidence,
                reason="funding_normalized_from_negative",
                indicators=indicators,
            )

        if normalized and state != _FundingState.EXTREME_NEGATIVE:
            self._state[symbol] = _FundingState.IDLE

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            confidence=0.0,
            reason="funding_normalization_wait",
            indicators=indicators,
        )

    def get_name(self) -> str:
        return "funding_normalization"
