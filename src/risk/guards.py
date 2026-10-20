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


def _is_exit_order(context: GuardContext) -> bool:
    """Return True when the order reduces existing exposure.

    ``GuardContext.is_exit`` is authoritative when the caller set it. When it
    is ``None`` (older call sites that only populate ``side``), fall back to
    the legacy inference: a SELL is treated as an exit. This keeps existing
    behaviour for untouched call paths while letting a short ENTRY declare
    ``is_exit=False`` and stay subject to the position limit.
    """
    if context.is_exit is not None:
        return context.is_exit
    return context.side == "SELL"


@dataclass(frozen=True)
class GuardContext:
    """Context passed to guards for evaluation.

    ``quantity`` is the order size in the unit the caller already holds, which
    differs between executors (base-asset qty for spot SELL, quote notional for
    spot BUY, base-asset qty for futures). Guards that reason about *notional*
    should use ``order_notional`` when it is set, because it is unambiguous.
    """

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float | None = None
    portfolio_value: float = 0.0
    current_position: float = 0.0
    signal_confidence: float = 0.0
    metadata: dict[str, Any] | None = None
    # Explicit order notional in quote currency (USDT). When None, guards fall
    # back to quantity * price.
    order_notional: float | None = None
    # Whether the order REDUCES existing exposure (closing a long, covering a
    # short). Guards that exist to stop NEW risk (cooldown, position limit)
    # must not block these: a control that prevents you from exiting a
    # position is a risk amplifier, not a control.
    #
    # This is deliberately separate from ``side``. A SELL can either close a
    # long OR open a short; only the former is an exit. Deriving "is exit"
    # from the side string alone would let a short ENTRY slip past the
    # position limit.
    #
    # Tri-state on purpose:
    #   True  -> definitely an exit, always allow.
    #   False -> definitely NOT an exit (a short entry), apply the limits.
    #   None  -> caller did not say; fall back to the legacy ``side == SELL``
    #            inference so older call sites keep working unchanged.
    is_exit: bool | None = None


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

    def record_fill(self, symbol: str, timestamp: float | None = None) -> None:
        """Notify stateful guards that an order filled.

        Guards that only read state (limits, thresholds) ignore this. Guards
        that track a post-trade window -- notably ``CooldownGuard`` -- use it to
        start the window *after* the fill rather than before the order.
        """
        for guard in self._guards:
            record = getattr(guard, "record_order", None)
            if callable(record):
                record(symbol, timestamp)


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
        # The kill switch and circuit breakers exist to stop NEW risk. They
        # must not prevent an EXIT: a control that blocks you from closing a
        # position is a risk amplifier, not a control. Only a reconciliation
        # block is respected for closes, because mismatched position data
        # makes any close unsafe.
        if _is_exit_order(context):
            is_allowed, reason = self._risk_manager.is_close_allowed()
        else:
            is_allowed, reason = self._risk_manager.is_trading_allowed()
        if not is_allowed:
            return GuardResult.block(reason, self.name)
        return GuardResult.allow("Trading allowed", self.name)


class PositionLimitGuard(Guard):
    """Blocks orders that would exceed position limits.

    The limit is expressed as a fraction of portfolio value, so the guard needs
    both sides of the comparison in the *same* unit (quote currency / USDT
    notional). Executors hold order sizes in different units -- spot BUY uses
    quote notional, spot SELL and futures use base-asset quantity -- so callers
    pass ``order_notional`` explicitly. When it is absent we fall back to
    ``quantity * price``, which is only correct for base-asset quantities.
    """

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

    def _notional(self, context: GuardContext) -> float | None:
        """Order notional in quote currency, or None if it cannot be derived."""
        if context.order_notional is not None:
            return context.order_notional
        if context.price is not None and context.price > 0:
            return context.quantity * context.price
        return None

    def check(self, context: GuardContext) -> GuardResult:
        if _is_exit_order(context):
            # Allow exits (closing positions). A SELL that OPENS a short is not
            # an exit and is still subject to the position limit.
            return GuardResult.allow("Exit allowed", self.name)

        portfolio_value = context.portfolio_value
        if portfolio_value <= 0:
            # Cannot evaluate a percentage without a portfolio value. Fail closed:
            # a limit that cannot be checked must not silently allow the order.
            return GuardResult.block(
                "Portfolio value unavailable; cannot verify position limit",
                self.name,
                {"portfolio_value": portfolio_value},
            )

        order_value = self._notional(context)
        if order_value is None:
            return GuardResult.block(
                "Order notional unavailable; cannot verify position limit",
                self.name,
                {"quantity": context.quantity, "price": context.price},
            )

        if order_value <= 0:
            return GuardResult.block(
                "Order notional must be positive",
                self.name,
                {"notional": order_value},
            )

        # current_position is already a notional (USDT) in every executor.
        current_value = context.current_position

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
    """Enforces minimum time between orders for a symbol.

    Two properties matter for safety:

    1. **Exits are never blocked.** A cooldown that prevents closing a position
       turns a risk control into a risk amplifier -- the position stays open
       exactly when you want it closed. SELL orders bypass the cooldown.
    2. **The cooldown is recorded on fill, not on check.** ``check()`` runs
       before the order is placed, so recording there means a *rejected* order
       still burns the cooldown window (and a rejected BUY blocks the next
       legitimate entry). Callers must call :meth:`record_order` after a
       successful fill.
    """

    def __init__(self, min_seconds: float = 180.0) -> None:
        super().__init__()
        self._min_seconds = min_seconds
        self._last_order_time: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "Cooldown"

    def check(self, context: GuardContext) -> GuardResult:
        import time

        if _is_exit_order(context):
            # Never block an exit.
            return GuardResult.allow("Cooldown skipped for exit", self.name)

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

        # NOTE: the timestamp is deliberately NOT updated here. It is recorded
        # by record_order() once an order actually fills.
        return GuardResult.allow("Cooldown passed", self.name)

    def record_order(self, symbol: str, timestamp: float | None = None) -> None:
        """Record a filled order, starting the cooldown window for ``symbol``."""
        import time

        self._last_order_time[symbol] = time.time() if timestamp is None else timestamp


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
