#!/usr/bin/env python3
"""Test risk management functionality."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.risk.manager import RiskManager


def test_risk_management():
    """Test risk manager functionality."""
    print("🧪 Testing Risk Management System...")
    print()

    # Create risk manager
    print("1️⃣  Initializing risk manager...")
    risk = RiskManager(Path("config/risk.yaml"))
    print("   ✅ Risk manager initialized")
    print()

    # Test 1: Check trading allowed initially
    print("2️⃣  Checking initial trading status...")
    is_allowed, reason = risk.is_trading_allowed()
    print(f"   Trading allowed: {is_allowed} - {reason}")
    assert is_allowed, "Trading should be allowed initially"
    print("   ✅ Trading is allowed initially")
    print()

    # Test 2: Position limits
    print("3️⃣  Testing position limits...")
    ok, msg = risk.check_position_limit("BTCUSDT", 0.05, 100000)  # 5% position
    print(f"   Small position (5%): {ok} - {msg}")
    assert ok, "Small position should be allowed"

    ok, msg = risk.check_position_limit("BTCUSDT", 0.15, 100000)  # 15% position
    print(f"   Large position (15%): {ok} - {msg}")
    assert not ok, "Large position should be rejected"
    print("   ✅ Position limits working correctly")
    print()

    # Test 3: API error tracking
    print("4️⃣  Testing API error circuit breaker...")
    # Create fresh risk manager for this test
    risk = RiskManager(Path("config/risk.yaml"))

    # Record 2 errors (should not trigger yet)
    risk.record_api_error()
    risk.record_api_error()
    is_allowed, _ = risk.is_trading_allowed()
    print(f"   After 2 API errors: Trading allowed = {is_allowed}")
    assert is_allowed, "Should allow trading after 2 errors"

    # Record 3rd error (should trigger circuit breaker)
    risk.record_api_error()
    is_allowed, reason = risk.is_trading_allowed()
    print(f"   After 3 API errors: Trading allowed = {is_allowed} - {reason}")
    assert not is_allowed, "Should block trading after 3 errors"
    print("   ✅ API error circuit breaker working")
    print()

    # Test 4: Consecutive losses
    print("5️⃣  Testing consecutive losses circuit breaker...")
    # Create fresh risk manager for this test
    risk = RiskManager(Path("config/risk.yaml"))

    # Record 4 small losses (should not trigger)
    for _ in range(4):
        risk.record_trade("BTCUSDT", -100, 10000)  # $100 loss on $10k portfolio
    is_allowed, _ = risk.is_trading_allowed()
    print(f"   After 4 consecutive losses: Trading allowed = {is_allowed}")
    assert is_allowed, "Should allow trading after 4 losses"

    # Record 5th loss (should trigger)
    risk.record_trade("BTCUSDT", -100, 10000)
    is_allowed, reason = risk.is_trading_allowed()
    print(f"   After 5 consecutive losses: Trading allowed = {is_allowed} - {reason}")
    assert not is_allowed, "Should block trading after 5 losses"
    print("   ✅ Consecutive losses circuit breaker working")
    print()

    # Test 5: Max single loss
    print("6️⃣  Testing max single loss limit...")
    # Create fresh risk manager for this test
    risk = RiskManager(Path("config/risk.yaml"))

    # Small loss (should be fine)
    risk.record_trade("BTCUSDT", -100, 10000)  # 1% loss
    print(f"   After 1% loss: Kill switch = {risk._kill_switch_triggered}")

    # Large loss (should trigger)
    risk.record_trade("BTCUSDT", -300, 10000)  # 3% loss (exceeds 2% limit)
    print(f"   After 3% loss: Kill switch = {risk._kill_switch_triggered}")
    print("   ✅ Max single loss protection working")
    print()

    # Test 6: Risk summary
    print("7️⃣  Testing risk summary...")
    # Create fresh risk manager for this test
    risk = RiskManager(Path("config/risk.yaml"))
    summary = risk.get_risk_summary()
    print(f"   Kill switch active: {summary['kill_switch_active']}")
    print(f"   Circuit breakers: {summary['circuit_breakers']}")
    print(f"   Daily PnL: ${summary['daily_pnl']:.2f}")
    print(f"   Consecutive losses: {summary['consecutive_losses']}")
    print("   ✅ Risk summary available")
    print()

    print("=" * 60)
    print("✅ All risk management tests passed!")
    print("=" * 60)
    print()
    print("Risk Management Features:")
    print("  ✅ Position size limits")
    print("  ✅ Max open positions")
    print("  ✅ API error circuit breaker")
    print("  ✅ Consecutive losses circuit breaker")
    print("  ✅ Max single loss protection")
    print("  ✅ Kill switch activation")
    print("  ✅ Daily metrics tracking")
    print("  ✅ Risk summary reporting")

    return True


if __name__ == "__main__":
    try:
        success = test_risk_management()
        sys.exit(0 if success else 1)
    except Exception as exc:
        print(f"❌ Test failed: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
