# Probe #2: Fee-marginal retest post-#94/#140

**Status:** CLOSED — DELETED_NOT_NAMED
**Budget position:** the second and final allowed edge probe in the 30-day evidence
portfolio (see `README.md` and `NFP_PREREG.md`). There is no third probe.
**Date pre-registered:** 2026-07-07

**"Fee-marginal retest" is a category, not a probe.** Probe #2 is only admitted after
the exact strategy/family name is filled in below. If the family cannot be named, this
probe is **deleted from the budget, not postponed**.

---

## Admission status

Not admitted until exact family is named.

**Recommendation (2026-07-07): DELETED_NOT_NAMED — no family qualifies.** Evidence
review of the fee-marginal category found that every candidate was **already retested
after #94 with the trend filter explicitly controlled in both states**, making #140 a
non-event for these families (it fixed the config path; the 2026-06-18 rescreens set
the filter directly and measured both cells):

- `sol-4h-rsi-reversal`, `avax-4h-bollinger-strategy`, `eth-4h-range-reversion-bounded`
  — closed-family cost-corrected rescreen (#97/#98), cells A (filter OFF) and B (filter
  ON), **all 6 cells FAIL** the standard gate
  (`research/closed-family-cost-rescreen/combined_results.json`,
  `docs/reports/closed-family-cost-corrected-rescreen-2026-06-18.md`: "mean-reversion
  family genuinely closed … the cost bug hid no deployable edge in fee-marginal /
  trend-filter-confounded families").
- `daily-trend-long` BTC/ETH/SOL and `sol-1h-dislocation-event` — cost-realism rerun,
  legacy and realistic passes, **all FAIL**
  (`research/cost-realism-rerun/combined_results.json`,
  `docs/reports/cost-realism-rerun-2026-06-18.md`).
- Dislocation cost isolation (#95): best cell still FAIL (WFO Sharpe 0.15 < 0.5,
  concentration 79% > 50%) — "do not re-open the dislocation/fee-marginal family"
  (`docs/reports/dislocation-cost-isolation-2026-06-18.md`).
- Program-level verdict: "no closed lane revives at corrected costs"
  (`docs/reports/research-consolidation-2026-06-19.md`).

Naming any of the above would re-run a completed experiment with no changed input —
exactly the relapse this document exists to block. Deletion stands unless the human
names a fee-marginal family **not covered by the 2026-06-18 rescreens**.

## Family

[Exact strategy/family name required. Must identify a specific family previously
rejected under the broken cost model or config-deaf trend filter, and NOT already
covered by the 2026-06-18 corrected-cost rescreens listed above. If left blank at
decision time (2026-07-10), verdict = DELETED_NOT_NAMED.]

## Thing that changed

- #94 corrected the cost model (fees/slippage/funding).
- #140 fixed the config-deaf trend filter (backtest now respects
  `global_trend_filter_enabled`, mirroring live).

See `docs/reports/` fee/cost tooling notes and PR #140 (`68e2300`).

## Hypothesis

This family may have been incorrectly rejected because cost/config handling was wrong.
Under corrected costs/config, it may show positive net expectancy.

## Dataset

[Fixed date range required — set before running, including the train/OOS split
boundary.]

## Costs

[Fixed spread, commission, and slippage assumptions required — set before running.]

## Pass threshold

- Net expectancy > 0 after all costs
- Profit factor ≥ 1.10
- Max drawdown acceptable under intended risk model
- Result survives fixed train/OOS split

## Kill criterion

If expectancy ≤ 0, or if the result only appears after parameter sweeping, verdict =
**NO_PULSE**.

## Scope limit

- One read-only retest
- No optimization sweep
- No new feature engineering
- No lane expansion

## Verdict

**YES**, **NO_PULSE**, or **DELETED_NOT_NAMED**

Verdict: **DELETED_NOT_NAMED** (2026-07-09). No eligible family was named before
the decision gate, and the documented candidates were already retested under corrected
costs. This consumes no replacement probe budget.
