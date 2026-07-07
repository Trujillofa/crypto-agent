# Probe #1: NFP good-news-is-good OOS

**Status:** PRE-REGISTERED, not yet run
**Budget position:** one of only **two** allowed edge probes in the 30-day evidence
portfolio (see `README.md` and `FEE_MARGINAL_PREREG.md`). There is no third probe.
**Date pre-registered:** 2026-07-07

Background: the macro/news family is CLOSED (calendar NO_PULSE + surprise WEAK_EDGE;
see `docs/reports/macro-surprise-drift-probe-v0.md`). The NFP good-news-is-good effect
was flagged during that closure as an **OOS-only hypothesis** — the single pre-agreed
exception. This probe tests exactly that hypothesis, once, on out-of-sample data, with
the rules below fixed before any data is viewed.

---

## Hypothesis

[Placeholder: exact market behavior expected after NFP — e.g. direction and persistence
of the move in the chosen instrument following a positive NFP surprise. Must be filled
in before the probe runs.]

## Market/instrument

[Placeholder: exact instrument, venue, and timeframe.]

## Event window

[Placeholder: exact NFP release dates or release windows in scope, including the OOS
date range boundaries.]

## Entry rule

[Placeholder: deterministic entry condition and timing relative to the release.]

## Exit rule

[Placeholder: deterministic exit condition — time-based, level-based, or both.]

## Data source

[Placeholder: OHLCV/tick source and the NFP surprise data source (consensus vs actual).]

## Cost assumptions

[Placeholder: spread, commission, and slippage — fixed before running.]

## Pass threshold

- Net expectancy > 0 after all costs
- Profit factor ≥ 1.10
- Drawdown acceptable under intended risk model
- Result not dependent on one outlier event

## Kill criterion

If expectancy ≤ 0 after costs, or if the result requires parameter changes after viewing
the data, verdict = **NO_PULSE**.

## Scope limit

- One read-only script
- No optimization
- No feature expansion
- No second event thesis
- No lane expansion

## Verdict

**YES** or **NO_PULSE**

Verdict: _[pending]_
