"""ev.py — scenario EV calculator (pure, deterministic).

Exact formula from parent spec + handoff:

Net_EV =
    P(eligibility) * P(distribution) * E[reward_qty]
      * conservative_realizable_price * liquidity_vesting_haircut
  + contractual_base_yield
  - gas_and_bridge_fees
  - opportunity_cost(capital * days, vs benchmark_apy)
  - expected_loss_reserve
  - manual_labor_cost(hours * hourly_rate)

Base case: if reward_type=speculative_points AND not reward_announced -> speculative component = 0.
Returns net_ev, net_ev_per_capital_day, net_per_manual_hour.
"""

from __future__ import annotations

from .types import EVScenarioInputs, RewardType


def _opportunity_cost(capital: float, days: float, benchmark_apy: float) -> float:
    # Simple linear: capital * (benchmark_apy * days/365)
    if days <= 0:
        return 0.0
    return capital * benchmark_apy * (days / 365.0)


def compute_ev(
    inputs: EVScenarioInputs, *, reward_type: RewardType | None = None
) -> dict[str, float]:
    """Compute net EV and ranking metrics. reward_type used for base-case zeroing."""
    spec_component = (
        inputs.p_eligibility
        * inputs.p_distribution
        * inputs.reward_qty
        * inputs.realizable_price
        * inputs.liquidity_vesting_haircut
    )

    # Base case rule
    if reward_type == RewardType.SPECULATIVE_POINTS and not inputs.reward_announced:
        spec_component = 0.0

    net_ev = (
        spec_component
        + inputs.base_yield
        - inputs.gas_bridge_fees
        - _opportunity_cost(inputs.capital, inputs.days, inputs.benchmark_apy)
        - inputs.expected_loss_reserve
        - (inputs.manual_hours * inputs.hourly_rate)
    )

    denom_cap_day = max(inputs.capital * max(inputs.days, 1e-9), 1e-9)
    net_per_cap_day = net_ev / denom_cap_day

    denom_hours = max(inputs.manual_hours, 1e-9)
    net_per_hour = net_ev / denom_hours

    return {
        "net_ev": net_ev,
        "net_ev_per_capital_day": net_per_cap_day,
        "net_per_manual_hour": net_per_hour,
    }


def base_and_upside(
    base_inputs: EVScenarioInputs,
    upside_inputs: EVScenarioInputs | None = None,
    *,
    reward_type: RewardType,
) -> dict[str, dict[str, float]]:
    """Return {'base': ..., 'upside': ...} . If upside None, copy base but force announced=True for upside."""
    b = compute_ev(base_inputs, reward_type=reward_type)
    if upside_inputs is None:
        # construct synthetic upside that enables the speculative part
        up = EVScenarioInputs(
            p_eligibility=base_inputs.p_eligibility,
            p_distribution=base_inputs.p_distribution,
            reward_qty=base_inputs.reward_qty,
            realizable_price=base_inputs.realizable_price,
            liquidity_vesting_haircut=base_inputs.liquidity_vesting_haircut,
            base_yield=base_inputs.base_yield,
            gas_bridge_fees=base_inputs.gas_bridge_fees,
            capital=base_inputs.capital,
            days=base_inputs.days,
            benchmark_apy=base_inputs.benchmark_apy,
            expected_loss_reserve=base_inputs.expected_loss_reserve,
            manual_hours=base_inputs.manual_hours,
            hourly_rate=base_inputs.hourly_rate,
            reward_announced=True,
        )
        u = compute_ev(up, reward_type=reward_type)
    else:
        u = compute_ev(upside_inputs, reward_type=reward_type)
    return {"base": b, "upside": u}
