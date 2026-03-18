"""Guard protocol - composable risk checks.

Adapted from OpenAlice guard pattern. Guards are composable, chainable
risk checks that can short-circuit on first rejection.

Example:
    pipeline = GuardPipeline([
        CircuitBreakerGuard(),
        PositionLimitGuard(max_position_pct=0.1),
        CooldownGuard(min_candles=3),
    ])
    result = pipeline.check(context)
    if result.blocked:
        logger.warning(f"Order blocked: {result.reason}")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.utils.logger import get_logger


@dataclass(frozen=True)
class GuardContext:
    """Context passed to guards for evaluation."""

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float | None = None
    portfolio_value: float = 0.0
    current_position: float = 0.0
    signal_confidence: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class GuardResult:
    """Result of a guard check."""

    passed: bool
    reason: str = ""
    guard_name: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def blocked(self) -> bool:
        """True if guard blocked the order."""
        return not self.passed

    @staticmethod
    def allow(reason: str = "", guard_name: str = "", metadata: dict | None = None) -> GuardResult:
        """Create a passing result."""
        return GuardResult(True, reason, guard_name, metadata)

    @staticmethod
    def block(reason: str, guard_name: str = "", metadata: dict | None = None) -> GuardResult:
        """Create a blocking result."""
        return GuardResult(False, reason, guard_name, metadata)


class Guard(ABC):
    """Abstract base class for composable guards."""

    def __init__(self) -> None:
        self._logger = get_logger(self.__class__.__name__)

    @abstractmethod
    def check(self, context: GuardContext) -> GuardResult:
        """Evaluate the guard against the context.

        Args:
            context: Order context with symbol, quantity, etc.

        Returns:
            GuardResult indicating pass or block
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Guard name for logging."""
        pass


class GuardPipeline:
    """Chains multiple guards together, short-circuiting on first block."""

    def __init__(self, guards: list[Guard] | None = None) -> None:
        """Initialize pipeline with guards.

        Args:
            guards: List of guards to evaluate in order
        """
        self._guards = guards or []
        self._logger = get_logger(self.__class__.__name__)

    def add(self, guard: Guard) -> GuardPipeline:
        """Add a guard to the pipeline (chainable).

        Args:
            guard: Guard to add

        Returns:
            Self for chaining
        """
        self._guards.append(guard)
        return self

    def check(self, context: GuardContext) -> GuardResult:
        """Evaluate all guards in sequence.

        Short-circuits on first blocking guard.

        Args:
            context: Order context

        Returns:
            First blocking result, or passing result if all pass
        """
        for guard in self._guards:
            result = guard.check(context)
            if result.blocked:
                self._logger.info(
                    "Guard '%s' blocked %s %s: %s",
                    guard.name,
                    context.side,
                    context.symbol,
                    result.reason,
                )
                return GuardResult.block(
                    reason=result.reason,
                    guard_name=guard.name,
                    metadata=result.metadata,
                )

        return GuardResult.allow(
            reason="All guards passed",
            guard_name="Pipeline",
            metadata={"guards_checked": len(self._guards)},
        )

    def get_guard_names(self) -> list[str]:
        """Get names of all guards in pipeline."""
        return [g.name for g in self._guards]


# Example guard implementations


class CircuitBreakerGuard(Guard):
    """Blocks orders when circuit breaker is tripped."""

    def __init__(self, risk_manager: Any) -> None:
        super().__init__()
        self._risk_manager = risk_manager

    @property
    def name(self) -> str:
        return "CircuitBreaker"

    def check(self, context: GuardContext) -> GuardResult:
        is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            return GuardResult.block(reason, self.name)
        return GuardResult.allow("Trading allowed", self.name)


class PositionLimitGuard(Guard):
    """Blocks orders that would exceed position limits."""

    def __init__(
        self,
        max_position_pct: float = 0.1,
        risk_manager: Any | None = None,
    ) -> None:
        super().__init__()
        self._max_position_pct = max_position_pct
        self._risk_manager = risk_manager

    @property
    def name(self) -> str:
        return "PositionLimit"

    def check(self, context: GuardContext) -> GuardResult:
        if context.side == "SELL":
            # Allow sells (closing positions)
            return GuardResult.allow("Sell allowed", self.name)

        # Calculate position after this order
        order_value = context.quantity * (context.price or 0)
        current_value = context.current_position * (context.price or 0)
        portfolio_value = context.portfolio_value or 1  # Avoid div by zero

        new_position_pct = (current_value + order_value) / portfolio_value

        if new_position_pct > self._max_position_pct:
            return GuardResult.block(
                f"Position would exceed {self._max_position_pct * 100:.0f}% "
                f"({new_position_pct * 100:.1f}%)",
                self.name,
                {"max_pct": self._max_position_pct, "new_pct": new_position_pct},
            )

        return GuardResult.allow("Within position limits", self.name)


class CooldownGuard(Guard):
    """Enforces minimum time between orders for a symbol."""

    def __init__(self, min_seconds: float = 180.0) -> None:
        super().__init__()
        self._min_seconds = min_seconds
        self._last_order_time: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "Cooldown"

    def check(self, context: GuardContext) -> GuardResult:
        import time

        symbol = context.symbol
        now = time.time()
        last_time = self._last_order_time.get(symbol, 0)

        elapsed = now - last_time
        if elapsed < self._min_seconds:
            return GuardResult.block(
                f"Cooldown active: {elapsed:.0f}s < {self._min_seconds}s",
                self.name,
                {"elapsed": elapsed, "required": self._min_seconds},
            )

        # Update last order time (assume this order will proceed)
        self._last_order_time[symbol] = now
        return GuardResult.allow("Cooldown passed", self.name)


class ConfidenceThresholdGuard(Guard):
    """Blocks orders below confidence threshold."""

    def __init__(self, min_confidence: float = 0.5) -> None:
        super().__init__()
        self._min_confidence = min_confidence

    @property
    def name(self) -> str:
        return "ConfidenceThreshold"

    def check(self, context: GuardContext) -> GuardResult:
        if context.signal_confidence < self._min_confidence:
            return GuardResult.block(
                f"Confidence {context.signal_confidence:.2f} < {self._min_confidence:.2f}",
                self.name,
                {"confidence": context.signal_confidence, "min": self._min_confidence},
            )
        return GuardResult.allow("Confidence sufficient", self.name)
