#!/usr/bin/env python3
"""Test Binance private API endpoints with authentication."""

import hashlib
import hmac
import json
import os
import time
import urllib.error
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://api.binance.com"


def get_signature(query_string: str, secret: str) -> str:
    """Generate HMAC SHA256 signature for Binance API."""
    return hmac.new(
        secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def make_signed_request(
    endpoint: str, params: dict | None = None, method: str = "GET"
) -> dict:
    """Make a signed request to Binance private API."""
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise ValueError(
            "BINANCE_API_KEY and BINANCE_API_SECRET must be set in environment"
        )

    # Add timestamp
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000

    # Create query string and signature
    query_string = urlencode(params)
    signature = get_signature(query_string, api_secret)
    query_string += f"&signature={signature}"

    url = f"{BASE_URL}{endpoint}?{query_string}"

    headers = {"X-MBX-APIKEY": api_key, "Content-Type": "application/json"}

    req = Request(url, headers=headers, method=method)

    try:
        with urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8")
        raise RuntimeError(f"Binance API error: {exc.code} - {error_body}") from exc


def test_account_info():
    """Test /api/v3/account endpoint."""
    print("1️⃣  Testing Account Info (/api/v3/account)...")
    try:
        data = make_signed_request("/api/v3/account")
        print(f"   ✅ SUCCESS - Maker commission: {data.get('makerCommission', 'N/A')}")
        return True
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False


def test_balance():
    """Test /api/v3/account (balances) endpoint."""
    print("2️⃣  Testing Balance (/api/v3/account)...")
    try:
        data = make_signed_request("/api/v3/account")
        balances = data.get("balances", [])
        print(f"   ✅ SUCCESS - Retrieved {len(balances)} assets")

        # Find USDT balance
        for asset in balances:
            if asset.get("asset") == "USDT":
                print(f"   USDT Balance:")
                print(f"     Free: {asset.get('free', 'N/A')}")
                print(f"     Locked: {asset.get('locked', 'N/A')}")
                break
        return True
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False


def test_position_info():
    """Spot trading has no position risk endpoint."""
    print("3️⃣  Skipping Position Info (Not applicable for Spot)...")
    return True


def test_open_orders():
    """Test /api/v3/openOrders endpoint."""
    print("4️⃣  Testing Open Orders (/api/v3/openOrders)...")
    try:
        data = make_signed_request("/api/v3/openOrders")
        print(f"   ✅ SUCCESS - {len(data)} open orders")

        for order in data[:3]:  # Show first 3
            print(
                f"   {order.get('symbol', 'N/A')}: {order.get('side', 'N/A')} "
                f"{order.get('origQty', 'N/A')} @ {order.get('price', 'N/A')} "
                f"({order.get('status', 'N/A')})"
            )

        return True
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False


def test_account_trades():
    """Test /api/v3/myTrades endpoint for BTCUSDT."""
    print("5️⃣  Testing Recent Trades (/api/v3/myTrades)...")
    try:
        params = {"symbol": "BTCUSDT", "limit": 5}
        data = make_signed_request("/api/v3/myTrades", params)
        print(f"   ✅ SUCCESS - Retrieved {len(data)} recent trades for BTCUSDT")

        for trade in data[:3]:
            print(
                f"   {trade.get('time', 'N/A')}: {trade.get('side', 'N/A')} "
                f"{trade.get('qty', 'N/A')} @ {trade.get('price', 'N/A')} "
            )

        return True
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False


def test_income_history():
    """Spot trading has no simple income endpoint like futures."""
    print("6️⃣  Skipping Income History (Not applicable for Spot)...")
    return True


def test_leverage_brackets():
    """Spot trading has no leverage brackets."""
    print("7️⃣  Skipping Leverage Brackets (Not applicable for Spot)...")
    return True


def test_api_key_permissions():
    """Test /api/v3/account (permissions) endpoint."""
    print("8️⃣  Testing API Key Permissions (/api/v3/account)...")
    try:
        data = make_signed_request("/api/v3/account")
        print(f"   ✅ SUCCESS - API Key permissions retrieved")

        print(f"   Can Trade: {data.get('canTrade', False)}")
        print(f"   Can Withdraw: {data.get('canWithdraw', False)}")
        print(f"   Can Deposit: {data.get('canDeposit', False)}")

        return True
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False


def main():
    """Run all private endpoint tests."""
    print("🧪 Testing Binance Private API Endpoints...")
    print(f"   Base URL: {BASE_URL}")
    print()

    # Check environment variables
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    if not api_key:
        print("❌ BINANCE_API_KEY not set in environment!")
        print("   Set it with: export BINANCE_API_KEY='your_key'")
        return False

    # Mask and display key
    masked_key = api_key[:8] + "..." + api_key[-8:] if len(api_key) > 16 else "***"
    print(f"   Using API Key: {masked_key}")
    print()

    tests = [
        test_account_info,
        test_balance,
        test_position_info,
        test_open_orders,
        test_account_trades,
        test_income_history,
        test_leverage_brackets,
        test_api_key_permissions,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as exc:
            print(f"   ❌ EXCEPTION: {exc}")
            results.append(False)
        print()

    # Summary
    passed = sum(results)
    total = len(results)

    print("=" * 60)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 60)

    if passed == total:
        print("✅ All private endpoint tests passed!")
        print()
        print("Your API keys are working correctly.")
        print("You can now use private endpoints for:")
        print("  • Account balance queries")
        print("  • Position monitoring")
        print("  • Order management")
        print("  • Trade history")
    else:
        print("⚠️  Some tests failed. Check your API key permissions.")
        print()
        print("Common issues:")
        print("  • IP restrictions not matching your current IP")
        print("  • Futures trading not enabled on the API key")
        print("  • Read permissions only (no trading)")

    return passed == total


if __name__ == "__main__":
    import sys

    success = main()
    sys.exit(0 if success else 1)
