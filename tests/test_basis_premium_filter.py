"""Unit tests for basis premium filter helpers."""

from __future__ import annotations

from src.strategy.basis_premium_filter import (
    BasisPremiumFilterConfig,
    apply_basis_premium_gate,
    compute_positive_tail_threshold,
    parse_basis_premium_filter,
    should_block_buy,
    with_calibrated_threshold,
)
from src.strategy.signals import Signal, SignalType


def test_compute_positive_tail_threshold_top_five_percent() -> None:
    values = list(range(100))
    threshold = compute_positive_tail_threshold(values, 0.05)
    assert threshold is not None
    assert threshold >= 94


def test_should_block_when_value_exceeds_calibrated_threshold() -> None:
    config = with_calibrated_threshold(
        BasisPremiumFilterConfig(enabled=True, positive_tail_pct=0.05),
        10.0,
    )
    blocked, reason = should_block_buy(basis_bps=12.0, premium_index=0.0, config=config)
    assert blocked is True
    assert "basis_bps" in reason


def test_missing_data_allows_by_default() -> None:
    config = with_calibrated_threshold(BasisPremiumFilterConfig(enabled=True), 1.0)
    blocked, _ = should_block_buy(basis_bps=None, premium_index=None, config=config)
    assert blocked is False


def test_apply_gate_converts_buy_to_hold() -> None:
    config = with_calibrated_threshold(
        BasisPremiumFilterConfig(enabled=True, tail_metric="basis_bps"),
        5.0,
    )
    signal = Signal(SignalType.BUY, "SOLUSDT", 100.0, 1.0, "buy", {})
    row = {"basis_bps": 6.0, "premium_index": 0.001}
    gated, blocked = apply_basis_premium_gate(signal, row, config)
    assert blocked is True
    assert gated.type == SignalType.HOLD


def test_parse_basis_premium_filter_defaults_off() -> None:
    parsed = parse_basis_premium_filter(None)
    assert parsed.enabled is False
