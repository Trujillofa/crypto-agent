# Day-30 Portfolio Review — 2026-08-06 (template, pre-drafted 2026-07-21)

**Status:** DRAFT — NFP row refreshed 2026-08-13; awaiting human ratification
**Purpose:** close the 30-day evidence portfolio (2026-07-07 → ~2026-08-06) with a
15-minute ratification. All track verdicts are already determinate; blanks below are
for anything that changes between drafting and review day.

## Track outcomes (refreshed 2026-08-13; originally drafted 2026-07-21)

| Track | Verdict | Evidence |
|-------|---------|----------|
| Edge probe #1 (NFP OOS) | **YES** (2026-07-16) | `NFP_PREREG.md`, PR #154 — 21 trades, +0.70%/trade net, PF 2.18, all LOO positive |
| Edge probe #2 (fee-marginal) | **DELETED_NOT_NAMED** (2026-07-09) | `FEE_MARGINAL_PREREG.md` — no eligible family; budget slot consumed, not replaced |
| A1 incentive farming | **CLOSED, no Scale** (2026-07-14) | `A1_THRESHOLD_LOCK.md` — Legion = weekly watch only; galxe → controls; tick timer disabled |
| cTrader FX (external) | _[fill at review: challenge status vs `CTRADER_EXTERNAL_GATE.md`]_ | external repo |
| NFP forward gate | **SIGNED, in force** (2026-07-21) | `NFP_FORWARD_GATE.md`, PR #155 — first print 2026-08-07 = `MISSED_CAPTURE` (1/3); booked in `data/macro_events/nfp_good_news_forward.csv` (commit `6860d13`). Next print 2026-09-04. |

## Kill-gate check

`PORTFOLIO_KILL_GATE.md` triggers only if *all four* tracks come back empty. Probe #1
returned YES → **the kill gate does not fire.** No fallback decision is required.

## Post-window allocation (proposed for ratification)

1. **cTrader** keeps all active agent-hours: exit-path deterministic replay first
   (session-independent), then the ≥5 live executions per exit path the external gate
   requires.
2. **NFP forward gate** runs on autopilot: one capture ritual per print
   (`docs/specs/nfp-forward-capture-routine.md`), append-only CSV, no other work until
   the 7-print interim check.
3. **A1** stays on the weekly Legion watcher. No new programs.
4. **Research bucket stays closed.** No new probes, no lane #16. Reopening requires a
   named changed input + explicit human decision, per `README.md`.

## Ratification

- [ ] Track table verified current
- [ ] Allocation 1–4 accepted (or amendments noted below)

Amendments: _[none]_

Ratified by: _[unsigned]_
