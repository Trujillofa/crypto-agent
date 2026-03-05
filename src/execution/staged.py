"""Staged order queue with git-like workflow.

This module implements a staged -> committed -> pushed workflow for orders,
providing an additional safety layer before any exchange interaction.

Workflow:
1. stage(order)   - Order created and queued for review
2. commit(order_id) -> hash  - Order explicitly approved, ready for execution
3. push(order)    - Order sent to exchange

Each commit generates an 8-character hash for traceability.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.strategy.signals import SignalType
from src.utils.logger import get_logger


class OrderStage(Enum):
    """Order lifecycle stages."""

    STAGED = "staged"  # Created, waiting for commit
    COMMITTED = "committed"  # Approved, ready for push
    PUSHED = "pushed"  # Sent to exchange
    FILLED = "filled"  # Order filled
    CANCELLED = "cancelled"  # Order cancelled
    FAILED = "failed"  # Order failed


@dataclass
class StagedOrder:
    """Represents an order in the staged workflow.

    Attributes:
        id: Unique identifier for this staged order
        hash: 8-char commit hash (generated on commit)
        symbol: Trading pair (e.g., BTCUSDT)
        side: BUY or SELL
        quantity: Order quantity
        price: Limit price (None for market orders)
        order_type: MARKET or LIMIT
        stage: Current workflow stage
        signal_confidence: Confidence from strategy signal
        signal_reason: Reason from strategy signal
        created_at: Timestamp when staged
        committed_at: Timestamp when committed
        pushed_at: Timestamp when pushed to exchange
        filled_at: Timestamp when order was filled
        exchange_order_id: Exchange order ID (after push)
        exchange_order_id: Exchange order ID (after push)
        error: Error message if failed
        metadata: Additional context
    """

    id: str
    symbol: str
    side: SignalType
    quantity: float
    price: float | None = None
    order_type: str = "MARKET"
    stage: OrderStage = OrderStage.STAGED
    hash: str = ""
    signal_confidence: float = 0.0
    signal_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    committed_at: datetime | None = None
    pushed_at: datetime | None = None
    filled_at: datetime | None = None
    exchange_order_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for logging/audit."""
        return {
            "id": self.id,
            "hash": self.hash,
            "symbol": self.symbol,
            "side": self.side.value if isinstance(self.side, SignalType) else self.side,
            "quantity": self.quantity,
            "price": self.price,
            "order_type": self.order_type,
            "stage": self.stage.value,
            "signal_confidence": self.signal_confidence,
            "signal_reason": self.signal_reason,
            "created_at": self.created_at.isoformat(),
            "committed_at": self.committed_at.isoformat() if self.committed_at else None,
            "pushed_at": self.pushed_at.isoformat() if self.pushed_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "exchange_order_id": self.exchange_order_id,
            "error": self.error,
            "metadata": self.metadata,
        }


class StagedOrderQueue:
    """Manages the staged order workflow.

    Provides:
    - stage(): Add new order to queue
    - commit(): Approve order with hash
    - get_pending(): Get orders waiting for commit
    - get_ready(): Get committed orders ready for push
    - update_stage(): Move order to next stage
    - get_history(): Get audit trail
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._orders: dict[str, StagedOrder] = {}
        self._max_history = max_history
        self._order_counter = 0

    def stage(
        self,
        symbol: str,
        side: SignalType,
        quantity: float,
        price: float | None = None,
        order_type: str = "MARKET",
        signal_confidence: float = 0.0,
        signal_reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> StagedOrder:
        """Stage a new order for review.

        Args:
            symbol: Trading pair
            side: BUY or SELL
            quantity: Order quantity
            price: Limit price (None for market)
            order_type: MARKET or LIMIT
            signal_confidence: Strategy signal confidence
            signal_reason: Strategy signal reason
            metadata: Additional context

        Returns:
            StagedOrder in STAGED stage
        """
        self._order_counter += 1
        order_id = f"staged_{int(time.time() * 1000)}_{self._order_counter}"

        order = StagedOrder(
            id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            stage=OrderStage.STAGED,
            signal_confidence=signal_confidence,
            signal_reason=signal_reason,
            metadata=metadata or {},
        )

        self._orders[order_id] = order
        # Trim history to max_history
        if len(self._orders) > self._max_history:
            oldest_id = next(iter(self._orders))
            del self._orders[oldest_id]
        self._logger.info(
            "Order staged: %s %s %.4f %s (id=%s, confidence=%.2f)",
            side.value if isinstance(side, SignalType) else side,
            symbol,
            quantity,
            order_type,
            order_id,
            signal_confidence,
        )

        return order

    def commit(self, order_id: str) -> str | None:
        """Commit a staged order, generating its hash.

        Args:
            order_id: ID of order to commit

        Returns:
            8-char hash if successful, None if order not found or not staged
        """
        order = self._orders.get(order_id)
        if order is None:
            self._logger.warning("Commit failed: order %s not found", order_id)
            return None

        if order.stage != OrderStage.STAGED:
            self._logger.warning(
                "Commit failed: order %s in stage %s (expected STAGED)",
                order_id,
                order.stage.value,
            )
            return None

        # Generate deterministic hash from order details
        hash_input = f"{order.symbol}:{order.side}:{order.quantity}:{order.created_at.isoformat()}"
        hash_bytes = hashlib.sha256(hash_input.encode()).digest()
        commit_hash = hash_bytes[:4].hex()[:8]

        order.hash = commit_hash
        order.stage = OrderStage.COMMITTED
        order.committed_at = datetime.now(timezone.utc)

        self._logger.info(
            "Order committed: %s -> hash=%s",
            order_id,
            commit_hash,
        )

        return commit_hash

    def commit_all_pending(self) -> list[str]:
        """Commit all pending staged orders.

        Returns:
            List of commit hashes
        """
        hashes = []
        for order_id in list(self._orders.keys()):
            if self._orders[order_id].stage == OrderStage.STAGED:
                hash_val = self.commit(order_id)
                if hash_val:
                    hashes.append(hash_val)
        return hashes

    def get_pending(self) -> list[StagedOrder]:
        """Get all orders waiting to be committed."""
        return [o for o in self._orders.values() if o.stage == OrderStage.STAGED]

    def get_ready(self) -> list[StagedOrder]:
        """Get all committed orders ready for push."""
        return [o for o in self._orders.values() if o.stage == OrderStage.COMMITTED]

    def get_by_hash(self, commit_hash: str) -> StagedOrder | None:
        """Get order by commit hash."""
        for order in self._orders.values():
            if order.hash == commit_hash:
                return order
        return None

    def update_stage(
        self,
        order_id: str,
        new_stage: OrderStage,
        exchange_order_id: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Update order stage after push/fill/cancel.

        Args:
            order_id: Order to update
            new_stage: Target stage
            exchange_order_id: Exchange order ID (for PUSHED)
            error: Error message (for FAILED)

        Returns:
            True if updated, False if order not found
        """
        order = self._orders.get(order_id)
        if order is None:
            return False

        old_stage = order.stage.value
        order.stage = new_stage
        order.exchange_order_id = exchange_order_id
        order.error = error

        if new_stage == OrderStage.PUSHED:
            order.pushed_at = datetime.now(timezone.utc)
        if new_stage == OrderStage.FILLED:
            if order.filled_at is None:
                order.filled_at = datetime.now(timezone.utc)
            if order.pushed_at is None:
                self._logger.warning(
                    "Order %s filled without pushed_at set; missing PUSHED transition.",
                    order_id,
                )

        self._logger.info(
            "Order stage updated: %s %s -> %s",
            order_id,
            old_stage,
            new_stage.value,
        )

        return True

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get audit trail of all orders.

        Args:
            limit: Max number of orders to return

        Returns:
            List of order dictionaries
        """
        orders = sorted(
            self._orders.values(),
            key=lambda o: o.created_at,
            reverse=True,
        )
        return [o.to_dict() for o in orders[:limit]]

    def get_stats(self) -> dict[str, int]:
        """Get queue statistics."""
        stats = {stage.value: 0 for stage in OrderStage}
        for order in self._orders.values():
            stats[order.stage.value] += 1
        return stats


# Global queue instance for the executor
_queue: StagedOrderQueue | None = None


def get_staged_order_queue() -> StagedOrderQueue:
    """Get or create the global staged order queue."""
    global _queue
    if _queue is None:
        _queue = StagedOrderQueue()
    return _queue
