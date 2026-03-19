from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.download_historical import download_klines, parse_args, save_to_csv


def test_parse_args_defaults_end_and_db_flag() -> None:
    args = parse_args(["--symbol", "BTCUSDT"])

    assert args.symbol == "BTCUSDT"
    assert args.interval == "5m"
    assert args.db is False
    assert isinstance(args.end, str)


@pytest.mark.asyncio
async def test_download_klines_normalizes_response() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def _fetch_klines(
            self,
            symbol: str,
            interval: str,
            start_time: int,
            limit: int,
        ):
            self.calls += 1
            if self.calls > 1:
                return []
            assert symbol == "LINKUSDT"
            assert interval == "4h"
            assert limit == 1000
            return [
                [
                    1704067200000,
                    "15.0",
                    "16.0",
                    "14.5",
                    "15.5",
                    "1000",
                    1704081599999,
                    "15500",
                    123,
                    "500",
                    "7750",
                    "0",
                ]
            ]

    rows = await download_klines(
        FakeClient(),
        "LINKUSDT",
        "4h",
        "2024-01-01",
        "2024-01-02",
    )

    assert len(rows) == 1
    assert rows[0]["symbol"] == "LINKUSDT"
    assert rows[0]["timeframe"] == "4h"
    assert rows[0]["time"] == datetime(2024, 1, 1, tzinfo=UTC)
    assert rows[0]["close_price"] == 15.5
    assert rows[0]["trades"] == 123


def test_save_to_csv_writes_expected_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "candles.csv"
    save_to_csv(
        [
            {
                "time": datetime(2024, 1, 1, tzinfo=UTC),
                "symbol": "AVAXUSDT",
                "timeframe": "4h",
                "open_price": 10.0,
                "high_price": 11.0,
                "low_price": 9.5,
                "close_price": 10.5,
                "volume": 200.0,
                "close_time": 1704081599999,
                "quote_volume": 2100.0,
                "trades": 50,
                "taker_buy_base": 100.0,
                "taker_buy_quote": 1050.0,
            }
        ],
        csv_path,
    )

    content = csv_path.read_text(encoding="utf-8")
    assert "symbol,timeframe,open_price" in content
    assert "AVAXUSDT,4h,10.0,11.0,9.5,10.5,200.0" in content
