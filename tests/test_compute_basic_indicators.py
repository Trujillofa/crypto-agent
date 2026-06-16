from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.compute_basic_indicators import (
    ComputeArgs,
    build_db_config,
    parse_args,
    process_rows,
)


def test_parse_args_normalizes_symbol_and_defaults_lookback() -> None:
    args = parse_args(["--symbol", "linkusdt", "--timeframe", "4h"])

    assert args == ComputeArgs(symbol="LINKUSDT", timeframe="4h", lookback=250)


def test_build_db_config_reads_environment_values() -> None:
    config = build_db_config(
        {
            "DB_HOST": "db.internal",
            "DB_PORT": "5439",
            "DB_NAME": "marketdata_test",
            "DB_USER": "trader",
            "DB_PASSWORD": "secret",
        }
    )

    assert config == {
        "host": "db.internal",
        "port": 5439,
        "name": "marketdata_test",
        "user": "trader",
        "password": "secret",
    }


@pytest.mark.asyncio
async def test_process_rows_writes_symbol_specific_indicators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_time = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    for index in range(251):
        price = 100.0 + index
        rows.append(
            {
                "time": base_time + timedelta(hours=4 * index),
                "open_price": price,
                "high_price": price + 1.0,
                "low_price": price - 1.0,
                "close_price": price + 0.5,
                "volume": 1000.0 + index,
            }
        )

    written = []

    class FakeWriter:
        async def write_indicators(self, indicator):
            written.append(indicator)

    indicator = SimpleNamespace(
        rsi_14=50.0,
        rsi_7=52.0,
        macd=1.0,
        macd_signal=0.5,
        macd_hist=0.5,
        bb_upper_dist=0.02,
        bb_lower_dist=0.01,
        atr_14=2.0,
        atr_pct=0.01,
        ema_8=102.0,
        ema_10=101.5,
        ema_14=100.8,
        ema_21=100.3,
        ema_24=100.1,
        ema_30=99.8,
        ema_12=101.0,
        ema_26=100.0,
        ema_50=99.0,
        ema_200=95.0,
        sma_20=100.0,
        sma_40=99.5,
        sma_50=99.0,
        sma_60=98.5,
        sma_200=96.0,
        vwap=100.5,
        stoch_k=55.0,
        stoch_d=53.0,
        cci=110.0,
        ema_slope_50=0.01,
        volatility_percentile=60.0,
        atr_percentile=58.0,
        volume_regime=15.0,
        price_vs_weekly=0.02,
        price_vs_monthly=0.03,
        rsi_slope=4.0,
        trend_consistency=80.0,
    )

    monkeypatch.setattr(
        "scripts.compute_basic_indicators.compute_indicators",
        lambda data: indicator,
    )

    processed, errors = await process_rows(
        rows,
        symbol="LINKUSDT",
        timeframe="4h",
        lookback=250,
        writer=FakeWriter(),
    )

    assert processed == 1
    assert errors == 0
    assert len(written) == 1
    assert written[0].symbol == "LINKUSDT"
    assert written[0].timeframe == "4h"
    assert written[0].time == rows[-1]["time"]


@pytest.mark.asyncio
async def test_process_rows_requires_minimum_history() -> None:
    rows = [
        {
            "time": datetime(2025, 1, 1, tzinfo=UTC),
            "open_price": 1.0,
            "high_price": 2.0,
            "low_price": 0.5,
            "close_price": 1.5,
            "volume": 10.0,
        }
    ]

    with pytest.raises(ValueError, match="Not enough data"):
        await process_rows(
            rows,
            symbol="AVAXUSDT",
            timeframe="4h",
            lookback=250,
            writer=SimpleNamespace(write_indicators=None),
        )
