"""Composable guard pipeline for pre-execution safety checks.

This module implements a guard pipeline pattern where each guard is an independent
check that can pass, block, or warn. Guards are chainable and configurable.

Guards available:
- PositionSizeGuard: Max position size per asset
- MaxPositionsGuard: Max number of open positions
- CooldownGuard: Minimum time between trades
- LeverageGuard: Max leverage (futures)
- LiquidationBufferGuard: Warn if near liquidation (futures)
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GuardResult(Enum):
    """Guard check result."""

    PASS = "pass"
    BLOCK = "block"
    WARN = "warn"


@dataclass
class GuardResponse:
    """Response from a guard check."""

    result: GuardResult
    guard_name: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        return self.result in (GuardResult.PASS, GuardResult.WARN)


class Guard(ABC):
    """Abstract base class for guards."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Guard name for logging."""
        pass

    @abstractmethod
    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> GuardResponse:
        """Execute guard check.

        Args:
            symbol: Trading pair
            side: BUY or SELL
            quantity: Order quantity
            portfolio_value: Total portfolio value
            context: Additional context (open positions, last trade time, etc.)

        Returns:
            GuardResponse with result and message
        """
        pass

    def reset(self) -> None:
        """Reset guard state (e.g., cooldown timer)."""
        pass


@dataclass
class PositionSizeGuard(Guard):
    """Guard that checks max position size per asset."""

    max_position_pct: float = 0.10
    _name: str = "PositionSizeGuard"

    @property
    def name(self) -> str:
        return self._name

    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> GuardResponse:
        position_value = quantity
        position_pct = position_value / portfolio_value if portfolio_value > 0 else 0

        if position_pct > self.max_position_pct:
            return GuardResponse(
                result=GuardResult.BLOCK,
                guard_name=self.name,
                message=f"Position size {position_pct:.1%} exceeds max {self.max_position_pct:.1%}",
                details={"position_pct": position_pct, "max_pct": self.max_position_pct},
            )

        return GuardResponse(
            result=GuardResult.PASS,
            guard_name=self.name,
            message=f"Position size {position_pct:.1%} within limit",
            details={"position_pct": position_pct, "max_pct": self.max_position_pct},
        )


@dataclass
class MaxPositionsGuard(Guard):
    """Guard that checks max number of open positions."""

    max_open_positions: int = 5
    _name: str = "MaxPositionsGuard"

    @property
    def name(self) -> str:
        return self._name

    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> GuardResponse:
        open_positions = context.get("open_positions", {})
        current_count = len(open_positions)
        has_position = symbol in open_positions

        # SELL doesn't increase position count
        effective_count = current_count if has_position else current_count + 1

        if side == "BUY" and effective_count > self.max_open_positions:
            return GuardResponse(
                result=GuardResult.BLOCK,
                guard_name=self.name,
                message=f"Max positions {self.max_open_positions} reached ({current_count} open)",
                details={"current": current_count, "max": self.max_open_positions},
            )

        return GuardResponse(
            result=GuardResult.PASS,
            guard_name=self.name,
            message=f"Position count {current_count}/{self.max_open_positions}",
            details={"current": current_count, "max": self.max_open_positions},
        )


@dataclass
class CooldownGuard(Guard):
    """Guard that enforces minimum time between trades."""

    cooldown_seconds: float = 60.0
    _last_trade_time: dict[str, float] = field(default_factory=dict)
    _name: str = "CooldownGuard"

    @property
    def name(self) -> str:
        return self._name

    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> GuardResponse:
        last_time = self._last_trade_time.get(symbol, 0.0)
        elapsed = time.monotonic() - last_time

        if elapsed < self.cooldown_seconds:
            remaining = self.cooldown_seconds - elapsed
            return GuardResponse(
                result=GuardResult.BLOCK,
                guard_name=self.name,
                message=f"Cooldown active for {symbol}: {remaining:.1f}s remaining",
                details={"elapsed": elapsed, "cooldown": self.cooldown_seconds},
            )

        return GuardResponse(
            result=GuardResult.PASS,
            guard_name=self.name,
            message=f"Cooldown passed ({elapsed:.1f}s since last trade)",
            details={"elapsed": elapsed, "cooldown": self.cooldown_seconds},
        )

    def record_trade(self, symbol: str) -> None:
        self._last_trade_time[symbol] = time.monotonic()

    def reset(self) -> None:
        self._last_trade_time.clear()


@dataclass
class LeverageGuard(Guard):
    """Guard that checks max leverage (futures)."""

    max_leverage: int = 10
    _name: str = "LeverageGuard"

    @property
    def name(self) -> str:
        return self._name

    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> GuardResponse:
        leverage = context.get("leverage", 1)

        if leverage > self.max_leverage:
            return GuardResponse(
                result=GuardResult.BLOCK,
                guard_name=self.name,
                message=f"Leverage {leverage}x exceeds max {self.max_leverage}x",
                details={"leverage": leverage, "max": self.max_leverage},
            )

        return GuardResponse(
            result=GuardResult.PASS,
            guard_name=self.name,
            message=f"Leverage {leverage}x within limit",
            details={"leverage": leverage, "max": self.max_leverage},
        )


@dataclass
class LiquidationBufferGuard(Guard):
    """Guard that warns if position is too close to liquidation (futures)."""

    liquidation_buffer_pct: float = 5.0
    _name: str = "LiquidationBufferGuard"

    @property
    def name(self) -> str:
        return self._name

    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> GuardResponse:
        liquidation_distance_pct = context.get("liquidation_distance_pct", 100.0)

        if 0 < liquidation_distance_pct < self.liquidation_buffer_pct:
            return GuardResponse(
                result=GuardResult.WARN,
                guard_name=self.name,
                message=f"Position {liquidation_distance_pct:.1f}% from liquidation (buffer: {self.liquidation_buffer_pct}%)",
                details={
                    "distance_pct": liquidation_distance_pct,
                    "buffer_pct": self.liquidation_buffer_pct,
                },
            )

        return GuardResponse(
            result=GuardResult.PASS,
            guard_name=self.name,
            message=f"Liquidation buffer OK ({liquidation_distance_pct:.1f}%)",
            details={"distance_pct": liquidation_distance_pct},
        )


class GuardPipeline:
    """Chain of guards that validate orders before execution.

    Each guard is checked in order. BLOCK stops immediately, WARN continues.
    """

    def __init__(self, guards: list[Guard] | None = None) -> None:
        self._guards = guards or []

    def add_guard(self, guard: Guard) -> None:
        self._guards.append(guard)

    def check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        portfolio_value: float,
        context: dict[str, Any],
    ) -> tuple[bool, list[GuardResponse]]:
        """Run all guards in order.

        Returns:
            (is_allowed, responses) - is_allowed False if any guard blocks
        """
        responses: list[GuardResponse] = []
        is_allowed = True

        for guard in self._guards:
            response = guard.check(symbol, side, quantity, portfolio_value, context)
            responses.append(response)

            if response.result == GuardResult.BLOCK:
                is_allowed = False
                break

        return is_allowed, responses

    def record_trade(self, symbol: str) -> None:
        """Record trade for guards that track state (e.g., cooldown)."""
        for guard in self._guards:
            if hasattr(guard, "record_trade"):
                guard.record_trade(symbol)

    def reset(self) -> None:
        """Reset all guards."""
        for guard in self._guards:
            guard.reset()

    @property
    def guards(self) -> list[Guard]:
        return list(self._guards)


def create_default_pipeline(
    max_position_pct: float = 0.10,
    max_open_positions: int = 5,
    cooldown_seconds: float = 60.0,
    max_leverage: int = 10,
    liquidation_buffer_pct: float = 5.0,
) -> GuardPipeline:
    """Create a guard pipeline with default guards.

    Args:
        max_position_pct: Max position size as % of portfolio
        max_open_positions: Max number of open positions
        cooldown_seconds: Seconds between trades per symbol
        max_leverage: Max leverage for futures
        liquidation_buffer_pct: Warn if within this % of liquidation

    Returns:
        Configured GuardPipeline
    """
    return GuardPipeline(
        guards=[
            PositionSizeGuard(max_position_pct=max_position_pct),
            MaxPositionsGuard(max_open_positions=max_open_positions),
            CooldownGuard(cooldown_seconds=cooldown_seconds),
            LeverageGuard(max_leverage=max_leverage),
            LiquidationBufferGuard(liquidation_buffer_pct=liquidation_buffer_pct),
        ]
    )
