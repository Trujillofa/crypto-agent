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

## External follow-up checklist (work belongs to the cTrader repository)

- [ ] Deterministic replay harness covers every agent-managed exit path
- [ ] Partial TP: ≥5 real-time executions observed and reconciled
- [ ] Trailing stop: ≥5 real-time executions observed and reconciled
- [ ] Break-even move: ≥5 real-time executions observed and reconciled
- [ ] Forced close / emergency exit: ≥5 real-time executions observed and reconciled
- [ ] Broker SL and TP handling re-verified under the current reconciler
- [ ] Forward sample reaches ≥30 closed trades or ≥10 live sessions (whichever later)
- [ ] Net expectancy recomputed after all costs on the forward sample
- [ ] Drawdown check: < 50% of challenge allowance across the validation window
- [ ] Zero-violation audit of challenge rules over the full validation window
