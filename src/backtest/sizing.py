"""Pure exchange-constraint sizing shared by live execution and replay."""

from __future__ import annotations

import math


def calculate_futures_order_quantity(
    *,
    order_size_usdt: float,
    price: float,
    quantity_step_size: float = 0.0,
    min_notional_usdt: float = 0.0,
) -> float:
    """Return a Binance-compatible quantity for a fixed-notional futures order.

    Binance checks the minimum notional after LOT_SIZE truncation.  The live
    executor therefore adds one quantity step when truncation would otherwise
    fall below the configured minimum.  Keeping that decision here prevents a
    replay from silently using different exposure than the order path.
    """
    if price <= 0 or order_size_usdt <= 0:
        return 0.0

    raw_quantity = order_size_usdt / price
    if quantity_step_size <= 0:
        return raw_quantity

    truncated = math.floor(raw_quantity / quantity_step_size) * quantity_step_size
    if min_notional_usdt > 0 and truncated * price < min_notional_usdt:
        return truncated + quantity_step_size
    return truncated
