"""Staged orders implementation - OpenAlice pattern.

Provides a 3-stage order lifecycle:
1. stage() - Queue order for review (creates order draft)
2. commit() - Approve order for execution
3. execute() - Send to exchange

In paper/test mode: auto-commits
In live mode: requires explicit commit
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from src.execution.binance_client import OrderInfo
from src.utils.logger import get_logger


class OrderStage(Enum):
    """Order lifecycle stages."""

    STAGED = auto()  # Draft created, not yet committed
    COMMITTED = auto()  # Approved for execution
    EXECUTING = auto()  # Sent to exchange
    FILLED = auto()  # Successfully filled
    CANCELED = auto()  # Canceled before execution
    REJECTED = auto()  # Rejected by risk or exchange


@dataclass
class StagedOrder:
    """A staged order before execution."""

    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    order_type: str = "MARKET"
    stage: OrderStage = OrderStage.STAGED
    created_at: float = field(default_factory=time.time)
    committed_at: float | None = None
    executed_at: float | None = None
    result: OrderInfo | None = None
    reject_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class StagedOrderManager:
    """Manages staged orders with 3-stage lifecycle.

    Pattern adapted from OpenAlice's Wallet.add/commit/push flow.
    """

    def __init__(self, auto_commit: bool = False) -> None:
        """Initialize staged order manager.

        Args:
            auto_commit: If True, automatically commit staged orders.
                         Set to True for paper/test mode.
        """
        self._auto_commit = auto_commit
        self._staged: dict[str, StagedOrder] = {}
        self._lock = asyncio.Lock()
        self._logger = get_logger(self.__class__.__name__)

    async def stage(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        metadata: dict[str, Any] | None = None,
    ) -> StagedOrder:
        """Stage an order for potential execution.

        Creates a draft order that can be reviewed before commitment.

        Args:
            symbol: Trading pair symbol
            side: "BUY" or "SELL"
            quantity: Order quantity
            order_type: "MARKET" or "LIMIT"
            metadata: Additional context (signal info, strategy name, etc.)

        Returns:
            StagedOrder with STAGED status
        """
        order_id = str(uuid.uuid4())
        order = StagedOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            stage=OrderStage.STAGED,
            metadata=metadata or {},
        )

        async with self._lock:
            self._staged[order_id] = order

        self._logger.info(
            "Order staged: %s %s %s %s (id=%s)", side, quantity, symbol, order_type, order_id[:8]
        )

        if self._auto_commit:
            await self.commit(order_id)

        return order

    async def commit(self, order_id: str) -> StagedOrder:
        """Commit a staged order for execution.

        In live mode, this is the explicit approval step before
        sending to the exchange.

        Args:
            order_id: ID of staged order to commit

        Returns:
            Updated StagedOrder with COMMITTED status

        Raises:
            ValueError: If order not found or not in STAGED state
        """
        async with self._lock:
            if order_id not in self._staged:
                raise ValueError(f"Order {order_id} not found")

            order = self._staged[order_id]
            # Idempotent: if already COMMITTED (e.g. via auto_commit=True),
            # return without error. Required because the live executor calls
            # commit() explicitly even when the manager auto-committed at stage().
            if order.stage == OrderStage.COMMITTED:
                return order
            if order.stage != OrderStage.STAGED:
                raise ValueError(
                    f"Order {order_id} is not in STAGED state (current: {order.stage.name})"
                )

            order.stage = OrderStage.COMMITTED
            order.committed_at = time.time()

        self._logger.info(
            "Order committed: %s %s %s (id=%s)",
            order.side,
            order.quantity,
            order.symbol,
            order_id[:8],
        )

        return order

    async def mark_executing(self, order_id: str) -> StagedOrder:
        """Mark order as executing (sent to exchange).

        Args:
            order_id: ID of committed order

        Returns:
            Updated StagedOrder with EXECUTING status
        """
        async with self._lock:
            order = self._staged[order_id]
            order.stage = OrderStage.EXECUTING
            order.executed_at = time.time()

        self._logger.info(
            "Order executing: %s %s %s (id=%s)",
            order.side,
            order.quantity,
            order.symbol,
            order_id[:8],
        )

        return order

    async def mark_completed(
        self,
        order_id: str,
        result: OrderInfo,
    ) -> StagedOrder:
        """Mark order as completed with result.

        Args:
            order_id: ID of executing order
            result: Order result from exchange

        Returns:
            Updated StagedOrder with FILLED status
        """
        async with self._lock:
            order = self._staged[order_id]
            order.stage = OrderStage.FILLED
            order.result = result

        self._logger.info(
            "Order filled: %s %s %s filled_qty=%s (id=%s)",
            order.side,
            order.quantity,
            order.symbol,
            result.executed_quantity,
            order_id[:8],
        )

        return order

    async def mark_rejected(
        self,
        order_id: str,
        reason: str,
    ) -> StagedOrder:
        """Mark order as rejected.

        Args:
            order_id: ID of order to reject
            reason: Rejection reason

        Returns:
            Updated StagedOrder with REJECTED status
        """
        async with self._lock:
            order = self._staged[order_id]
            order.stage = OrderStage.REJECTED
            order.reject_reason = reason

        self._logger.warning(
            "Order rejected: %s %s %s - %s (id=%s)",
            order.side,
            order.quantity,
            order.symbol,
            reason,
            order_id[:8],
        )

        return order

    async def cancel(self, order_id: str) -> StagedOrder:
        """Cancel a staged order before execution.

        Args:
            order_id: ID of order to cancel

        Returns:
            Updated StagedOrder with CANCELED status
        """
        async with self._lock:
            order = self._staged[order_id]
            if order.stage in (OrderStage.EXECUTING, OrderStage.FILLED):
                raise ValueError(f"Cannot cancel order in {order.stage.name} state")

            order.stage = OrderStage.CANCELED

        self._logger.info(
            "Order canceled: %s %s %s (id=%s)",
            order.side,
            order.quantity,
            order.symbol,
            order_id[:8],
        )

        return order

    def get_staged(self, order_id: str) -> StagedOrder | None:
        """Get a staged order by ID."""
        return self._staged.get(order_id)

    def get_all_staged(self) -> list[StagedOrder]:
        """Get all orders in STAGED state."""
        return [o for o in self._staged.values() if o.stage == OrderStage.STAGED]

    def get_all_committed(self) -> list[StagedOrder]:
        """Get all orders in COMMITTED state (ready to execute)."""
        return [o for o in self._staged.values() if o.stage == OrderStage.COMMITTED]

    def get_stats(self) -> dict[str, int]:
        """Get order statistics by stage."""
        stats = {stage.name: 0 for stage in OrderStage}
        for order in self._staged.values():
            stats[order.stage.name] += 1
        return stats
