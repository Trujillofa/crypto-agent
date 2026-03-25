#!/usr/bin/env python3
"""Test Binance API connection (Spot)."""

import json
import urllib.error
from urllib.parse import urlencode
from urllib.request import urlopen

BASE_URL = "https://api.binance.com"


def test_binance_connection():
    """Test basic connectivity to Binance Spot API."""
    print("🧪 Testing Binance Spot API Connection...")
    print(f"   Base URL: {BASE_URL}")
    print()

    # Test 1: Server time endpoint
    print("1️⃣  Testing /api/v3/time (Server Time)...")
    try:
        url = f"{BASE_URL}/api/v3/time"
        with urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        print(f"   ✅ SUCCESS - Server time: {data.get('serverTime')}")
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False

    print()

    # Test 2: Exchange info
    print("2️⃣  Testing /api/v3/exchangeInfo...")
    try:
        url = f"{BASE_URL}/api/v3/exchangeInfo"
        with urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        symbols = [s["symbol"] for s in data.get("symbols", [])[:5]]
        print(f"   ✅ SUCCESS - Total symbols: {len(data.get('symbols', []))}")
        print(f"   Sample symbols: {', '.join(symbols)}...")
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False

    print()

    # Test 3: Klines (OHLCV data) - Main endpoint used by the agent
    print("3️⃣  Testing /api/v3/klines (OHLCV data)...")
    try:
        query = urlencode({"symbol": "BTCUSDT", "interval": "1m", "limit": 2})
        url = f"{BASE_URL}/api/v3/klines?{query}"
        with urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        if isinstance(data, list) and len(data) > 0:
            candle = data[-1]
            print(f"   ✅ SUCCESS - Fetched {len(data)} candles for BTCUSDT")
            print(
                f"   Latest candle - Open: {candle[1]}, High: {candle[2]}, Low: {candle[3]}, Close: {candle[4]}, Volume: {candle[5]}"
            )
        else:
            print("   ⚠️  Unexpected response format")
            return False
    except urllib.error.HTTPError as exc:
        print(f"   ❌ FAILED - HTTP Error {exc.code}: {exc.reason}")
        return False
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False

    print()

    # Test 4: Ticker price (Spot equivalent for mark price check)
    print("4️⃣  Testing /api/v3/ticker/price...")
    try:
        query = urlencode({"symbol": "BTCUSDT"})
        url = f"{BASE_URL}/api/v3/ticker/price?{query}"
        with urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        print(f"   ✅ SUCCESS - BTCUSDT Price: {data.get('price', 'N/A')}")
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False

    print()
    print("=" * 50)
    print("✅ All Binance API tests passed!")
    print("=" * 50)
    return True


if __name__ == "__main__":
    success = test_binance_connection()
    exit(0 if success else 1)
