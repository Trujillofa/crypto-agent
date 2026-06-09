"""Tests for Bybit perp basis importer (pure units + mocked HTTP, no live net)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from scripts.import_bybit_perp_basis import (
    _normalize_bybit_list,
    fetch_aligned_chunk,
)
from scripts.import_perp_basis_metrics import align_three_feeds


def _bybit_row(open_ms: int, close: float) -> list[str]:
    """Minimal Bybit v5 list item shape for the three kline endpoints (5-elem)."""
    return [str(open_ms), "0", "0", "0", str(close)]


def test_normalize_bybit_list_basic_and_close_time() -> None:
    """Normalization fabricates close_time and the indices align uses ([0],[4],[6])."""
    t0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    interval_ms = 3_600_000  # 1h
    raw = [_bybit_row(t0, 101.23), _bybit_row(t0 + interval_ms, 102.5)]
    rows = _normalize_bybit_list(raw, interval_ms)
    assert len(rows) == 2
    assert rows[0][0] == t0
    assert float(rows[0][4]) == 101.23
    assert rows[0][6] == t0 + (interval_ms - 1)
    # second bar
    assert rows[1][0] == t0 + interval_ms
    assert rows[1][6] == t0 + interval_ms + (interval_ms - 1)


def test_normalize_bybit_then_align_matches_binance_path() -> None:
    """After normalize, shared align_three_feeds produces identical basis_bps logic."""
    t0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = t0 + 3_600_000
    # Same prices as the existing test_perp_basis_align would use
    mark_raw = [_bybit_row(t0, 100.0), _bybit_row(t1, 101.0)]
    index_raw = [_bybit_row(t0, 99.0)]
    premium_raw = [_bybit_row(t0, 0.001), _bybit_row(t1, 0.002)]

    m_rows = _normalize_bybit_list(mark_raw, 3_600_000)
    i_rows = _normalize_bybit_list(index_raw, 3_600_000)
    p_rows = _normalize_bybit_list(premium_raw, 3_600_000)

    aligned, partial = align_three_feeds(m_rows, i_rows, p_rows)
    assert len(aligned) == 1
    assert partial == 1
    assert aligned[0].mark_price == 100.0
    assert aligned[0].index_price == 99.0
    assert aligned[0].basis_bps == (100.0 - 99.0) / 99.0 * 10_000.0


@pytest.mark.asyncio
async def test_fetch_aligned_chunk_bybit_mocked() -> None:
    """One mocked-HTTP chunk test exercising the Bybit path + reuse of align."""
    t0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    interval_ms = 3_600_000

    # Bybit returns newest-first in list; we must still align correctly.
    mark_list = [
        _bybit_row(t0 + interval_ms, 101.0),
        _bybit_row(t0, 100.0),
    ]
    index_list = [_bybit_row(t0, 99.0)]
    premium_list = [
        _bybit_row(t0 + interval_ms, 0.002),
        _bybit_row(t0, 0.001),
    ]

    fake_mark = {"retCode": 0, "result": {"list": mark_list}}
    fake_index = {"retCode": 0, "result": {"list": index_list}}
    fake_premium = {"retCode": 0, "result": {"list": premium_list}}

    class _FakeResp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status = 200

        async def json(self) -> dict:
            return self._payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

    def _make_get():
        def _get(url: str, params: dict | None = None):
            # Route based on which endpoint was requested. Return cm *synchronously*
            # because real aiohttp session.get returns a context manager (no await on get()).
            if "mark-price" in url:
                return _FakeResp(fake_mark)
            if "index-price" in url:
                return _FakeResp(fake_index)
            if "premium-index" in url:
                return _FakeResp(fake_premium)
            raise AssertionError(f"unexpected url {url}")

        return _get

    mock_session = MagicMock()
    mock_session.get.side_effect = _make_get()

    bars, partial = await fetch_aligned_chunk(
        mock_session,
        "SOLUSDT",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )

    assert partial == 1  # t1 missing from index
    assert len(bars) == 1
    b = bars[0]
    assert b.mark_price == 100.0
    assert b.index_price == 99.0
    assert abs(b.basis_bps - ((100.0 - 99.0) / 99.0 * 10000.0)) < 1e-9
    # ensure we called the three distinct endpoints
    calls = [c.args[0] for c in mock_session.get.call_args_list]
    assert any("mark-price-kline" in c for c in calls)
    assert any("index-price-kline" in c for c in calls)
    assert any("premium-index-price-kline" in c for c in calls)
