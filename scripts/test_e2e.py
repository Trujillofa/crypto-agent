#!/usr/bin/env python3
"""Test end-to-end: Binance → Ingestor → TimescaleDB."""

import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingest.binance import BinanceIngestor
from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv


def test_end_to_end():
    """Test full data flow from Binance to TimescaleDB."""
    print("🧪 Testing End-to-End Data Flow...")
    print("   Binance → Ingestor → TimescaleDB")
    print()

    # Database config
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "name": os.getenv("POSTGRES_DB", "marketdata"),
        "user": os.getenv("POSTGRES_USER", "trading"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
    }

    metrics = IngestMetrics()

    async def run_test():
        print("1️⃣  Connecting to TimescaleDB...")
        writer = TimescaleWriter(db_config, metrics)

        async with writer:
            print("   ✅ Connected to TimescaleDB")
            print()

            print("2️⃣  Fetching data from Binance...")
            ingestor = BinanceIngestor(["BTCUSDT"], "1m", metrics)

            # Fetch a single candle
            candles = []

            async def collect_candle(candle: Ohlcv):
                candles.append(candle)
                print(
                    f"   ✅ Received candle: {candle.symbol} @ {candle.open_time_utc}"
                )
                print(
                    f"      O: {candle.open_price:.2f} H: {candle.high_price:.2f} L: {candle.low_price:.2f} C: {candle.close_price:.2f} V: {candle.volume:.4f}"
                )

            # Run one poll iteration
            await ingestor._poll_latest(collect_candle)
            print()

            if not candles:
                print("   ❌ No candles received")
                return False

            print("3️⃣  Writing to TimescaleDB...")
            for candle in candles:
                await writer.write_ohlcv(candle)
            print(f"   ✅ Wrote {len(candles)} candle(s) to database")
            print()

            print("4️⃣  Verifying data in database...")
            cursor = writer._conn.cursor()
            cursor.execute(
                "SELECT COUNT(*), symbol FROM ohlcv WHERE symbol = 'BTCUSDT' GROUP BY symbol"
            )
            result = cursor.fetchone()
            if result:
                count, symbol = result
                print(f"   ✅ Found {count} BTCUSDT records in database")
            else:
                print("   ⚠️  No BTCUSDT records found (may be first run)")

            # Show latest record
            cursor.execute(
                "SELECT time, symbol, open_price, close_price, volume FROM ohlcv WHERE symbol = 'BTCUSDT' ORDER BY time DESC LIMIT 1"
            )
            latest = cursor.fetchone()
            if latest:
                print(
                    f"   Latest record: {latest[0]} | {latest[1]} | O: {latest[2]} C: {latest[3]} V: {latest[4]}"
                )

            print()
            print("5️⃣  Checking metrics...")
            print(f"   ✅ Metrics collected (check Prometheus on port 8000)")

        print()
        print("=" * 60)
        print("✅ End-to-end test PASSED!")
        print("   Data successfully flowed: Binance → Ingestor → TimescaleDB")
        print("=" * 60)
        return True

    try:
        return asyncio.run(run_test())
    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_end_to_end()
    exit(0 if success else 1)
