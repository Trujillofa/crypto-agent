# cTrader FX External Validation Gate

**Status:** gate documented; validation in progress externally
**Date recorded:** 2026-07-07

## Boundary

The cTrader FX agent is **external** to `crypto-trading-agent`. This repository must
not modify cTrader source code. This document only records the validation gate and the
external follow-up checklist. All implementation, testing, and deployment work for the
cTrader agent happens in its own repository.

Context: the cTrader session-momentum FX agent is the forward vehicle (Track 1) —
live-validated +EV on a FundedHive prop challenge. Honest booking is proven on both a
win and a loss (2026-06-30, 2026-07-01), but every live close so far has been a broker
bracket fill caught by the reconciler; agent-initiated close paths (partial TP,
trailing) remain unexercised. This gate exists to close that hole before the funded
challenge carries real consequences.

---

## Challenge-ready acceptance criteria

- 0 challenge-rule violations
- 0 missing SL/TP events
- 0 duplicate orders
- 0 orphan positions
- No trade over allowed risk
- At least 30 closed forward trades or 10 live sessions, whichever comes later
- Validation drawdown below 50% of challenge allowance
- Positive net expectancy after spread, commissions, slippage, and failed fills
- All agent-managed exit paths passing deterministic replay
- At least 5 real-time executions per agent-managed exit path before that path is
  production-ready

## Exit paths to validate

- Partial TP
- Trailing stop
- Break-even move
- Forced close / emergency exit
- Broker SL handling
- Broker TP handling
- Reconciliation after fill

## Important rule

**The funded challenge is not QA.** Demo, paper, or micro-live testing can satisfy the
exit-path validation gate in parallel. No exit path is exercised for the first time on
challenge (or funded) capital.

---

## External follow-up ownership

Status checked on 2026-07-08. All cTrader execution, reconciliation, forward
validation, and funded-challenge gates are owned by the `ctrader-trading-agent`
repository. Do not implement, validate, or close those tasks in `crypto-agent`.

This file is only a boundary note so `crypto-agent` agents do not mistake the
cTrader gate for local work. The canonical checklist and evidence must live in
`ctrader-trading-agent`.

---

## Update 2026-07-27 — the "unexercised" premise is stale

Exit-path telemetry was built in `ctrader-trading-agent`
(PR #48, `feat/exit-path-gate-telemetry`): exits are now tagged at write time with
`execution` (live/paper) and `agent_initiated`, aggregated by
`PaperPnLLog.get_exit_path_stats()`, and reported by
`scripts/exit_path_gate_report.py`. Until this existed the gate was **unmeasurable**
without hand-grepping the P&L journal — nothing aggregated exit reasons.

Run against a read-only copy of the production journal (146 exits):

| path | live (tagged) | inferred live | paper | status |
|---|---:|---:|---:|---|
| partial_tp | 0 | **20** | 26 | SHORT |
| trailing_stop | 0 | **13** | 25 | SHORT |
| time_stop | 0 | 0 | 3 | UNEXERCISED |
| stale_exit | 0 | 0 | 7 | UNEXERCISED |
| weekend_flatten | 0 | 0 | 1 | UNEXERCISED |

**This contradicts the boundary note above**, which records that every live close has
been a broker bracket fill caught by the reconciler and that agent-initiated paths are
unexercised. The journal shows 20 partial-TP and 13 trailing-stop live closes dated
**2026-06-09 → 2026-07-27**. Verified, not assumed:

- `apply_live_fill` — the only writer of `filled_exit_price` — is called at exactly two
  sites, both agent-initiated live paths (`cli.py:1815`, `cli.py:1956`).
- Reconciliation **suffixes** its reasons `"(broker)"`, so a bare `partial_tp` /
  `trailing_stop` cannot originate there.
- Rows carry real commissions, and `exit_price` ≠ `filled_exit_price` (modeled trigger
  vs actual fill).

**Open human decision (not made by tooling):** whether those 33 retroactively-identified
executions count as gate evidence, or whether the ≥5 threshold must be met with
write-time-tagged rows from here on. Two paths clear the threshold on the inferred
evidence; `time_stop`, `stale_exit`, and `weekend_flatten` remain genuinely unexercised
live either way. Going forward the ambiguity is gone — every new exit is tagged.

**Taxonomy mismatch to resolve:** the "Exit paths to validate" list above does not map
1:1 onto the code's `ExitReason` values. `time_stop` and `stale_exit` are real exit
reasons absent from the list; "break-even move" is not a distinct exit reason; and
"broker SL/TP handling" / "reconciliation after fill" are broker-side paths, not
agent-managed ones. The gate should be restated against the actual taxonomy.

Deciding and restating both points belongs in `ctrader-trading-agent` per the ownership
rule above; this entry only records the measurement.
