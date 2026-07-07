# Probe #2: Fee-marginal retest post-#94/#140

**Status:** NOT ADMITTED
**Budget position:** the second and final allowed edge probe in the 30-day evidence
portfolio (see `README.md` and `NFP_PREREG.md`). There is no third probe.
**Date pre-registered:** 2026-07-07

**"Fee-marginal retest" is a category, not a probe.** Probe #2 is only admitted after
the exact strategy/family name is filled in below. If the family cannot be named, this
probe is **deleted from the budget, not postponed**.

---

## Admission status

Not admitted until exact family is named.

## Family

[Exact strategy/family name required. Must identify a specific family previously
rejected under the broken cost model or config-deaf trend filter. If left blank at
decision time, verdict = DELETED_NOT_NAMED.]

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

Verdict: _[pending — probe not admitted]_
