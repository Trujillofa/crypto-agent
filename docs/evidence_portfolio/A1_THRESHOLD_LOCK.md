# A1 Incentive-Farming Threshold Lock

**Status:** LOCKED (pre-registered before Phase-0 results are reviewed)
**Lock deadline:** thresholds must be finalized before **2026-07-10**
**Phase-0 close date:** **2026-07-11**
**Primary metric:** net yield per ops-hour
**Date locked:** 2026-07-07

This document fixes the A1 incentive-farming scale/fix/freeze/kill thresholds **before**
Phase-0 baseline data is reviewed. Thresholds may not be adjusted after 2026-07-10 or
after any Phase-0 result has been seen, whichever comes first. Post-hoc threshold
changes invalidate the decision and default the affected program to **Freeze**.

Related: `docs/specs/a1-incentive-farming-pilot-v0.md`,
`docs/specs/a1-phase0-tooling-handoff-v0.md`,
`docs/reports/a1-phase0-day0-2026-06-27.md`,
program registry `research/a1-incentive-farming/starter-registry-v0.yaml`,
freeze guard `scripts/check_incentive_ops_freeze.py`.

---

## Definitions

**Cash net profit** =
gross rewards
− gas
− bridge fees
− swap/slippage
− retry/failure costs
− capital lockup cost

**Economic profit** =
cash net profit
− ($25 × operator hours)

**Cash yield per ops-hour** =
cash net profit / total operator hours

**Phase-0 ROI** =
cash net profit / average capital deployed during the Phase-0 measurement window

### ROI period definition

Phase-0 ROI is measured over the Phase-0 baseline window ending 2026-07-11. It is a
**raw period return, not annualized**. Do **not** annualize ROI for the scale decision.
Annualized ROI may be shown as context only, clearly labeled, and carries no weight in
the classification below.

### Operator-time double-count fix

Operator hours are counted **once**. Economic profit already deducts operator time at
$25/hour; therefore cash yield per ops-hour is computed from **cash** net profit (before
the operator-time deduction), never from economic profit. Deducting operator time and
then also dividing by operator hours on the same figure double-counts the cost and is
prohibited. Shared operator time spent across multiple programs is allocated
proportionally, not billed in full to each program.

---

## Classification table (per program)

Each Phase-0 program is classified into exactly one bucket. All criteria in a bucket's
list are evaluated against the locked definitions above.

### Scale

- Economic profit > $0
- Phase-0 ROI > 1%
- Cash yield per ops-hour ≥ $25
- Failure rate ≤ 5%
- No unresolved operational risk

**Action:** scale cautiously.

### Fix

- Cash net profit > $0 but economic profit ≤ $0
- OR cash yield per ops-hour < $25
- OR failure rate between 5% and 15%
- OR process is too manual

**Action:** improve process and retest small.

### Freeze

- Economic profit between −$25 and +$25
- OR data quality is poor
- OR result depends on one abnormal reward

**Action:** do not scale.

### Kill

- Cash net profit < $0
- OR Phase-0 ROI negative
- OR failure rate > 15%
- OR reward is not repeatable

**Action:** stop.

---

## Hard rule

**No A1 program scales unless it is profitable after operator time** (economic profit >
$0). Cash-positive but economically negative programs are Fix at best, never Scale.

## PRs #132/#133 re-land rule

PRs #132 (allowlisted GET client) and #133 (address checksums) were merged during the
tooling freeze and reverted on 2026-07-06 (`ac43cec`, `4e106f0`). They **re-land only if
Phase-0 data classifies at least one program as Scale** — i.e. the tooling investment is
justified by evidence. They do **not** re-land merely because the freeze expires on
2026-07-11. If no program scales, both PRs stay reverted until a program earns Scale.
