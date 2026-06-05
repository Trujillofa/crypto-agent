"""Unit tests for basis/premium coverage audit helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from scripts.audit_basis_premium_coverage import has_valid_overlap, overlap_bounds


def test_has_valid_overlap_when_ranges_intersect() -> None:
    ohlcv_min = datetime(2024, 6, 1, tzinfo=UTC)
    ohlcv_max = datetime(2024, 12, 1, tzinfo=UTC)
    basis_min = datetime(2024, 1, 1, tzinfo=UTC)
    basis_max = datetime(2024, 7, 1, tzinfo=UTC)
    assert has_valid_overlap(ohlcv_min, ohlcv_max, basis_min, basis_max) is True
    start, end = overlap_bounds(ohlcv_min, ohlcv_max, basis_min, basis_max)
    assert start == ohlcv_min
    assert end == basis_max


def test_has_valid_overlap_false_when_basis_entirely_after_ohlcv() -> None:
    ohlcv_min = datetime(2024, 1, 1, tzinfo=UTC)
    ohlcv_max = datetime(2024, 6, 1, tzinfo=UTC)
    basis_min = datetime(2025, 1, 1, tzinfo=UTC)
    basis_max = datetime(2025, 6, 1, tzinfo=UTC)
    assert has_valid_overlap(ohlcv_min, ohlcv_max, basis_min, basis_max) is False


def test_has_valid_overlap_false_when_basis_entirely_before_ohlcv() -> None:
    ohlcv_min = datetime(2024, 6, 1, tzinfo=UTC)
    ohlcv_max = datetime(2024, 12, 1, tzinfo=UTC)
    basis_min = datetime(2023, 1, 1, tzinfo=UTC)
    basis_max = datetime(2023, 12, 1, tzinfo=UTC)
    assert has_valid_overlap(ohlcv_min, ohlcv_max, basis_min, basis_max) is False
