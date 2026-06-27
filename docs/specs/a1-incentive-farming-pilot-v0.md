# Lane Brief + Builder Handoff — A1 Capped / Fixed-Reward Incentive Operations (Pilot v0)

**Status:** Gate 0 brief (v0.2, narrowed on review) → **build handoff to Grok (builder).**
**Author role:** specified by Claude/Codex (planner/reviewer). **Implementation by Grok.** Planner
reviews code + the forward-ROI verdict.
**Operator profile (frozen):** solo, **one legitimate identity**, ≤$10k total capital, mostly-automated,
hands-on account ops OK (claims/allocations/governance/KYC).
**Premise:** the trading engine is **banked**; this is the **access / "size-is-edge" track** — narrowed
on review to its only defensible form. Backing analysis:
[differentiated-advantage-research-2026-06-27.md](../reports/differentiated-advantage-research-2026-06-27.md),
[deep-edge-research-reconciliation-2026-06-24.md](../reports/deep-edge-research-reconciliation-2026-06-24.md).
This is **not** an RBI engine lane and produces **no `src/strategy` code**; success is realized forward
ROI, not a HAS_PULSE backtest.

> **v0.2 narrowing (review-driven).** v0 defined A1 too broadly — it lumped five economically different
> activities and overstated both the "smallness edge" and "durable." The inverse-scale edge is real
> **only where a per-identity/fixed cap is enforceable**, so a whale cannot capture proportionally more
> with size. Pro-rata pools (reward ∝ capital×time) are yield enhancement, not an edge. This pilot is
> therefore **capped/fixed-reward operations only.**

---

## Why this lane (first principles) — narrowed

Asymmetry #5 (inverse-scale) holds **only when the distribution has an enforceable per-person or fixed
cap.** "Whales dilute by participating" is true for capped distributions and **false for ordinary
pro-rata reward pools.** The durable capability is **disciplined discovery + operations + accounting** —
no individual program is durable (terms change retroactively, points may never convert, formulas are
undisclosed, sybil filters claw back, value can collapse pre-liquidity, competition dilutes future rounds).

## Scope — IN / DEFER / OUT (frozen for the pilot)

| Tier | Activity | Pilot |
|---|---|---|
| **IN (core)** | Fixed / per-identity-capped allocations with **published limits** (KYC/account-based launch allocations; documented fixed task rewards) | ✅ |
| **IN (conditional)** | Testnet / contribution tasks — *labor*-driven, not capital | ⚠️ only if legit, anti-sybil-clean, gas+labor capped, value treated as zero until announced |
| **YIELD-ONLY** | Launchpool on an asset you would hold **anyway** | ⚠️ as yield enhancement only — **never buy a volatile token solely to farm** |
| **DEFER** | Restaking / proportional points | ⛔ excluded v0 — test: *"would I hold this on cash yield + risk alone if points = 0?"* If no, it's speculative farming |
| **OUT (reject)** | Early-token LP, governance vote-incentive ("bribe") markets, any leveraged/recursive points loop | ❌ a different MM/yield-risk business (IL, inventory, adverse selection, contract/bridge risk) |

## SELECTION CRITERIA (a program enters the tracked set only if ALL hold, recorded ex-ante)

1. **Reward is fixed or per-identity-capped** (not pro-rata to capital) — the enforceable-cap test.
2. **Terms are officially documented** (source URL + snapshot); distribution mechanism is disclosed.
3. **Eligibility/points state readable from public data** (explorer/official API) — no insider info.
4. **Capital-at-risk and lockup are bounded and quantifiable** before committing.
5. **Tail + custody + sybil-policy named** (contract/chain, jurisdiction/KYC limits, anti-farming rules).
6. **A first-principles reason the reward exists** (protocol pays to bootstrap X).

Reject most candidates here. Anything failing a criterion is recorded in-file with the reason.

---

## The EV framework (implement exactly)

For each candidate `p`, net EV in dollars:

```
Net_EV_p =
    P(eligibility) × P(distribution) × E[reward_qty]
      × conservative_realizable_price × liquidity_vesting_haircut    # speculative reward component
  + contractual_base_yield                                           # the part that pays even if reward = 0
  − gas_and_bridge_fees
  − opportunity_cost(capital × days, vs benchmark)
  − expected_loss_reserve(contract/custody/depeg)
  − manual_labor_cost(hours × $/hr)
```

Track **both**: `Net_EV / capital_day` and `Net_$ / manual_hour`.
**Base case: if no reward has been officially announced, the speculative component = 0.** Keep an upside
scenario separately; never let unannounced points drive a go decision.

---

## What Grok builds — Phase 0 (read-only; NO wallets, NO capital, NO keys)

> Phase 0 is a **2-week paper inventory of 15–20 opportunities** with no wallet connected and no capital
> deployed. Automate **observation and accounting, never custody.**

1. **Program registry** — sourced from **official pages** (URL + terms snapshot/version); one record per
   program with the SELECTION-CRITERIA fields, distribution mechanism, fixed/capped-vs-pro-rata flag,
   KYC/jurisdiction limits, sybil policy, capital+time required, contracts/chains, claim date + vesting,
   announced-reward-vs-speculative-points, est. fees + manual hours, exit liquidity.
2. **Scenario EV calculator** — implements the formula above; ranks by `Net_EV/capital_day`; surfaces
   `Net_$/manual_hour`; base/upside scenarios with unannounced reward = 0 in base. Deterministic, unit-tested.
3. **Deadline + claim/expiry monitor** — alerts on windows closing, claim dates, vesting unlocks.
4. **Read-only eligibility/points tracker** — given the operator's **public wallet address(es)** (address
   only, never keys), pull current eligibility/points/allocation history from public APIs/explorers; cache
   + diff so accrual is visible.
5. **Accounting** — capital-at-risk dashboard (% of caps), gas/fee ledger, realized-P&L + hours-worked report.

**Do NOT build a "restaking optimizer" or any auto-deploy/auto-claim.** That automates the riskiest,
least-differentiated part before the operating process is proven.

## Security & human-gate rules (NON-NEGOTIABLE)

- **No agent ever handles private keys, seed phrases, or signs/sends transactions.** Treat any key/seed
  pasted into chat as compromised — flag, do not use.
- **Kept manual (human only):** wallet connection, contract approval, deposits/withdrawals, claims, KYC,
  governance votes, **final program acceptance**.
- **Automated (agent):** discovery, registry, eligibility *monitoring*, deadlines, accounting, EV measurement.
- One identity only; **no multi-wallet / sybil activity.** No secrets to disk/logs/commits (addresses only).
- `ruff`/`structlog` standards; reuse `src/utils/logger.py`; no `print()`.

## Phase 1 — capped live pilot (human-gated). $10k is a CEILING RESERVE, not the budget.

- **Total deployed ≤ $1,000; per protocol ≤ $250; ≤ 3 simultaneous programs.**
- One identity; **no sybil, no leverage, no borrowing, no unaudited bridges, no early-token LP, no auto
  transaction signing, no position whose economics require an unannounced token.**
- Operator deploys manually into the top `Net_EV/capital_day` programs that clear all criteria; tooling
  records realized rewards, gas, and any tail losses vs ex-ante EV. The remaining ~$9k stays in reserve.

## Promotion / kill gates (the verdict, after a fixed window — recommend 90 days)

**CONTINUE/SCALE** only if all hold:
- Every transaction + cost captured; no eligibility/terms violations.
- Realized rewards exceed **all** direct costs **and** labor.
- Results beat a passive cash/stablecoin benchmark **after a substantial risk haircut**.
- Returns are **not dependent on one lucky allocation**.
- Capital exited reliably; EV estimates were calibrated (realized within stated ranges).

**STOP immediately** if any:
- A program encourages sybil behavior, or reward rules stay materially undisclosed.
- It requires leverage or recursive points loops, or contract exposure can't be bounded.
- Expected return depends mainly on an assumed token valuation.
- `Net_$/manual_hour` falls below an acceptable rate.

## Roles & flow

1. Planner freezes the starter registry + EV-input bases and the IN/DEFER/OUT scope.
2. Grok builds Phase 0 (registry + EV calculator + deadline/eligibility monitors + accounting); planner
   reviews code and the ranked paper inventory.
3. Human decides Phase 1 deployment within the caps (gated); tooling tracks realized ROI.
4. At 90 days, planner renders CONTINUE/CLOSE from measured results.

## A2 status

**A2 (maker-rebate) stays CLOSED** under the current ≤$10k solo posture — its per-trade dollar-edge is
below operating cost at this size; it reopens only on a deliberate scale-up to a market-making business.
