"""Tests for types: enums, Address validation (reject secrets), EV inputs validation."""

from __future__ import annotations

import pytest

from tools.incentive_ops.types import (
    Address,
    Criterion,
    EVScenarioInputs,
    PilotCaps,
    SuspectedSecretError,
    ValidationError,
)


def test_criterion_enum():
    assert Criterion.TRUE == "true"
    assert Criterion("maybe") == Criterion.MAYBE


def test_pilot_caps_defaults():
    c = PilotCaps()
    assert c.total_usd == 1000.0
    assert c.per_program_usd == 250.0
    assert c.max_concurrent == 3


def test_address_evm_ok():
    a = Address("0x0000000000000000000000000000000000000000")
    assert str(a) == "0x0000000000000000000000000000000000000000"


def test_address_rejects_privkey():
    with pytest.raises(SuspectedSecretError):
        Address("0x" + "a" * 64)
    with pytest.raises(SuspectedSecretError):
        Address("a" * 64)


def test_address_rejects_mnemonic_like():
    with pytest.raises(SuspectedSecretError):
        Address(
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        )


def test_ev_inputs_validation():
    with pytest.raises(ValidationError):
        EVScenarioInputs(
            p_eligibility=1.5,
            p_distribution=0.5,
            reward_qty=10,
            realizable_price=1,
            liquidity_vesting_haircut=1,
            base_yield=0,
            gas_bridge_fees=0,
            capital=100,
            days=10,
            benchmark_apy=0.05,
            expected_loss_reserve=0,
            manual_hours=1,
            hourly_rate=10,
            reward_announced=False,
        )
    # ok
    EVScenarioInputs(
        p_eligibility=0.9,
        p_distribution=0.8,
        reward_qty=50,
        realizable_price=2.0,
        liquidity_vesting_haircut=0.8,
        base_yield=1.0,
        gas_bridge_fees=2.0,
        capital=200,
        days=10,
        benchmark_apy=0.04,
        expected_loss_reserve=5,
        manual_hours=2,
        hourly_rate=30,
        reward_announced=True,
    )
