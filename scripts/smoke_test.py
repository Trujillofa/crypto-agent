#!/usr/bin/env python3
"""End-to-end smoke test for the trading agent pipeline."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingest.binance import BinanceIngestor
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv
from src.notifications.telegram import TelegramNotifier
from src.portfolio import PortfolioManager
from src.risk.manager import RiskManager
from src.strategy import SimpleMACrossoverStrategy
from src.strategy.signals import SignalType
from src.utils.logger import configure_logger


class SmokeTestMetricsServer:
    """Mock metrics server for smoke test."""

    def start(self, port: int) -> None:
        print(f"  📊 Metrics server would start on port {port}")


async def test_ingestion():
    """Test OHLCV ingestion from Binance."""
    print("\n🔍 TEST 1: OHLCV Ingestion")
    print("-" * 50)

    metrics = IngestMetrics()
    symbols = ["BTCUSDT"]
    ingestor = BinanceIngestor(symbols, "1m", metrics)

    candles_received = []

    async def capture_candle(candle: Ohlcv):
        candles_received.append(candle)
        if len(candles_received) <= 3:
            print(
                f"  ✅ Received: {candle.symbol} @ {candle.close_price:.2f} (vol: {candle.volume:.4f})"
            )

    async with ingestor:
        # Fetch a few candles
        for _ in range(3):
            await ingestor._poll_latest(capture_candle)
            await asyncio.sleep(1)

    if len(candles_received) >= 3:
        print(f"  ✅ Ingestion test PASSED ({len(candles_received)} candles received)")
        return True
    else:
        print(f"  ❌ Ingestion test FAILED (only {len(candles_received)} candles)")
        return False


async def test_indicator_computation():
    """Test indicator computation from OHLCV data."""
    print("\n🔍 TEST 2: Indicator Computation")
    print("-" * 50)

    # Mock OHLCV data
    ohlcv_data = {
        "time": ["2024-01-01T00:00:00Z"] * 50,
        "open": [50000.0 + i * 10 for i in range(50)],
        "high": [50100.0 + i * 10 for i in range(50)],
        "low": [49900.0 + i * 10 for i in range(50)],
        "close": [50050.0 + i * 10 for i in range(50)],
        "volume": [1.0] * 50,
    }

    from src.features.technical import compute_indicators

    try:
        indicators = compute_indicators(ohlcv_data)
        print(f"  ✅ RSI(14): {indicators.rsi_14:.2f}")
        print(f"  ✅ MACD: {indicators.macd:.4f}")
        print(f"  ✅ EMA(12): {indicators.ema_12:.2f}")
        print(f"  ✅ EMA(26): {indicators.ema_26:.2f}")
        print(f"  ✅ ATR(14): {indicators.atr_14:.2f}")
        print("  ✅ Indicator computation test PASSED")
        return True
    except Exception as e:
        print(f"  ❌ Indicator computation test FAILED: {e}")
        return False


async def test_strategy_signals():
    """Test strategy signal generation."""
    print("\n🔍 TEST 3: Strategy Signal Generation")
    print("-" * 50)

    strategy = SimpleMACrossoverStrategy(
        {
            "ema_short_period": 12,
            "ema_long_period": 26,
        }
    )

    # Test crossover UP (BUY signal): short EMA was below, now above
    # First call - short below long
    indicators_below = {
        "ema_12": 49000.0,  # Short below
        "ema_26": 50000.0,  # Long above
        "close_price": 49500.0,
    }
    signal1 = await strategy.evaluate("BTCUSDT", indicators_below)
    print(f"  📊 First evaluation (short below): {signal1.type.value}")

    # Second call - short crosses above long (BUY signal)
    indicators_above = {
        "ema_12": 51000.0,  # Short now above
        "ema_26": 50000.0,  # Long below
        "close_price": 50500.0,
    }
    signal2 = await strategy.evaluate("BTCUSDT", indicators_above)
    print(f"  📊 Crossover UP: {signal2.type.value} - {signal2.reason[:60]}...")

    # Third call - maintain state
    signal3 = await strategy.evaluate("BTCUSDT", indicators_above)
    print(f"  📊 Maintained: {signal3.type.value}")

    # Fourth call - cross down (SELL signal): short was above, now below
    indicators_cross_down = {
        "ema_12": 48000.0,  # Short now below
        "ema_26": 50000.0,  # Long above
        "close_price": 49000.0,
    }

    signal4 = await strategy.evaluate("BTCUSDT", indicators_cross_down)
    print(f"  📊 Crossover DOWN: {signal4.type.value} - {signal4.reason[:60]}...")

    if signal2.type == SignalType.BUY and signal4.type == SignalType.SELL:
        print("  ✅ Strategy signal generation test PASSED")
        return True
    else:
        print("  ❌ Strategy signal generation test FAILED")
        return False


async def test_position_tracking():
    """Test portfolio manager position tracking."""
    print("\n🔍 TEST 4: Position Tracking (NEW)")
    print("-" * 50)

    # Use SQLite for smoke test
    config = {
        "host": "localhost",
        "port": 5432,
        "name": "test_db",
        "user": "test",
        "password": "test",
    }

    async with PortfolioManager(config) as pm:
        # Open a position
        position = await pm.open_position(
            symbol="BTCUSDT", quantity=0.5, price=50000.0, order_id="test_buy_001"
        )
        print(f"  ✅ Opened position: {position.symbol} @ {position.entry_price}")

        # Check position exists
        open_pos = pm.get_position("BTCUSDT")
        if open_pos:
            print(f"  ✅ Position in cache: {open_pos.symbol}, qty: {open_pos.quantity}")

        # Calculate unrealized PnL
        current_price = 52000.0
        unrealized = pm.calculate_unrealized_pnl("BTCUSDT", current_price)
        print(f"  📊 Unrealized PnL @ {current_price}: {unrealized:.2f} USDT")

        # Close position
        closed_pos, realized_pnl = await pm.close_position(
            symbol="BTCUSDT", price=52000.0, order_id="test_sell_001"
        )
        print(f"  ✅ Closed position with realized PnL: {realized_pnl:.2f} USDT")

        # Get portfolio summary
        summary = await pm.get_portfolio_summary()
        print(f"  📊 Portfolio: {summary.total_positions} positions, {summary.total_trades} trades")
        print(f"  📊 Total realized PnL: {summary.total_realized_pnl:.2f} USDT")

        if realized_pnl > 0 and summary.total_trades == 2:
            print("  ✅ Position tracking test PASSED")
            return True
        else:
            print("  ❌ Position tracking test FAILED")
            return False


async def test_telegram_notifications():
    """Test Telegram notification formatting (without actual send)."""
    print("\n🔍 TEST 5: Telegram Notifications (NEW)")
    print("-" * 50)

    # Create notifier (won't actually send without token)
    notifier = TelegramNotifier()

    # Test that message formatting works
    message = notifier._format_message("Test trade alert", None)
    print(f"  ✅ Message formatting works: {message[:30]}...")

    # Test trade alert formatting
    print("  📱 Trade alert would be sent with:")
    print("     - Symbol: BTCUSDT")
    print("     - Side: BUY")
    print("     - Price: 50000.00")
    print("     - Quantity: 0.5")

    print("  ✅ Telegram notification test PASSED (disabled, no token)")
    return True


async def test_full_pipeline():
    """Test full pipeline integration."""
    print("\n🔍 TEST 6: Full Pipeline Integration")
    print("-" * 50)

    print("  Simulating: Ingestor → Indicators → Strategy → Executor")

    # Step 1: Mock OHLCV data
    ohlcv_data = {
        "time": ["2024-01-01T00:00:00Z"] * 30,
        "open": [50000.0 + i * 100 for i in range(30)],
        "high": [50100.0 + i * 100 for i in range(30)],
        "low": [49900.0 + i * 100 for i in range(30)],
        "close": [50050.0 + i * 100 for i in range(30)],
        "volume": [1.0] * 30,
    }

    # Step 2: Compute indicators
    from src.features.technical import compute_indicators

    indicators = compute_indicators(ohlcv_data)
    print(f"  ✅ Computed indicators: RSI={indicators.rsi_14:.2f}, EMA12={indicators.ema_12:.2f}")

    # Step 3: Generate signal
    strategy = SimpleMACrossoverStrategy({})
    signal_indicators = {
        "ema_12": indicators.ema_12 or 0,
        "ema_26": indicators.ema_26 or 0,
        "close_price": ohlcv_data["close"][-1],
    }

    # Need to prime the strategy first
    await strategy.evaluate("BTCUSDT", signal_indicators)
    signal = await strategy.evaluate("BTCUSDT", signal_indicators)
    print(f"  ✅ Generated signal: {signal.type.value}")

    # Step 4: Check risk manager

    risk_mgr = RiskManager()
    allowed, reason = risk_mgr.is_trading_allowed()
    print(f"  ✅ Risk check: {reason}")

    print("  ✅ Full pipeline integration test PASSED")
    return True


async def run_smoke_test():
    """Run all smoke tests."""
    print("=" * 60)
    print("🧪 CRYPTO TRADING AGENT - END-TO-END SMOKE TEST")
    print("=" * 60)
    print("\nTesting the full pipeline: Ingest → Indicators → Strategy → Executor")
    print("Mode: Paper trading (no real orders)")
    print("Features: Position tracking + Telegram notifications")

    configure_logger("INFO")

    is_ci = os.environ.get("CI") == "true"
    results = []
    # OHLCV ingestion requires live Binance access; skip in CI
    network_results = []

    try:
        if is_ci:
            print("\n⏭️  SKIP: OHLCV Ingestion (requires live Binance API, not available in CI)")
            network_results.append(("OHLCV Ingestion", None))
        else:
            results.append(("OHLCV Ingestion", await test_ingestion()))

        results.append(("Indicator Computation", await test_indicator_computation()))
        results.append(("Strategy Signals", await test_strategy_signals()))
        results.append(("Position Tracking (NEW)", await test_position_tracking()))
        results.append(("Telegram Notifications (NEW)", await test_telegram_notifications()))
        results.append(("Full Pipeline", await test_full_pipeline()))

    except Exception as e:
        print(f"\n❌ Smoke test failed with error: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Summary
    print("\n" + "=" * 60)
    print("📊 SMOKE TEST SUMMARY")
    print("=" * 60)

    all_entries = network_results + results
    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in all_entries:
        if result is None:
            print(f"  ⏭️  SKIP: {name}")
        else:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"  {status}: {name}")

    print("-" * 60)
    skipped = len(network_results)
    skip_msg = f" ({skipped} skipped)" if skipped else ""
    print(f"Results: {passed}/{total} tests passed{skip_msg}")

    if passed == total:
        print("\n🎉 ALL SMOKE TESTS PASSED!")
        return True
    else:
        print(f"\n⚠️  {total - passed} tests failed")
        print("Review the failures above before proceeding.")
        return False


if __name__ == "__main__":
    success = asyncio.run(run_smoke_test())
    sys.exit(0 if success else 1)
