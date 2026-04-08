import asyncio
from datetime import UTC, datetime, timedelta

import aiohttp
import asyncpg


async def backfill_symbol(symbol, start_date, end_date):
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    conn = await asyncpg.connect(
        "postgresql://trading:834ced988853c03a046e056c3c819a3c@timescaledb:5432/marketdata"
    )

    total_inserted = 0
    current_start = start_date

    async with aiohttp.ClientSession() as session:
        while current_start < end_date:
            current_end = min(current_start + timedelta(days=90), end_date)

            params = {
                "symbol": symbol,
                "startTime": int(current_start.timestamp() * 1000),
                "endTime": int(current_end.timestamp() * 1000),
                "limit": 1000,
            }

            async with session.get(url, params=params) as resp:
                if resp.status == 429:
                    await asyncio.sleep(1)
                    continue
                data = await resp.json()

                for item in data:
                    try:
                        await conn.execute(
                            """
                            INSERT INTO funding_rates (symbol, funding_time, funding_rate, mark_price)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (symbol, funding_time) DO NOTHING
                        """,
                            item["symbol"],
                            datetime.fromtimestamp(item["fundingTime"] / 1000, tz=UTC),
                            float(item["fundingRate"]),
                            float(item["markPrice"]) if item.get("markPrice") else None,
                        )
                        total_inserted += 1
                    except Exception as e:
                        print(f"Error inserting {item}: {e}")

                print(
                    f"{symbol}: {current_start.date()} - Inserted {len(data)} rates (total: {total_inserted})"
                )

            current_start = current_end
            await asyncio.sleep(0.1)

    await conn.close()
    print(f"Total inserted: {total_inserted}")


if __name__ == "__main__":
    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = datetime.now(UTC)
    asyncio.run(backfill_symbol("AVAXUSDT", start, end))
