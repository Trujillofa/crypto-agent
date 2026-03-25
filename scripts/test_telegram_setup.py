#!/usr/bin/env python3
"""Quick test for Telegram notifications."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifications.telegram import AlertLevel, TelegramNotifier


async def test_telegram():
    """Test Telegram notifications."""
    print("🧪 Testing Telegram Notifications...")
    print()

    # Check if configured
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or token.startswith("#"):
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        print("   1. Get token from @BotFather")
        print("   2. Edit .env and uncomment: TELEGRAM_BOT_TOKEN=your_token")
        return False

    if not chat_id or chat_id.startswith("#"):
        print("❌ TELEGRAM_CHAT_ID not set in .env")
        print("   1. Get ID from @userinfobot")
        print("   2. Edit .env and uncomment: TELEGRAM_CHAT_ID=your_id")
        return False

    print("✅ Configuration found:")
    print(f"   Bot Token: {token[:10]}...{token[-5:]}")
    print(f"   Chat ID: {chat_id}")
    print()

    # Create notifier and test
    async with TelegramNotifier() as notifier:
        print("1️⃣  Sending test message...")
        success = await notifier.send_alert(
            "🧪 <b>Test Message</b>\n\nYour Crypto Trading Agent is configured correctly!",
            level=AlertLevel.INFO,
        )

        if success:
            print("   ✅ Test message sent successfully!")
        else:
            print("   ❌ Failed to send message")
            print("   Check your bot token and chat ID")
            return False

        print()
        print("2️⃣  Sending kill switch test...")
        success = await notifier.send_kill_switch_alert("Test: Manual kill switch activation")

        if success:
            print("   ✅ Kill switch alert sent!")
        else:
            print("   ❌ Failed to send kill switch alert")
            return False

        print()
        print("3️⃣  Sending circuit breaker test...")
        success = await notifier.send_circuit_breaker_alert(
            "consecutive_losses", "5 consecutive losing trades detected"
        )

        if success:
            print("   ✅ Circuit breaker alert sent!")
        else:
            print("   ❌ Failed to send circuit breaker alert")
            return False

        print()
        print("4️⃣  Sending trade alert test...")
        success = await notifier.send_trade_alert(
            symbol="BTCUSDT", side="BUY", quantity=0.1, price=72500.0, pnl=None
        )

        if success:
            print("   ✅ Trade alert sent!")
        else:
            print("   ❌ Failed to send trade alert")
            return False

    print()
    print("=" * 60)
    print("✅ All Telegram tests passed!")
    print("=" * 60)
    print()
    print("Your notifications are working correctly.")
    print("You will receive alerts for:")
    print("  • Kill switch activation")
    print("  • Circuit breaker triggers")
    print("  • Trade executions")
    print("  • Daily summaries")
    print()
    print("Restart the agent to enable Telegram:")
    print("  docker-compose restart agent")

    return True


if __name__ == "__main__":
    # Load .env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

    success = asyncio.run(test_telegram())
    sys.exit(0 if success else 1)
