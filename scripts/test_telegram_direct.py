#!/usr/bin/env python3
"""Direct Telegram test with provided credentials."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifications.telegram import TelegramConfig, TelegramNotifier, AlertLevel


async def test_direct():
    """Test with hardcoded credentials."""
    print("🧪 Testing Telegram with provided credentials...")
    print()

    # Use the provided credentials
    config = TelegramConfig(
        bot_token="8538622562:AAEptM1aOLyl5G9qL6mPiOUdi5S5Vh5hUd4",
        chat_id="1278127918",
        enabled=True,
        rate_limit_seconds=1,  # Shorter for testing
    )

    print(f"Token: {config.bot_token[:15]}...{config.bot_token[-5:]}")
    print(f"Chat ID: {config.chat_id}")
    print()

    async with TelegramNotifier(config) as notifier:
        print("📱 Sending test message to your Telegram...")

        message = """🧪 <b>Crypto Trading Agent - Test</b>

Your Telegram notifications are now configured!

<b>Time:</b> Setup complete
<b>Status:</b> Ready for production

You will receive alerts for:
• Kill switch activation
• Circuit breaker triggers  
• Trade executions
• Daily summaries"""

        try:
            success = await notifier.send_alert(message, AlertLevel.INFO)
            if success:
                print("✅ Message sent successfully!")
                print("   Check your Telegram now!")
                return True
            else:
                print("❌ Failed to send")
                print("\n⚠️  Common fix:")
                print("   1. Message your bot @FAT2728 in Telegram first")
                print("   2. Click /start")
                print("   3. Run this test again")
                return False
        except Exception as e:
            print(f"❌ Error: {e}")
            return False


if __name__ == "__main__":
    success = asyncio.run(test_direct())
    sys.exit(0 if success else 1)
