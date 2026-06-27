"""ev.py golden + base case + validation tests (deterministic)."""

from __future__ import annotations

import pytest

from tools.incentive_ops.ev import compute_ev
from tools.incentive_ops.types import EVScenarioInputs, RewardType, ValidationError


def _mk(**kw):
    base = {
        "p_eligibility": 1.0,
        "p_distribution": 1.0,
        "reward_qty": 100.0,
        "realizable_price": 0.5,
        "liquidity_vesting_haircut": 1.0,
        "base_yield": 0.0,
        "gas_bridge_fees": 0.0,
        "capital": 100.0,
        "days": 10.0,
        "benchmark_apy": 0.0,
        "expected_loss_reserve": 0.0,
        "manual_hours": 0.0,
        "hourly_rate": 0.0,
        "reward_announced": False,
    }
    base.update(kw)
    return EVScenarioInputs(**base)


def test_base_unannounced_speculative_is_zero_spec_component():
    inp = _mk(reward_announced=False)
    ev = compute_ev(inp, reward_type=RewardType.SPECULATIVE_POINTS)
    # speculative part zeroed
    assert ev["net_ev"] == -0.0  # opportunity + etc zero


def test_announced_speculative_nonzero():
    inp = _mk(reward_announced=True)
    ev = compute_ev(inp, reward_type=RewardType.SPECULATIVE_POINTS)
    assert ev["net_ev"] > 0


def test_golden_values_deterministic():
    # chosen numbers produce known result
    inp = EVScenarioInputs(
        p_eligibility=0.8,
        p_distribution=0.75,
        reward_qty=120.0,
        realizable_price=1.0,
        liquidity_vesting_haircut=0.5,
        base_yield=10.0,
        gas_bridge_fees=2.0,
        capital=200.0,
        days=7.0,
        benchmark_apy=0.06,
        expected_loss_reserve=5.0,
        manual_hours=1.0,
        hourly_rate=40.0,
        reward_announced=True,
    )
    res = compute_ev(inp, reward_type=RewardType.ANNOUNCED_FIXED_TOKEN)
    # exact: 0.8*0.75*120*1*0.5 +10 -2 - (200*7/365*0.06) -5 -40
    assert abs(res["net_ev"] - (-1.2301369863013605)) < 1e-9
    assert res["net_ev_per_capital_day"] < 0
    assert "net_per_manual_hour" in res


def test_ev_construction_rejects_bad_ranges():
    with pytest.raises(ValidationError):
        _mk(p_eligibility=-0.1)
    with pytest.raises(ValidationError):
        _mk(gas_bridge_fees=-1)
