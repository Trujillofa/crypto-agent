#!/usr/bin/env python3
"""Monitor sentiment-macro-bot trade milestones and send Telegram alerts.

Tracks cumulative trade count for agent_id='sentiment-macro-bot' and sends
alerts at 50, 75, and 100 trades (decision gate checkpoints).

Usage:
    # Run once (cron every 15 minutes)
    python scripts/monitor_sentiment_macro.py

    # Run with custom thresholds
    python scripts/monitor_sentiment_macro.py --thresholds 50,75,100

Environment:
    DATABASE_URL: PostgreSQL connection string
    TELEGRAM_BOT_TOKEN: Bot token for notifications
    TELEGRAM_CHAT_ID: Chat ID for notifications
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.notifications.telegram import AlertLevel, TelegramNotifier
from src.utils.logger import get_logger

logger = get_logger("sentiment_macro_monitor")

DEFAULT_THRESHOLDS = [50, 75, 100]
AGENT_ID = "sentiment-macro-bot"
STATE_FILE = Path("/tmp/sentiment_macro_milestones.json")


@dataclass
class TradeStats:
    """Trade statistics for an agent."""

    total_trades: int
    total_pnl: float
    win_count: int
    loss_count: int
    win_rate: float


async def get_trade_stats(conn: asyncpg.Connection, agent_id: str) -> TradeStats:
    """Query cumulative trade stats from database."""
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*) as total_trades,
            COALESCE(SUM(realized_pnl), 0) as total_pnl,
            COUNT(*) FILTER (WHERE realized_pnl > 0) as win_count,
            COUNT(*) FILTER (WHERE realized_pnl < 0) as loss_count
        FROM positions
        WHERE status = 'closed' AND agent_id = $1
        """,
        agent_id,
    )

    total_trades = int(row["total_trades"] or 0)
    total_pnl = float(row["total_pnl"] or 0.0)
    win_count = int(row["win_count"] or 0)
    loss_count = int(row["loss_count"] or 0)
    win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

    return TradeStats(
        total_trades=total_trades,
        total_pnl=total_pnl,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
    )


def load_notified_milestones() -> set[int]:
    """Load already-notified milestones from state file."""
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("notified", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def save_notified_milestones(milestones: set[int]) -> None:
    """Save notified milestones to state file."""
    STATE_FILE.write_text(
        json.dumps(
            {
                "notified": sorted(milestones),
                "last_update": datetime.now(UTC).isoformat(),
            }
        )
    )


async def send_milestone_alert(
    notifier: TelegramNotifier,
    milestone: int,
    stats: TradeStats,
) -> bool:
    """Send Telegram alert for trade milestone."""
    pnl_sign = "+" if stats.total_pnl >= 0 else ""
    pnl_emoji = "📈" if stats.total_pnl >= 0 else "📉"

    # Determine gate status
    if milestone == 100:
        gate_name = "FINAL DECISION GATE"
        recommendation = "Evaluate: Keep if profitable, disable if breakeven or worse"
    elif milestone == 75:
        gate_name = "CHECKPOINT (75%)"
        recommendation = "Trend check: 25 trades remaining to final evaluation"
    else:  # 50
        gate_name = "CHECKPOINT (50%)"
        recommendation = "Halfway: 50 trades remaining to final evaluation"

    message = f"""🎯 <b>SENTIMENT-MACRO MILESTONE: {milestone} TRADES</b>

🤖 Agent: <code>{AGENT_ID}</code>
🔢 Total Trades: <b>{stats.total_trades}</b>
{pnl_emoji} Cumulative P&L: {pnl_sign}{stats.total_pnl:.2f} USDT
🎯 Win Rate: {stats.win_rate:.1f}% ({stats.win_count}/{stats.loss_count})

📊 {gate_name}
📝 {recommendation}

🕐 {datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}"""

    success = await notifier.send_alert(message, AlertLevel.INFO)
    if success:
        logger.info(f"Sent milestone alert for {milestone} trades")
    else:
        logger.error(f"Failed to send milestone alert for {milestone} trades")
    return success


async def main() -> int:
    """Main monitoring loop."""
    parser = argparse.ArgumentParser(description="Monitor sentiment-macro trade milestones")
    parser.add_argument(
        "--thresholds",
        type=str,
        default=",".join(map(str, DEFAULT_THRESHOLDS)),
        help="Comma-separated milestone thresholds (default: 50,75,100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print alerts instead of sending to Telegram",
    )
    args = parser.parse_args()

    thresholds = [int(t.strip()) for t in args.thresholds.split(",")]

    # Load already-notified milestones
    notified = load_notified_milestones()

    # Check if there are new milestones to check
    pending = set(thresholds) - notified
    if not pending:
        logger.info("All milestones already notified, nothing to do")
        return 0

    # Get database connection
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        return 1

    conn = None
    try:
        conn = await asyncpg.connect(database_url)

        # Get current trade stats
        stats = await get_trade_stats(conn, AGENT_ID)
        logger.info(
            f"Agent {AGENT_ID}: {stats.total_trades} trades, "
            f"P&L: {stats.total_pnl:.2f}, Win rate: {stats.win_rate:.1f}%"
        )

        # Check for milestone crossings
        new_notified = set()
        notifier = TelegramNotifier()

        for threshold in sorted(pending):
            if stats.total_trades >= threshold:
                logger.info(f"Milestone reached: {threshold} trades")

                if args.dry_run:
                    print(f"\n[DRY RUN] Would send alert for {threshold} trades:")
                    print(f"  Total: {stats.total_trades}, P&L: {stats.total_pnl:.2f}")
                    new_notified.add(threshold)
                else:
                    async with notifier:
                        success = await send_milestone_alert(notifier, threshold, stats)
                        if success:
                            new_notified.add(threshold)

        # Save updated notified milestones
        if new_notified:
            notified.update(new_notified)
            save_notified_milestones(notified)
            logger.info(f"Updated notified milestones: {sorted(notified)}")

        return 0

    except Exception as e:
        logger.error(f"Monitor failed: {e}")
        return 1
    finally:
        if conn:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
