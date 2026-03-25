#!/usr/bin/env python3
"""Direct Telegram test with provided credentials."""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifications.telegram import AlertLevel, TelegramConfig, TelegramNotifier


async def test_direct():
    """Test Telegram with credentials from environment."""
    print("🧪 Testing Telegram with credentials from environment...")
    print()

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        print("❌ TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        print("   1. Copy .env.example to .env")
        print("   2. Fill in your Telegram credentials")
        sys.exit(1)

    config = TelegramConfig(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=True,
        rate_limit_seconds=1,
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
