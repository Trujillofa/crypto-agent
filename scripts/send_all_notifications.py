#!/usr/bin/env python3
"""Send all notification types for verification."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifications.telegram import TelegramConfig, TelegramNotifier, AlertLevel


async def send_all_notifications():
    """Send all notification types."""
    print("🚀 Sending all notification types to @trujillofa_trading_bot...")
    print()

    config = TelegramConfig(
        bot_token="8538622562:AAEptM1aOLyl5G9qL6mPiOUdi5S5Vh5hUd4",
        chat_id="1278127918",
        enabled=True,
        rate_limit_seconds=2,
    )

    async with TelegramNotifier(config) as notifier:
        # 1. Info message
        print("1️⃣  Sending test message...")
        await notifier.send_alert(
            "✅ <b>Setup Complete!</b>\n\nYour Crypto Trading Agent is now fully operational.",
            AlertLevel.INFO,
        )
        await asyncio.sleep(2)

        # 2. Kill switch test
        print("2️⃣  Sending kill switch alert...")
        await notifier.send_kill_switch_alert("Test: Manual activation by user")
        await asyncio.sleep(2)

        # 3. Circuit breaker
        print("3️⃣  Sending circuit breaker alert...")
        await notifier.send_circuit_breaker_alert(
            "max_daily_loss", "Daily loss limit of 5% exceeded"
        )
        await asyncio.sleep(2)

        # 4. Trade execution
        print("4️⃣  Sending trade execution alert...")
        await notifier.send_trade_alert(
            symbol="BTCUSDT", side="BUY", quantity=0.15, price=72500.50, pnl=None
        )
        await asyncio.sleep(2)

        # 5. Trade with profit
        print("5️⃣  Sending profitable trade alert...")
        await notifier.send_trade_alert(
            symbol="ETHUSDT", side="SELL", quantity=2.5, price=3850.00, pnl=125.50
        )
        await asyncio.sleep(2)

        # 6. Daily summary
        print("6️⃣  Sending daily summary...")
        await notifier.send_daily_summary(
            total_pnl=245.75, trades_count=12, win_rate=68.5
        )

    print()
    print("=" * 60)
    print("✅ All notifications sent successfully!")
    print("=" * 60)
    print("\nYou will now receive real-time alerts for:")
    print("  • Kill switch activations")
    print("  • Circuit breaker triggers")
    print("  • Trade executions")
    print("  • Daily performance summaries")
    print("\nYour trading agent is fully operational! 🎉")


if __name__ == "__main__":
    asyncio.run(send_all_notifications())
