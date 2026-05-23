#!/usr/bin/env python3
"""Read-only Binance futures account check."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.execution.futures_client import BinanceFuturesClient

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def _format_time(timestamp_ms: int) -> str:
    if timestamp_ms <= 0:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


async def main() -> None:
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_API_SECRET are required")

    async with BinanceFuturesClient(api_key, api_secret, test_mode=False) as client:
        account = await client._request("GET", "/fapi/v2/account", params={}, signed=True)

        print("=" * 50)
        print("BINANCE FUTURES ACCOUNT")
        print("=" * 50)
        print(f"Wallet Balance:  {account.get('totalWalletBalance', '0')} USDT")
        print(f"Unrealized PnL:  {account.get('totalUnrealizedProfit', '0')} USDT")
        print(f"Margin Balance:  {account.get('totalMarginBalance', '0')} USDT")
        print(f"Available:       {account.get('availableBalance', '0')} USDT")

        positions = [
            position
            for position in account.get("positions", [])
            if float(position.get("positionAmt", 0)) != 0
        ]
        print(f"\nOpen positions: {len(positions)}")
        for position in positions:
            print(
                "  "
                f"{position.get('symbol')}: {position.get('positionAmt')} @ "
                f"{position.get('entryPrice')} | UnPnL: "
                f"{position.get('unrealizedProfit')} USDT | leverage: {position.get('leverage')}x"
            )

        print("\n" + "=" * 50)
        print("OPEN ORDERS")
        print("=" * 50)
        for symbol in SYMBOLS:
            orders = await client.get_open_orders(symbol)
            print(f"{symbol}: {len(orders)} regular open orders")
            for order in orders:
                print(
                    "  "
                    f"{order.get('orderId')} {order.get('side')} {order.get('type')} "
                    f"status={order.get('status')} qty={order.get('origQty')} "
                    f"price={order.get('price')} stop={order.get('stopPrice')}"
                )

        algo_orders = await client.get_open_algo_orders()
        print(f"\nOpen algo orders: {len(algo_orders)}")
        for order in algo_orders:
            print(
                "  "
                f"{order.symbol}: {order.algo_id} {order.side} {order.order_type} "
                f"status={order.status} trigger={order.trigger_price}"
            )

        print("\n" + "=" * 50)
        print("RECENT INCOME")
        print("=" * 50)
        for income_type in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE"):
            records = await client.get_income_history(income_type=income_type, limit=20)
            total = sum(record.income for record in records)
            print(f"{income_type}: {total:+.8f} USDT ({len(records)} records)")
            for record in records[-8:]:
                print(
                    "  "
                    f"{record.symbol or '-'} {record.income:+.8f} "
                    f"{record.asset} {_format_time(record.time)} {record.info}"
                )

        print("\n" + "=" * 50)
        print("RECENT USER TRADES")
        print("=" * 50)
        for symbol in SYMBOLS:
            trades = await client.get_user_trades(symbol, limit=10)
            print(f"{symbol}: {len(trades)} recent trades")
            for trade in trades[-5:]:
                print(
                    "  "
                    f"{trade.trade_id} order={trade.order_id} {trade.side} "
                    f"qty={trade.quantity:g} price={trade.price:g} "
                    f"realized={trade.realized_pnl:+.8f} "
                    f"commission={trade.commission:.8f} {trade.commission_asset} "
                    f"{_format_time(trade.time)}"
                )


if __name__ == "__main__":
    asyncio.run(main())
