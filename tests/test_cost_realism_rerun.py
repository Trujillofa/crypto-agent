"""Tests for cost-realism experiment harness."""

from __future__ import annotations

import pytest

from src.backtest.cost_overrides import (
    LEGACY_FEE_RATE,
    REALISTIC_FEE_RATE,
    legacy_cost_profile,
    realistic_cost_profile,
)


def test_legacy_profile_matches_audit_defaults() -> None:
    profile = legacy_cost_profile()
    assert profile.fee_rate == LEGACY_FEE_RATE == 0.001
    assert profile.slippage_pct == 0.001
    assert profile.apply_global_trend_filter is True
    assert profile.funding_cadence == "per_bar"
    assert profile.round_trip_cost_pct == 0.4


def test_realistic_profile_matches_brief() -> None:
    profile = realistic_cost_profile()
    assert profile.fee_rate == REALISTIC_FEE_RATE == 0.0004
    assert profile.slippage_pct == 0.0002
    assert profile.apply_global_trend_filter is False
    assert profile.funding_cadence == "scaled_8h"
    assert profile.round_trip_cost_pct == pytest.approx(0.12)


def test_funding_scale_1h_is_one_eighth_per_bar() -> None:
    profile = realistic_cost_profile()
    assert profile.effective_futures_funding_rate("1h") == 0.0001 * (1.0 / 8.0)


def test_funding_scale_4h_is_half_per_bar() -> None:
    profile = realistic_cost_profile()
    assert profile.effective_futures_funding_rate("4h") == 0.0001 * 0.5


def test_frozen_lane_registry_has_preregistered_ids() -> None:
    from scripts.run_cost_realism_rerun import FROZEN_LANES

    lane_ids = {lane.lane_id for lane in FROZEN_LANES}
    assert "daily-trend-long-btc" in lane_ids
    assert "eth-4h-range-reversion-bounded" in lane_ids
    assert "sol-1h-dislocation-event" in lane_ids
    assert len(FROZEN_LANES) == 5
