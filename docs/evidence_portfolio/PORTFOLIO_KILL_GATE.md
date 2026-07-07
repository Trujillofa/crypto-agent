# Portfolio Kill Gate — Day-30 Fallback

**Status:** LOCKED
**Portfolio start:** 2026-07-07
**Day-30 review:** on or about 2026-08-06
**Date locked:** 2026-07-07

This gate defines what happens if the entire 30-day evidence portfolio comes back
empty. It is written now, before results, so the day-30 decision cannot be
rationalized into "one more lane."

---

## Trigger

If by day 30:

- cTrader is not validated (fails `CTRADER_EXTERNAL_GATE.md`)
- A1 measured poorly (no program classifies as Scale under `A1_THRESHOLD_LOCK.md`)
- NFP probe returns NO_PULSE (`NFP_PREREG.md`)
- Fee-marginal retest returns NO_PULSE or DELETED_NOT_NAMED (`FEE_MARGINAL_PREREG.md`)

## Then

**No new public-data trading lane opens.**

## Fallback

- **Input acquisition**: better data, venue access, rebates, lower fees, capital,
  proprietary flow, or execution advantage — change what goes into the process, not the
  process itself
- Or redeploy agent-hours to domains with more controllable ROI
- **Do not open lane #16**

Input acquisition is the fallback, not a disguised lane #16: it acquires a
differentiated input first and only then re-evaluates, consistent with the structural
conclusion of the program (`docs/reports/research-consolidation-2026-06-19.md`,
different-universe closure): no edge exists on public data without a differentiated
advantage.
