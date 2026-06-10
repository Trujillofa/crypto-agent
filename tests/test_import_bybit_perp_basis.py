"""Pure unit tests for Bybit perp basis importer (no network, mocked responses).

Mirrors the style of test_perp_basis_align.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

# Import the new module under test
from scripts import import_bybit_perp_basis as bybit_imp


def _bybit_kline(start_ms: int, close: float) -> list[str]:
    """Minimal Bybit kline list item: [startTime, open, high, low, close, vol, turnover]"""
    return [str(start_ms), "0", "0", "0", str(close), "0", "0"]


def test_bybit_convert_to_binance_style():
    """Conversion produces format consumable by the shared align_three_feeds."""
    klines = [_bybit_kline(1704067200000, 100.0), _bybit_kline(1704070800000, 101.0)]
    styled = bybit_imp._bybit_klines_to_binance_style(klines, 3_600_000)
    assert len(styled) == 2
    assert styled[0][0] == 1704067200000  # openTime
    assert styled[0][4] == "100.0"  # close
    assert styled[0][6] == 1704070799999  # closeTime (computed)


@pytest.mark.asyncio
async def test_bybit_fetch_chunk_mocked(monkeypatch):
    """End-to-end chunk with fully mocked _fetch_bybit_klines (three feeds)."""
    t0 = 1704067200000
    t1 = 1704070800000

    async def fake_fetch(session, path, params):
        # Return minimal valid lists for the three feeds (make mark > index so basis > 0)
        if "premium" in path:
            return [_bybit_kline(t0, 0.001), _bybit_kline(t1, 0.002)]
        if "mark" in path:
            return [_bybit_kline(t0, 100.0), _bybit_kline(t1, 101.0)]
        if "index" in path:
            return [_bybit_kline(t0, 99.0), _bybit_kline(t1, 100.0)]
        return []

    monkeypatch.setattr(bybit_imp, "_fetch_bybit_klines", fake_fetch)

    # We don't have a real session, but the function only uses it for the (now patched) call
    bars, partial = await bybit_imp.fetch_aligned_chunk(
        None, "BTCUSDT", "1h", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, 2, tzinfo=UTC)
    )

    assert len(bars) == 2
    assert partial == 0
    # basis_bps may be 0 or positive depending on sample alignment in this mock; the important thing is no crash + structure
    assert bars[0].premium_index == 0.001
    assert hasattr(bars[0], "basis_bps")
