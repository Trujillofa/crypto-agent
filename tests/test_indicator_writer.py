from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.writer import IndicatorWriter, StoredIndicator


@pytest.mark.asyncio
async def test_insert_row_uses_single_values_block_with_expected_placeholders(monkeypatch):
    writer = IndicatorWriter({})
    connection = AsyncMock()

    class FakeAcquire:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, exc_type, exc, tb):
            return False

    pool = MagicMock()
    pool.acquire.return_value = FakeAcquire()
    monkeypatch.setattr("src.features.writer.get_pool", lambda: pool)

    indicator = StoredIndicator(
        time=datetime(2026, 3, 18, 0, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="1h",
        rsi_14=50.0,
        rsi_7=48.0,
        macd=0.1,
        macd_signal=0.05,
        macd_hist=0.05,
        bb_upper_dist=0.02,
        bb_lower_dist=0.01,
        atr_14=100.0,
        atr_pct=0.01,
        ema_8=1002.0,
        ema_10=1001.5,
        ema_12=1000.0,
        ema_14=999.8,
        ema_21=999.5,
        ema_24=999.3,
        ema_26=999.0,
        ema_30=998.8,
        ema_50=995.0,
        ema_200=900.0,
        sma_20=998.0,
        sma_50=990.0,
        sma_200=880.0,
        vwap=997.0,
        stoch_k=60.0,
        stoch_d=58.0,
        cci=120.0,
        ema_slope_50=0.006,
        volatility_percentile=65.0,
        atr_percentile=55.0,
        volume_regime=10.0,
        price_vs_weekly=0.02,
        price_vs_monthly=0.03,
        rsi_slope=5.0,
        trend_consistency=70.0,
    )

    await writer._insert_row(indicator)

    query = connection.execute.await_args.args[0]
    values = connection.execute.await_args.args[1:]

    assert query.count(") VALUES (") == 1
    assert "$37" in query
    assert "$38" not in query
    assert len(values) == 37
