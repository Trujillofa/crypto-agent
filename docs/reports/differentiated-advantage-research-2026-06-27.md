# Differentiated-Advantage Research — 2026-06-27

**Role:** planner/reviewer (Claude/Codex). **Status:** strategy research, not an engine lane.
**Premise:** the trading-engine program is **banked terminal** — public, liquid, aggregate data is
efficiently priced; *no edge exists without a differentiated advantage* (capstone
[research-consolidation-2026-06-23.md](./research-consolidation-2026-06-23.md), reconciliation
[deep-edge-research-reconciliation-2026-06-24.md](./deep-edge-research-reconciliation-2026-06-24.md),
Path-2 economics close [path2-gate0-economics-close-2026-06-24.md](./path2-gate0-economics-close-2026-06-24.md)).
This doc enumerates *what a differentiated advantage could actually be* and ranks the ones reachable
from the current operator profile.

---

## First principles — a checklist of edge asymmetries (framework, not theorem)

Durable edges tend to come from one or more of seven structural asymmetries. This is a **checklist for
where to look, not a proof** — the categories overlap (maker rebates are *access* and *cost structure*;
restaking reward is partly *constraint tolerance*; an airdrop is a protocol subsidy, not necessarily
trading alpha) and listing an activity under a category does **not** establish it has positive EV. The
bank tested **only #1 on liquid majors** and found it efficient; the others were never the object of study.

| # | Asymmetry | "I win because I…" |
|---|---|---|
| 1 | **Information** | know something others don't, or sooner |
| 2 | **Latency** | act on the same information faster |
| 3 | **Capital scale** | can deploy size others can't |
| 4 | **Access** | hold a privileged structural position (venue/relationship) |
| 5 | **Inverse-scale / capacity-exempt** | do what's *too small* for others — being small **is** the moat |
| 6 | **Cost structure** | pay less per unit of trading (rebates, fee tiers) |
| 7 | **Constraint tolerance** | hold what others *can't* (custody / illiquidity / regulatory) |

## Mapping each against the operator profile (solo, public data, ≤$10k, no infra/latency edge)

| # | Asymmetry | Reachable now? | Why |
|---|---|---|---|
| 1 | Information | ⚠️ narrow | Public aggregate data is priced (the bank's whole finding). Only survives on an *under-watched* public surface with a tiny audience — fragile, decays when noticed. |
| 2 | Latency | ❌ no | Requires co-location + infra + a different objective. Operator has no execution asymmetry. This is what kills "new-listing microstructure" (it's a sniping/latency war). |
| 3 | Capital scale | ❌ no | The operator is the *small* side by definition. |
| 4 | Access (MM / maker-rebate / designated LP) | ⚠️ C-tier | Real and durable, but a **business**: capital + infra + venue relationship. Inverts the spread (earn it instead of paying it). Reachable only on a deliberate scale-up. |
| 5 | **Inverse-scale / capacity-exempt** | ✅ **yes** | The one place small capital is a *strength*: incentive distributions, allocations, and per-wallet rewards that a whale **dilutes by participating**. Genuinely uncovered by the bank. |
| 6 | Cost structure | ⚠️ multiplier | Fee-tier/rebate/referral optimization is a margin multiplier on an existing flow, not a standalone edge. Meaningful only layered on #4 or #5. |
| 7 | Constraint tolerance | ⚠️ overlaps #5 | Much of the #5 reward is *compensation* for holding new/illiquid/risky assets others avoid. Edge and tail-risk are the same coin — must be sized as such. |

**Conclusion:** for this profile, #2/#3 are closed, #1/#6/#7 are weak-or-derivative, #4 is a deliberate
business, and **#5 is the profile-fit advantage — but only in its narrow, capped form** (see A1 below).
The durable capability is disciplined discovery + operations, not the reward source. This matches the
reconciliation's one substantive addition: the access/"size-is-edge" track is real, uncovered, and *not*
a trading-engine lane.

---

## Recommended advantages (ranked)

### ★ A1 — Capped / fixed-reward incentive operations (RECOMMENDED #1 — NARROWED)
**Asymmetry #5 (+#7), but only where a per-identity/per-account cap is *enforceable*.** The smallness
edge is real **only** when the distribution has an enforceable per-person or fixed cap, so a whale
*cannot* capture proportionally more by deploying size. For ordinary **pro-rata** pools (reward ∝
capital × time) this is false — that is yield enhancement, not an inverse-scale edge.

The original draft lumped five economically different activities; they are **not** the same edge:

| Activity | Genuine small-account edge? | Pilot disposition |
|---|---|---|
| Fixed / per-identity-capped allocations (documented limits, KYC/account caps) | **Yes** | **Core of pilot** |
| Testnet / contribution tasks | Sometimes — it's *labor*, not capital | Conditional (legit, anti-sybil-clean, capped labor) |
| Launchpool on an asset you'd hold anyway | No — pro-rata to capital×time | Yield enhancement only; never buy a token just to farm |
| Restaking / proportional points | Usually no — pro-rata, risk-compensated | **Defer** (test: would I hold it at points=0?) |
| Early LP / vote-incentive ("bribe") markets | No — it's a MM/yield-risk business (IL, inventory, contract/bridge risk) | **Reject from pilot** |

- **Durability — corrected:** the *category* recurs, but **no individual program is durable** (terms
  change retroactively, points may never convert, allocation formulas are undisclosed, sybil filters claw
  back, reward value can collapse pre-liquidity, competition dilutes future rounds). The durable
  capability is **disciplined discovery + operations + accounting**, not the reward source.
- **EV character:** real but lumpy; ROI realized forward in real (small) money, not backtestable.
  **Unannounced rewards are valued at zero in the base case** (upside tracked separately).
- **Kill risks:** sybil/anti-farming rules, smart-contract & custody risk, time-intensive ops, reward variance,
  and #7 tail (the asset you hold to qualify can crater). Must be sized and risk-budgeted explicitly.
- **Buildable by Grok (this is the bridge to "builder"):** eligibility/points trackers, allocation & claim
  monitors, an EV estimator per program (expected reward × probability ÷ capital-time-at-risk), restaking
  yield optimizer. The *"test"* is a forward, capped, measured-ROI pilot — not a HAS_PULSE backtest.

### ★ A2 — Maker-rebate / venue cost edge (RECOMMENDED #2, conditional)
**Asymmetry #4/#6.** Stop paying the spread; *earn* it. Inverts the exact sub-gate that killed Path-2
illiquid microstructure (spread-vs-edge).

- **Reachable only on a scale-up** to a small-business posture: sustained volume, capital, maker-tier or
  rebate relationship, latency-tolerant market-making infra.
- **Gate-able cheaply (planner work first):** a *paper economics gate* like the Path-2 close — estimate
  net rebate EV at the operator's realistic volume on named venues *before* any build or capital. If the
  rebate-minus-adverse-selection is ≤ operating cost, close it on paper.
- **Durable** if it clears the gate; this is how real prop crypto desks actually earn.

### A3 — Niche information on an under-watched public surface (CONDITIONAL #3)
**Asymmetry #1.** Only worth a Gate-0 if the operator can *name* a specific public surface with a
genuinely small audience and a first-principles reason it's under-watched (not "I'll watch Twitter
faster"). Low prior, decays on discovery. Default: do not open without a named surface.

### A4 — Latency / co-location (REJECT for this profile)
**Asymmetry #2.** Not reachable without becoming an infra business; explicitly what made new-listing
microstructure a C-tier latency war, not a retail lane.

---

## The decision variables (these change the ranking)

The "right" advantage is a function of three operator choices the planner cannot assume:

1. **Capital ceiling** — stay ≤$10k, or willing to deploy materially more? (Gates A2 in/out.)
2. **Posture** — solo + mostly-automated, or willing to run it as a small *business* (infra, capital, a
   different objective)? (A2/A4 require the latter.)
3. **Ops appetite** — willing to do account-level manual work (claims, allocations, governance, KYC), or
   strictly code-only? (A1 is ops-heavy by nature; pure-code-only shrinks it to its trackable subset.)

## Recommendation

- **Primary:** pursue **A1 (capacity-exempt incentive farming)** — it is the only advantage where this
  operator's smallness is an *edge*, it is durable, and it has concrete buildable tooling for Grok while
  staying out of the (sealed) RBI engine loop.
- **Secondary, only on a scale-up decision:** **A2 (maker-rebate)** — but planner runs a paper economics
  gate *before* any capital or build.
- **Hold:** A3 unless a specific surface is named; **reject** A4.
- **Unchanged:** the trading-engine stays banked. None of this reopens a backtest lane.

**Next step:** operator answers the three decision variables; planner then either (a) writes an A1 pilot
spec + buildable-tooling brief for Grok, or (b) runs the A2 paper economics gate.
