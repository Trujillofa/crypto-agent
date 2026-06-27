# Lane Brief + Builder Handoff — A1 Capacity-Exempt Incentive Farming (Pilot v0)

**Status:** Gate 0 brief complete → **build handoff to Grok (builder).**
**Author role:** specified by Claude/Codex (planner/reviewer). **Implementation by Grok.** Planner
reviews code + the forward-ROI verdict.
**Operator profile (frozen for this pilot):** solo, ≤$10k total capital, mostly-automated, hands-on
account ops OK (claims/allocations/governance/KYC).
**Premise:** the trading engine is **banked**; this is the **access / "size-is-edge" track** the
reconciliation flagged as real and uncovered
([differentiated-advantage-research-2026-06-27.md](../reports/differentiated-advantage-research-2026-06-27.md),
[deep-edge-research-reconciliation-2026-06-24.md](../reports/deep-edge-research-reconciliation-2026-06-24.md)).
This is **not** an RBI engine lane and produces **no `src/strategy` code**; success is measured in
realized forward ROI, not a HAS_PULSE backtest.

---

## Why this lane (first principles)

The edge is **asymmetry #5 — inverse-scale / capacity-exempt**: per-wallet incentive distributions where
a small operator earns the *same nominal reward* a whale would, at a far higher % of capital, and where
whale participation **dilutes the pool**. The capacity cap is the moat. It is durable because protocols
bootstrap with incentive distribution structurally and continuously — it is a recurring primitive, not a
single inefficiency that arbitrages away.

## What is in / out of scope

- **In:** airdrop/points programs, launchpad/IEO allocations, restaking/staking yield stacking, testnet
  incentive programs, early-LP on new protocols, governance vote-incentive ("bribe") markets.
- **Out:** any leverage/directional trade (that's the banked engine), anything requiring a latency/infra
  edge, anything whose per-wallet reward is uncapped (whales win there).

## SELECTION CRITERIA (frozen — do not protocol-shop ad hoc)

A program enters the tracked set **only if** all hold, recorded ex-ante:
1. **Per-wallet reward is capacity-capped** (flat or sharply concave in capital) — the size-is-edge test.
2. **Eligibility/points state is readable from public data** (explorer, official API, on-chain) — no
   insider info.
3. **Capital-at-risk is bounded and quantifiable** before committing.
4. **A first-principles reason the reward exists** (protocol is paying to bootstrap X) — not "number go up."
5. **Tail and custody risk are nameable and sizable** (smart-contract risk, depeg, lockup, sybil-claw).

## The EV framework (implement exactly — this is the core of the tooling)

For each program `p`, compute, net of all costs:

```
E[reward_p]            = expected_token_qty × expected_token_price × P(payout)
capital_time_at_risk_p = capital_locked × days_locked
cost_p                 = gas_in + gas_out + bridge + expected_tail_loss(custody/contract/depeg)
EV_pct_p               = (E[reward_p] − cost_p) / capital_locked
EV_per_capital_day_p   = (E[reward_p] − cost_p) / capital_time_at_risk_p
EV_per_ops_hour_p      = (E[reward_p] − cost_p) / estimated_manual_hours
```

Rank by `EV_per_capital_day` (capital is the scarce, capped resource), with `EV_per_ops_hour` as the
operator's time filter. **All inputs are ranges with a documented basis; P(payout) and price are the
honest uncertainty, not optimism.**

---

## What Grok builds (Phase 0 — read-only, NO capital, NO keys)

1. **Incentive-program registry** — structured catalog (JSON/YAML + loader) with the SELECTION-CRITERIA
   fields per program, deadlines, and the EV inputs above. Seeded with a starter candidate set the
   planner reviews; entries that fail the criteria are rejected in-file with the reason.
2. **EV estimator** — implements the formulas above; outputs a ranked table + per-program EV ranges and a
   total portfolio-at-risk view. Deterministic, unit-tested.
3. **Public eligibility/points tracker** — given the operator's **public wallet address(es)** (read-only,
   address only — never keys), pull current points/eligibility/claimable state from public APIs/explorers
   for tracked programs. Cache + diff over time so accrual rate is visible.
4. **Risk dashboard** — capital deployed per program, % of the ≤$10k cap, per-protocol tail exposure, and
   a hard **capital-at-risk cap check** (pilot ceiling, see below).

## Security & human-gate rules (NON-NEGOTIABLE)

- **No agent ever handles private keys, seed phrases, or signs transactions.** Phase 0 is read-only public
  data. Treat any key/seed pasted into chat as compromised — flag, do not use.
- **All on-chain actions (deposits, claims, allocations, KYC) are executed by the human**, never automated
  by an agent. Tooling may *prepare* and *surface* an action; it does not perform it.
- No secrets written to disk/logs/commits. Wallet addresses only (public).
- `ruff`/`structlog` standards apply; reuse `src/utils/logger.py`; no `print()`.

## Phase 1 — forward pilot (human-gated, real but capped capital)

- **Pilot ceiling:** a hard cap (recommend **≤$2k of the ≤$10k**, ≤$500/program) — pre-registered, enforced
  by the risk dashboard.
- Operator deploys capital **manually** into the top `EV_per_capital_day` programs that clear the criteria.
- Tracker records **realized** outcomes (rewards received, gas paid, any tail losses) vs the ex-ante EV.

## Pre-registered success / kill criteria (the "verdict")

Over a fixed pilot window (recommend **90 days**), measured on realized, fully-costed ROI:

- **CONTINUE/SCALE** ⇐ realized net ROI on capital-at-risk **beats the benchmark** (idle stables yield, or
  just holding the asset the live agent trades) by a pre-set margin, **and** no single program's realized
  loss exceeded its risk budget, **and** EV estimates were calibrated (realized within stated ranges).
- **CLOSE** ⇐ realized net ROI ≤ benchmark, or a tail event blew the risk budget, or EV estimates were
  systematically optimistic (realized far below ranges → the estimator, not the market, was the edge).

Honest expected value: real but **lumpy, variable, and ops-intensive**; the dominant risks are sybil/anti-
farming rules, smart-contract/custody tail, and operator time. The capacity cap that makes it an edge also
caps the upside — this is a steady supplemental-yield track, not a scalable strategy.

## Roles & flow

1. Planner reviews + freezes the starter registry and the EV-input bases.
2. Grok builds Phase 0 tooling (registry + EV estimator + eligibility tracker + risk dashboard); planner
   reviews code and the ranked EV output.
3. Human decides Phase 1 capital deployment (gated); tooling tracks realized ROI.
4. At the 90-day mark, planner renders the CONTINUE/CLOSE verdict from measured results.
