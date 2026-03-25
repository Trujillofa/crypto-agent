#!/usr/bin/env python3
"""Complete Telegram setup automation.

Run this after getting credentials from Telegram:
    python scripts/complete_telegram_setup.py TOKEN CHAT_ID

Or interactively:
    python scripts/complete_telegram_setup.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifications.telegram import AlertLevel, TelegramNotifier


def update_env_file(token: str, chat_id: str) -> bool:
    """Update .env file with Telegram credentials."""
    env_path = Path(__file__).parent.parent / ".env"

    if not env_path.exists():
        print("❌ .env file not found")
        return False

    # Read current content
    with open(env_path) as f:
        lines = f.readlines()

    # Update or add Telegram config
    telegram_config = {
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_CHAT_ID": chat_id,
        "TELEGRAM_ENABLED": "true",
        "TELEGRAM_RATE_LIMIT": "5",
    }

    new_lines = []
    updated = set()

    for line in lines:
        stripped = line.strip()
        # Check if this line sets a Telegram variable
        for key in telegram_config:
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                new_lines.append(f"{key}={telegram_config[key]}\n")
                updated.add(key)
                break
        else:
            new_lines.append(line)

    # Add any missing variables
    for key, value in telegram_config.items():
        if key not in updated:
            new_lines.append(f"{key}={value}\n")

    # Write back
    with open(env_path, "w") as f:
        f.writelines(new_lines)

    print(f"✅ Updated {env_path}")
    return True


async def test_telegram(token: str, chat_id: str) -> bool:
    """Test Telegram notifications."""
    from src.notifications.telegram import TelegramConfig

    config = TelegramConfig(bot_token=token, chat_id=chat_id, enabled=True)

    async with TelegramNotifier(config) as notifier:
        print("\n🧪 Testing Telegram notifications...")

        # Test 1: Basic alert
        print("1️⃣ Sending test message...")
        success = await notifier.send_alert(
            "🧪 <b>Crypto Trading Agent</b>\n\nSetup complete! Notifications are working.",
            level=AlertLevel.INFO,
        )
        if not success:
            print("   ❌ Failed")
            return False
        print("   ✅ Test message sent")

        # Test 2: Kill switch alert
        print("2️⃣ Testing kill switch alert...")
        success = await notifier.send_kill_switch_alert("Test activation")
        if not success:
            print("   ❌ Failed")
            return False
        print("   ✅ Kill switch alert sent")

        # Test 3: Circuit breaker
        print("3️⃣ Testing circuit breaker alert...")
        success = await notifier.send_circuit_breaker_alert("test_breaker")
        if not success:
            print("   ❌ Failed")
            return False
        print("   ✅ Circuit breaker alert sent")

        # Test 4: Trade alert
        print("4️⃣ Testing trade alert...")
        success = await notifier.send_trade_alert(
            symbol="BTCUSDT", side="BUY", quantity=0.1, price=72500.0
        )
        if not success:
            print("   ❌ Failed")
            return False
        print("   ✅ Trade alert sent")

    return True


def restart_agent() -> bool:
    """Restart Docker agent."""
    import subprocess

    print("\n🔄 Restarting agent with Telegram enabled...")
    try:
        result = subprocess.run(
            ["docker-compose", "restart", "agent"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        if result.returncode == 0:
            print("✅ Agent restarted successfully")
            return True
        else:
            print(f"❌ Failed to restart: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Error restarting: {e}")
        return False


def main():
    """Main setup function."""
    print("=" * 60)
    print("🚀 Telegram Setup Automation")
    print("=" * 60)

    # Get credentials
    if len(sys.argv) >= 3:
        token = sys.argv[1]
        chat_id = sys.argv[2]
    else:
        print("\n📋 Manual Entry Mode")
        print("-" * 60)
        token = input("Enter Bot Token (from @BotFather): ").strip()
        chat_id = input("Enter Chat ID (from @userinfobot): ").strip()

    if not token or not chat_id:
        print("❌ Token and Chat ID are required")
        return 1

    # Update .env
    print("\n📝 Updating configuration...")
    if not update_env_file(token, chat_id):
        return 1

    # Load environment
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    os.environ["TELEGRAM_ENABLED"] = "true"

    # Test notifications
    try:
        success = asyncio.run(test_telegram(token, chat_id))
        if not success:
            print("\n❌ Telegram test failed")
            print("Check your token and chat ID are correct")
            return 1
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        return 1

    # Restart agent
    restart_agent()

    print("\n" + "=" * 60)
    print("✅ Telegram setup complete!")
    print("=" * 60)
    print("\nYou will now receive notifications for:")
    print("  • Kill switch activation")
    print("  • Circuit breaker triggers")
    print("  • Trade executions")
    print("  • Daily summaries")
    print("\nCheck your Telegram messages now!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
