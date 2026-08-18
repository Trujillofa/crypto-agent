# Day-30 Portfolio Review — 2026-08-06

**Status:** READY FOR RATIFICATION — unsigned
**Purpose:** close the 30-day evidence portfolio (2026-07-07 → ~2026-08-06) with a
15-minute human ratification. Pre-drafted 2026-07-21; track blanks filled 2026-08-05
and refreshed 2026-08-18. **Does not:** trade, re-arm agents, open probes, or change
locked thresholds.

---

## Track outcomes (verified 2026-08-18)

| Track | Verdict | Evidence |
|-------|---------|----------|
| Edge probe #1 (NFP OOS) | **YES** (2026-07-16) | `NFP_PREREG.md`, PR #154 — 21 trades, +0.70%/trade net, PF 2.18, all LOO positive |
| Edge probe #2 (fee-marginal) | **DELETED_NOT_NAMED** (2026-07-09) | `FEE_MARGINAL_PREREG.md` — no eligible family; budget slot consumed, not replaced |
| A1 incentive farming | **CLOSED, no Scale** (2026-07-14) | `A1_THRESHOLD_LOCK.md` — Legion = weekly watch only; galxe → controls; tick timer disabled |
| cTrader FX (external) | **IN PROGRESS — not gate-closed** | See §cTrader snapshot. Boundary + measurements in `CTRADER_EXTERNAL_GATE.md` (through 2026-07-28). Challenge-ready criteria **not met**. |
| NFP forward gate | **SIGNED, in force** (2026-07-21) | `NFP_FORWARD_GATE.md`, PR #155. First print 2026-08-07 = `MISSED_CAPTURE` (**1/3**); booked in `data/macro_events/nfp_good_news_forward.csv` (`6860d13`). Next print **2026-09-04**. Pre-flight 2026-08-18: BLS + Investing confirm Sep 4 08:30 ET / 12:30 UTC; Wayback SPN still `ECONNRESET`; use `--snapshot-url` if SPN fails. |

### cTrader snapshot (external; for this table only)

Ownership stays in `ctrader-trading-agent`. This is a day-30 readout, not a close of
the external gate.

| Item | State |
|------|--------|
| Prod pin | `6707d70` / `v2026-08-11-ops-guard-hardening-stable` (read 2026-08-18) |
| FundedHive | `peak_equity=5000`, `target_reached=false`, `profitable_days_count=8`; state mtime **2026-08-13T03:05Z**, `daily_pnl_r=+0.70` that day |
| Exit paths (gate restatement 07-28) | `partial_tp` **ready**; `stale_exit` **ready**; `time_stop` needs **2** more execs; `weekend_flatten` needs **4**; `trailing_stop` **broker-side** |
| Challenge-ready | **Not met** — funded challenge is still not QA |

---

## Kill-gate check

`PORTFOLIO_KILL_GATE.md` triggers only if *all four* of these are empty/failed:

| Condition | Result at day 30 |
|-----------|------------------|
| cTrader not validated | Still open (in progress) — **not** a total empty track |
| A1 measured poorly / no Scale | **CLOSED, no Scale** |
| NFP probe NO_PULSE | **NO** — probe returned **YES** |
| Fee-marginal NO_PULSE / DELETED_NOT_NAMED | **DELETED_NOT_NAMED** |

Because probe #1 returned **YES**, the portfolio kill gate **does not fire.**
Research-bucket rules in `README.md` still apply independently.

---

## Post-window allocation (proposed for ratification)

1. **cTrader (primary agent-hours, external repo only)**
   - Accrue remaining paper/demo executions: **2 × `time_stop`**, **4 × `weekend_flatten`**.
   - Resolve `time_stop` vs `stale_exit` same-bar ordering in the external repo.
   - **Do not** treat the FundedHive challenge as QA for unproven exit paths.
   - **Do not** declare challenge-ready until remaining paths and ops criteria clear.

2. **NFP forward gate (measurement only)**
   - One capture ritual per print (`scripts/nfp_forward_capture.py` /
     `docs/specs/nfp-forward-capture-routine.md`).
   - Append-only `data/macro_events/nfp_good_news_forward.csv`.
   - No trading, no paper agent, no capital from this gate.
   - No other NFP work until the **7-print interim** check.
   - Next pre: **2026-09-03 12:32 UTC** (`research/nfp_forward/run_pre_2026-09-04.sh`).
     If Wayback SPN fails, freeze consensus via archive.ph / Wayback UI and
     `--snapshot-url`. Do not fire `pre` before the window. Do not spend `miss`
     lightly (budget already **1/3**).

3. **A1**
   - Weekly Legion watcher only.
   - No new incentive programs without economic profit after **$25/hr** operator cost.

4. **Research bucket stays closed**
   - No new probes, no lane #16.
   - Gate 4b is diagnostic-only, **NOT DISCRIMINATING**, threshold `0.0` — do not
     retune or reopen to rescue it.
   - HYP-HTFR-001 / meta-allocator **CLOSED NO_PULSE**.
   - Public OHLCV structural program remains terminal (2026-06-19 / 06-23 / 06-24).
   - Reopening any closed surface requires a **named changed input** + explicit human
     decision per `README.md`.

5. **crypto-agent production (operational, not a research track)**
   - All strategy services remain **paper** until a pre-registered WFO (or successor
     gate) justifies re-arm.
   - No re-arm of SOL overlay / sentiment-macro from this review.
   - Sentiment-macro records on **DeepSeek** (`ai.provider: deepseek`, #177). Do not
     flip back to xAI without funded credits and a separate Deploy.

---

## Amendments vs 2026-07-21 template

| # | Amendment |
|---|-----------|
| A1 | Filled cTrader row: **IN PROGRESS**, not blank. |
| A2 | NFP forward first print is `MISSED_CAPTURE` (1/3); next print 2026-09-04. |
| A3 | Allocation item 2 names the Sep-3 pre time and Wayback SPN fallback. |
| A4 | Research closed list includes Gate 4b NOT DISCRIMINATING and HTFR NO_PULSE. |
| A5 | Item 5: crypto-agent stays paper; sentiment-macro on DeepSeek. |
| A6 | Kill-gate outcome unchanged (does not fire). |

---

## Ratification checklist

- [x] Track table verified against gate files + 2026-08-18 readout *(agent draft)*
- [x] Kill gate evaluated: **does not fire** *(agent draft)*
- [x] Allocation 1–5 drafted with amendments A1–A6 *(agent draft)*
- [ ] **Human:** track table accepted as written (or edits noted below)
- [ ] **Human:** allocation 1–5 accepted (or amendments noted below)
- [ ] **Human:** signature

Human amendments: _[none yet]_

| Field | Value |
|-------|-------|
| Ratified by | _[unsigned — human only]_ |
| Date | _[YYYY-MM-DD]_ |
| Notes | _[optional]_ |

---

## Immediate follow-through after signature (not part of the vote)

1. NFP `pre` at **2026-09-03 12:32 UTC**; `post` after the 2026-09-04 BLS print.
2. In `ctrader-trading-agent` only: remaining exit-path accrual.
3. Optional: phantom-ticket baseline in the external repo.

---

*Do not edit locked gates (`NFP_PREREG`, `NFP_FORWARD_GATE`, `PORTFOLIO_KILL_GATE`,
`A1_THRESHOLD_LOCK`) to “improve” this review — that invalidates those decisions.*
