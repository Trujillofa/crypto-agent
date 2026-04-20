"""One-shot: close sentiment-macro-bot's open spot positions via Binance and DB.

Run inside the prod agent container. Stops trading, exits 3 spot longs, and
marks DB positions closed. Idempotent in the sense that positions already
closed in DB are skipped, and zero-balance assets are reported and skipped.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime

import asyncpg

from src.execution.binance_client import BinancePrivateClient
from src.utils.logger import get_logger

logger = get_logger("close_spot")

AGENT_ID = "sentiment-macro-bot"


async def main() -> None:
    api_key = os.environ["BINANCE_API_KEY"].strip()
    api_secret = os.environ["BINANCE_API_SECRET"].strip()

    db = await asyncpg.connect(
        host=os.environ.get("POSTGRES_HOST", "timescaledb"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    )

    rows = await db.fetch(
        """
        SELECT id, symbol, quantity, entry_price
        FROM positions
        WHERE agent_id = $1 AND status = 'open' AND market = 'spot'
        ORDER BY entry_time
        """,
        AGENT_ID,
    )
    if not rows:
        logger.info("No open spot positions for %s", AGENT_ID)
        await db.close()
        return

    logger.info("Found %d open spot positions for %s", len(rows), AGENT_ID)

    async with BinancePrivateClient(api_key, api_secret, test_mode=False) as client:
        for row in rows:
            symbol_full = row["symbol"]
            symbol = symbol_full.split("::")[-1]
            base_asset = symbol.removesuffix("USDT")
            entry_price = float(row["entry_price"])
            db_qty = float(row["quantity"])

            balance = await client.get_asset_balance(base_asset)
            logger.info(
                "Position %s: db_qty=%s exchange_balance=%s entry=%s",
                symbol, db_qty, balance, entry_price,
            )
            if balance <= 0:
                logger.warning("Zero balance for %s — marking position closed without trade", symbol)
                await db.execute(
                    """
                    UPDATE positions SET status='closed', exit_time=$1, exit_price=$2, realized_pnl=0
                    WHERE id=$3
                    """,
                    datetime.now(UTC), entry_price, row["id"],
                )
                continue

            normalized = await client.normalize_sell_quantity(symbol, balance)
            if normalized is None:
                logger.warning("Balance %s for %s below min LOT_SIZE — closing DB record only", balance, symbol)
                await db.execute(
                    """
                    UPDATE positions SET status='closed', exit_time=$1, exit_price=$2, realized_pnl=0
                    WHERE id=$3
                    """,
                    datetime.now(UTC), entry_price, row["id"],
                )
                continue

            order = await client.place_market_order(symbol, "SELL", balance)
            filled_qty = order.executed_quantity if order.executed_quantity > 0 else float(normalized)
            filled_price = order.executed_price or entry_price
            pnl = (filled_price - entry_price) * filled_qty

            logger.info(
                "SOLD %s qty=%s price=%s pnl=%.4f status=%s",
                symbol, filled_qty, filled_price, pnl, order.status,
            )

            await db.execute(
                """
                INSERT INTO trades (time, symbol, side, quantity, price, order_id, pnl, position_id, market, agent_id)
                VALUES ($1, $2, 'SELL', $3, $4, $5, $6, $7, 'spot', $8)
                """,
                datetime.now(UTC),
                symbol_full,
                filled_qty,
                filled_price,
                str(order.order_id),
                pnl,
                row["id"],
                AGENT_ID,
            )
            await db.execute(
                """
                UPDATE positions SET status='closed', exit_time=$1, exit_price=$2, realized_pnl=$3
                WHERE id=$4
                """,
                datetime.now(UTC), filled_price, pnl, row["id"],
            )
            time.sleep(0.2)

    await db.close()
    logger.info("Done")


if __name__ == "__main__":
    asyncio.run(main())
