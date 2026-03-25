#!/usr/bin/env python3
"""Test database connection (TimescaleDB or SQLite fallback)."""

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path for absolute imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ingest.db import TimescaleWriter
from src.ingest.metrics import IngestMetrics
from src.ingest.models import Ohlcv


def test_timescaledb_connection():
    """Test TimescaleDB connection."""
    print("🧪 Testing Database Connection...")
    print()

    # Check if we're using .env values or defaults
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    database = os.getenv("POSTGRES_DB", "marketdata")
    user = os.getenv("POSTGRES_USER", "trading")
    password = os.getenv("POSTGRES_PASSWORD", "")

    print("1️⃣  Configuration:")
    print(f"   Host: {host}")
    print(f"   Port: {port}")
    print(f"   Database: {database}")
    print(f"   User: {user}")
    print()

    config = {
        "host": host,
        "port": port,
        "name": database,
        "user": user,
        "password": password,
    }

    metrics = IngestMetrics()

    print("2️⃣  Attempting to connect...")
    try:
        writer = TimescaleWriter(config, metrics)

        # Use async context manager properly
        async def test_connection():
            async with writer as w:
                print("   ✅ Connection established!")
                print()

                # Test insert
                print("3️⃣  Testing OHLCV insert...")
                test_candle = Ohlcv(
                    symbol="TESTUSDT",
                    timeframe="1m",
                    open_time=datetime.now(UTC),
                    close_time=datetime.now(UTC),
                    open_price=50000.0,
                    high_price=51000.0,
                    low_price=49000.0,
                    close_price=50500.0,
                    volume=100.0,
                )

                await w.write_ohlcv(test_candle)
                print("   ✅ Successfully inserted test candle")
                print()

                # Check if using SQLite fallback
                if writer._use_sqlite:
                    print("   ⚠️  Using SQLite fallback (TimescaleDB unavailable)")
                    print("   SQLite path: data/ohlcv.sqlite")
                else:
                    print("   ✅ Using TimescaleDB")

                    # Check if hypertable exists
                    cursor = writer._conn.cursor()
                    cursor.execute(
                        "SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = 'ohlcv'"
                    )
                    result = cursor.fetchone()
                    if result:
                        print("   ✅ Hypertable 'ohlcv' exists")
                    else:
                        print("   ⚠️  Hypertable 'ohlcv' not found")

                print()
                print("=" * 50)
                print("✅ Database tests passed!")
                print("=" * 50)
                return True

        return asyncio.run(test_connection())

    except Exception as exc:
        print(f"   ❌ FAILED - {exc}")
        return False


if __name__ == "__main__":
    success = test_timescaledb_connection()
    exit(0 if success else 1)
