"""Portfolio models for position tracking and trade history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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
        quantity: Amount held (base asset units)
        status: Current status (open/closed)
        exit_time: When position was closed
        exit_price: Price at exit
        realized_pnl: Realized profit/loss when closed
    """

    id: int | None = None
    symbol: str = ""
    entry_time: datetime = field(default_factory=datetime.utcnow)
    entry_price: float = 0.0
    quantity: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    exit_time: datetime | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None

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

        Args:
            current_price: Current market price

        Returns:
            Unrealized profit/loss
        """
        if not self.is_open:
            return 0.0
        return (current_price - self.entry_price) * self.quantity

    def close(self, exit_price: float, exit_time: datetime | None = None) -> float:
        """Close the position and calculate realized PnL.

        Args:
            exit_price: Price at which position is closed
            exit_time: Time of closure (defaults to now)

        Returns:
            Realized profit/loss
        """
        self.exit_price = exit_price
        self.exit_time = exit_time or datetime.utcnow()
        self.status = PositionStatus.CLOSED
        self.realized_pnl = (exit_price - self.entry_price) * self.quantity
        return self.realized_pnl


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
    time: datetime = field(default_factory=datetime.utcnow)
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
