from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Summary

# Counter for orders placed
orders_placed_total = Counter(
    "execution_orders_placed_total",
    "Total number of orders placed",
    ["symbol", "order_type", "status"],
)

# Counter for order cancellations
orders_cancelled_total = Counter(
    "execution_orders_cancelled_total",
    "Total number of orders cancelled",
    ["symbol", "reason"],
)

# Counter for fills (executed orders)
orders_filled_total = Counter(
    "execution_orders_filled_total", "Total number of orders filled", ["symbol", "side"]
)

# Counter for rejected orders
orders_rejected_total = Counter(
    "execution_orders_rejected_total",
    "Total number of orders rejected",
    ["symbol", "reason"],
)

# Gauge for current open orders count
open_orders_count = Gauge(
    "execution_open_orders_count", "Current number of open orders", ["symbol"]
)

# Histogram for order latency
order_latency_seconds = Histogram(
    "execution_order_latency_seconds",
    "Order placement latency in seconds",
    ["symbol", "order_type"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

# Summary for PnL
realized_pnl_total = Summary(
    "execution_realized_pnl_total", "Realized profit/loss summary", ["symbol"]
)

# Gauge for account balance
account_balance = Gauge(
    "execution_account_balance",
    "Current account balance",
    ["type"],  # total_wallet, available
)

# Gauge for trading status
trading_active = Gauge(
    "execution_trading_active", "Whether trading is active (1=active, 0=inactive)"
)

# Counter for API errors
api_errors_total = Counter(
    "execution_api_errors_total",
    "Total number of API errors",
    ["endpoint", "error_code"],
)

# Counter for risk manager blocks
risk_blocks_total = Counter(
    "execution_risk_blocks_total",
    "Total number of orders blocked by risk manager",
    ["symbol", "reason"],
)

# Counter for strategy signals routed for execution
signals_total = Counter(
    "execution_signals_total",
    "Total number of strategy signals routed for execution",
    ["symbol", "trading_mode", "signal_type"],
)


class ExecutionMetrics:
    """Container for trading execution metrics."""

    def __init__(self) -> None:
        self.orders_placed = orders_placed_total
        self.orders_cancelled = orders_cancelled_total
        self.orders_filled = orders_filled_total
        self.orders_rejected = orders_rejected_total
        self.open_orders = open_orders_count
        self.order_latency = order_latency_seconds
        self.realized_pnl = realized_pnl_total
        self.account_balance = account_balance
        self.trading_active = trading_active
        self.api_errors = api_errors_total
        self.risk_blocks = risk_blocks_total
        self.signals = signals_total

    def start_trading(self) -> None:
        """Mark trading as active."""
        self.trading_active.set(1)

    def stop_trading(self) -> None:
        """Mark trading as inactive."""
        self.trading_active.set(0)

    def record_order_placed(
        self, symbol: str, order_type: str, status: str, latency_seconds: float
    ) -> None:
        """Record an order placement."""
        self.orders_placed.labels(
            symbol=symbol, order_type=order_type, status=status
        ).inc()
        self.order_latency.labels(symbol=symbol, order_type=order_type).observe(
            latency_seconds
        )

    def record_order_cancelled(self, symbol: str, reason: str) -> None:
        """Record an order cancellation."""
        self.orders_cancelled.labels(symbol=symbol, reason=reason).inc()

    def record_order_filled(self, symbol: str, side: str) -> None:
        """Record an order fill."""
        self.orders_filled.labels(symbol=symbol, side=side).inc()

    def record_order_rejected(self, symbol: str, reason: str) -> None:
        """Record an order rejection."""
        self.orders_rejected.labels(symbol=symbol, reason=reason).inc()

    def record_risk_block(self, symbol: str, reason: str) -> None:
        """Record an order blocked by risk manager."""
        self.risk_blocks.labels(symbol=symbol, reason=reason).inc()

    def record_signal(self, symbol: str, trading_mode: str, signal_type: str) -> None:
        """Record a strategy signal routed for execution."""
        self.signals.labels(
            symbol=symbol,
            trading_mode=trading_mode,
            signal_type=signal_type,
        ).inc()

    def record_api_error(self, endpoint: str, error_code: str) -> None:
        """Record an API error."""
        self.api_errors.labels(endpoint=endpoint, error_code=error_code).inc()

    def update_account_balance(
        self,
        total_wallet: float,
        available: float,
    ) -> None:
        """Update account balance metrics."""
        self.account_balance.labels(type="total_wallet").set(total_wallet)
        self.account_balance.labels(type="available").set(available)

    def update_open_orders(
        self, open_orders: list[tuple[str, int]]
    ) -> None:  # (symbol, count)
        """Update open orders metrics."""
        self.open_orders.clear()
        for symbol, count in open_orders:
            self.open_orders.labels(symbol=symbol).set(count)
