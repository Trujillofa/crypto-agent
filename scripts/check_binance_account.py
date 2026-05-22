#!/usr/bin/env python3
"""Quick Binance account check."""

import os
import sys

sys.path.insert(0, "/app")

from src.execution.binance_client import BinancePrivateClient


def main():
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    client = BinancePrivateClient(api_key, api_secret)

    account = client.futures_account()
    wallet = account["totalWalletBalance"]
    unrealized = account["totalUnrealizedProfit"]
    margin = account["totalMarginBalance"]
    available = account["availableBalance"]

    print("=" * 50)
    print("BINANCE FUTURES ACCOUNT")
    print("=" * 50)
    print(f"Wallet Balance:  {wallet} USDT")
    print(f"Unrealized PnL:  {unrealized} USDT")
    print(f"Margin Balance:  {margin} USDT")
    print(f"Available:       {available} USDT")

    positions = [p for p in account["positions"] if float(p["positionAmt"]) != 0]
    print(f"\nOpen positions: {len(positions)}")
    for p in positions:
        print(
            f"  {p['symbol']}: {p['positionAmt']} @ {p['entryPrice']} "
            f"| UnPnL: {p['unrealizedProfit']} USDT"
        )

    # Income history
    income = client.futures_income_history(incomeType="REALIZED_PNL", limit=500)
    gross = sum(float(i["income"]) for i in income)

    commissions = client.futures_income_history(incomeType="COMMISSION", limit=500)
    fees = sum(float(c["income"]) for c in commissions)

    print("\n" + "=" * 50)
    print("REALIZED PNL HISTORY")
    print("=" * 50)
    print(f"Gross PnL: {gross:+.2f} USDT ({len(income)} records)")
    print(f"Fees paid: {fees:+.2f} USDT ({len(commissions)} records)")
    print(f"Net PnL:   {gross + fees:+.2f} USDT")


if __name__ == "__main__":
    main()
