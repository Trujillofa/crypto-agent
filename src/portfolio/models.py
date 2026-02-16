"""Portfolio models for position tracking and trade history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PositionStatus(Enum):
    """Position status."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass
class Position:
    """A trading position.

    Attributes:
        id: Unique position identifier
        symbol: Trading pair symbol (e.g., BTCUSDT)
        entry_time: When position was opened
        entry_price: Price at entry
        quantity: Amount held (base asset units, always positive for spot)
        status: Current status (open/closed)
        exit_time: When position was closed
        exit_price: Price at exit
        realized_pnl: Realized profit/loss when closed

        # Futures-specific fields (None/0 for spot positions):
        position_side: LONG or SHORT for futures, None for spot
        leverage: Leverage level (1-20), None for spot
        margin_type: Isolated or cross, None for spot
        liquidation_price: Price at which position liquidates, None for spot
        mark_price: Last mark price (for liq monitoring), None for spot
        funding_fees: Accumulated funding fees, 0 for spot
    """

    id: int | None = None
    symbol: str = ""
    entry_time: datetime = field(default_factory=_utc_now)
    entry_price: float = 0.0
    quantity: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    exit_time: datetime | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None

    # Futures-specific fields (nullable for backward compatibility with spot)
    position_side: str | None = None  # "LONG" or "SHORT", None for spot
    leverage: int | None = None  # 1-20x, None for spot
    margin_type: str | None = None  # "isolated", None for spot
    liquidation_price: float | None = None  # Calculated, None for spot
    mark_price: float | None = None  # Last mark price update, None for spot
    funding_fees: float = 0.0  # Accumulated funding, 0 for spot
    fee_rate: float = 0.001

    @property
    def is_open(self) -> bool:
        """Check if position is currently open."""
        return self.status == PositionStatus.OPEN

    @property
    def is_closed(self) -> bool:
        """Check if position is closed."""
        return self.status == PositionStatus.CLOSED

    @property
    def value_at_entry(self) -> float:
        """Calculate position value at entry."""
        return self.entry_price * self.quantity

    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized PnL at current price.

        For spot positions (position_side is None):
            PnL = (current - entry) * quantity

        For futures LONG positions:
            PnL = (current - entry) * quantity

        For futures SHORT positions:
            PnL = (entry - current) * quantity

        Args:
            current_price: Current market price

        Returns:
            Unrealized profit/loss
        """
        if not self.is_open:
            return 0.0

        # Futures SHORT positions have inverse PnL calculation
        if self.position_side == "SHORT":
            raw_pnl = (self.entry_price - current_price) * self.quantity
        else:
            # Spot and futures LONG use same calculation
            raw_pnl = (current_price - self.entry_price) * self.quantity

        fees = (
            self.entry_price * self.quantity + current_price * self.quantity
        ) * self.fee_rate
        return raw_pnl - fees

    def close(self, exit_price: float, exit_time: datetime | None = None) -> float:
        """Close the position and calculate realized PnL.

        For spot and futures LONG:
            PnL = (exit - entry) * quantity

        For futures SHORT:
            PnL = (entry - exit) * quantity

        Args:
            exit_price: Price at which position is closed
            exit_time: Time of closure (defaults to now)

        Returns:
            Realized profit/loss
        """
        self.exit_price = exit_price
        self.exit_time = exit_time or datetime.now(timezone.utc)
        self.status = PositionStatus.CLOSED

        # Futures SHORT positions have inverse PnL calculation
        if self.position_side == "SHORT":
            raw_pnl = (self.entry_price - exit_price) * self.quantity
        else:
            # Spot and futures LONG use same calculation
            raw_pnl = (exit_price - self.entry_price) * self.quantity

        fees = (
            self.entry_price * self.quantity + exit_price * self.quantity
        ) * self.fee_rate
        self.realized_pnl = raw_pnl - fees

        return self.realized_pnl

    def calculate_liquidation_price(
        self, maintenance_margin_rate: float = 0.005
    ) -> float:
        """Calculate the liquidation price for a futures position.

        Formula (isolated margin, one-way mode):
            For LONG: liq = entry * (1 - 1/leverage + maintenance_margin)
            For SHORT: liq = entry * (1 + 1/leverage - maintenance_margin)

        Args:
            maintenance_margin_rate: Maintenance margin requirement (default 0.5%)

        Returns:
            Liquidation price

        Raises:
            ValueError: If position is not a futures position or leverage is invalid
        """
        if self.leverage is None or self.leverage < 1:
            raise ValueError("Leverage must be >= 1 for futures position")

        if self.position_side not in ("LONG", "SHORT"):
            raise ValueError(
                "Liquidation price only applies to futures LONG/SHORT positions"
            )

        leverage_factor = 1 / self.leverage

        if self.position_side == "LONG":
            # For LONG: liq = entry * (1 - 1/leverage + maintenance_margin)
            liq_price = self.entry_price * (
                1 - leverage_factor + maintenance_margin_rate
            )
        else:  # SHORT
            # For SHORT: liq = entry * (1 + 1/leverage - maintenance_margin)
            liq_price = self.entry_price * (
                1 + leverage_factor - maintenance_margin_rate
            )

        self.liquidation_price = liq_price
        return liq_price

    def update_mark_price(self, mark_price: float) -> float:
        """Update the mark price and return unrealized PnL.

        Args:
            mark_price: Current mark price from exchange

        Returns:
            Unrealized PnL at mark price
        """
        self.mark_price = mark_price
        return self.calculate_unrealized_pnl(mark_price)

    def is_near_liquidation(self, buffer_pct: float = 5.0) -> bool:
        """Check if position is within X% of liquidation price.

        Args:
            buffer_pct: Percentage buffer (default 5%)

        Returns:
            True if within buffer of liquidation
        """
        if self.liquidation_price is None or self.mark_price is None:
            return False

        buffer = buffer_pct / 100

        if self.position_side == "LONG":
            # For LONG: mark approaching liq from above
            # Danger zone: mark <= liq * (1 + buffer)
            return self.mark_price <= self.liquidation_price * (1 + buffer)
        else:  # SHORT
            # For SHORT: mark approaching liq from below
            # Danger zone: mark >= liq * (1 - buffer)
            return self.mark_price >= self.liquidation_price * (1 - buffer)


@dataclass
class Trade:
    """A completed trade.

    Attributes:
        id: Unique trade identifier
        time: Trade timestamp
        symbol: Trading pair symbol
        side: BUY or SELL
        quantity: Trade amount
        price: Execution price
        order_id: Binance order ID
        pnl: Realized PnL (for SELL trades that close positions)
        position_id: Reference to associated position
    """

    id: int | None = None
    time: datetime = field(default_factory=_utc_now)
    symbol: str = ""
    side: str = ""  # "BUY" or "SELL"
    quantity: float = 0.0
    price: float = 0.0
    order_id: str | None = None
    pnl: float | None = None
    position_id: int | None = None

    @property
    def value(self) -> float:
        """Calculate trade value in quote asset."""
        return self.price * self.quantity

    @property
    def is_buy(self) -> bool:
        """Check if this is a buy trade."""
        return self.side == "BUY"

    @property
    def is_sell(self) -> bool:
        """Check if this is a sell trade."""
        return self.side == "SELL"


@dataclass
class PortfolioSummary:
    """Summary of portfolio state.

    Attributes:
        total_positions: Total number of positions (open + closed)
        open_positions: Number of open positions
        closed_positions: Number of closed positions
        total_trades: Total number of trades
        total_realized_pnl: Sum of all realized PnL
        total_unrealized_pnl: Sum of unrealized PnL for open positions
        win_count: Number of winning trades
        loss_count: Number of losing trades
    """

    total_positions: int = 0
    open_positions: int = 0
    closed_positions: int = 0
    total_trades: int = 0
    total_realized_pnl: float = 0.0
    total_unrealized_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0

    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.win_count / self.total_trades) * 100

    @property
    def net_pnl(self) -> float:
        """Calculate net PnL (realized + unrealized)."""
        return self.total_realized_pnl + self.total_unrealized_pnl
