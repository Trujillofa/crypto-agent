"""Tests for perp basis kline alignment (no network)."""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.import_perp_basis_metrics import align_three_feeds


def _kline(open_ms: int, close: float, close_ms: int | None = None) -> list[object]:
    close_ms = close_ms if close_ms is not None else open_ms + 3_599_999
    return [open_ms, "0", "0", "0", str(close), "0", close_ms]


def test_align_three_feeds_intersection_only() -> None:
    t0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = int(datetime(2024, 1, 1, 1, tzinfo=UTC).timestamp() * 1000)
    mark = [_kline(t0, 100.0), _kline(t1, 101.0)]
    index = [_kline(t0, 99.0)]
    premium = [_kline(t0, 0.001), _kline(t1, 0.002)]

    aligned, partial = align_three_feeds(mark, index, premium)
    assert len(aligned) == 1
    assert partial == 1  # t1 present in mark/premium but missing index
    assert aligned[0].mark_price == 100.0
    assert aligned[0].index_price == 99.0
    assert aligned[0].premium_index == 0.001
    assert aligned[0].basis_bps > 0


def test_align_rejects_mismatched_close_time() -> None:
    t0 = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    mark = [_kline(t0, 100.0, close_ms=t0 + 3_599_999)]
    index = [_kline(t0, 99.0, close_ms=t0 + 7_199_999)]
    premium = [_kline(t0, 0.001, close_ms=t0 + 3_599_999)]

    aligned, partial = align_three_feeds(mark, index, premium)
    assert len(aligned) == 0
    assert partial >= 1
