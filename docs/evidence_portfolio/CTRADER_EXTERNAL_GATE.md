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

### Decision 1 (2026-07-27): inferred executions accepted, replay still required

The 33 retroactively-identified executions **count toward the ≥5 execution threshold**.
Each path must **still pass deterministic replay** before it is production-ready —
execution count alone does not make a path ready.

Going forward the ambiguity is gone: every new exit is tagged at write time. (The
`--accept-inferred` flag this originally added was removed on 07-28 — once paper
executions count, inferred rows are included either way.)

### Decision 2 (2026-07-27): exit paths restated against the code taxonomy

The original "Exit paths to validate" list did not map onto the code's `ExitReason`
values. It is superseded by the following. **Agent-managed** (what the ≥5 threshold and
the replay requirement apply to):

| path | live executions | deterministic replay | ready? |
|---|---:|---|---|
| `partial_tp` | 20 ✅ | dedicated parity test ✅ | **yes** |
| `trailing_stop` | 13 ✅ | only incidental, inside the partial_tp scenario ⚠️ | no — needs its own scenario |
| `time_stop` | 0 ❌ | none ❌ | no |
| `stale_exit` | 0 ❌ | none ❌ | no |
| `weekend_flatten` | 0 ❌ | none — `flatten_all` is mocked, never replayed ❌ | no |

**Broker-side paths** (not agent-managed; the threshold does not apply): broker SL
handling, broker TP handling, reconciliation after fill. Reconciliation emits these with
a `"(broker)"` suffix, which is what keeps them out of the agent-managed counts.

**Dropped:** "break-even move" — not a distinct exit reason in the code.

**Replay coverage is thinner than assumed.** `tests/test_backtest_paper_tracker_parity.py`
contains a single test (`test_backtest_and_paper_tracker_agree_on_partial_tp_bar_exit_count`).
That file exists because of the 2026-04-08 shelving, caused by silent drift between two
re-implementations of exit logic — so the missing scenarios are guarding against a failure
mode that has already happened once. *(Resolved 07-28: scenarios added for all four
uncovered paths.)*

---

## Update 2026-07-28 — why the paths were unexercised, and the gate restated

The 07-27 table above is **superseded**. Diagnosing *why* three paths read UNEXERCISED
found that the gate, as written and as first implemented, **could not be satisfied by
waiting** — two paths are effectively unreachable in live:

- **Max live `bars_held` is 9** across 64 live exits, while `time_stop_hours` and
  `stale_exit_bars` both default to **10**. No live position has ever survived long enough
  for either branch to be evaluated; live positions close at 2–4 bars in practice.
- **`broker_managed_protection` landed 2026-07-14** (`571f7dc`) and disables the agent's
  own TP/SL/trailing checks in live. Live bare `trailing_stop` exits run 2026-06-09 →
  **2026-07-14 and stop dead**; they now arrive via reconciliation as
  `"trailing_stop (broker)"`. Live `partial_tp` continues through 2026-07-27.

### Decision 3 (2026-07-28): paper and demo executions count

Per this file's own Important rule — *"Demo, paper, or micro-live testing can satisfy the
exit-path validation gate in parallel"* — the ≥5 threshold counts executions in **live or
paper**. The initial live-only implementation contradicted that rule.

### Decision 4 (2026-07-28): `trailing_stop` reclassified as broker-side

It joins `tp`/`sl` as broker-managed, because that is what the code does since 07-14. Its
13 pre-07-14 agent-initiated live executions stand in the record as history; the current
architecture cannot produce more.

### Current state

| path | executions (live + paper) | deterministic replay | ready? |
|---|---:|---|---|
| `partial_tp` | **46** (20 + 26) ✅ | dedicated parity test ✅ | **yes** |
| `stale_exit` | **7** (0 + 7) ✅ | single-engine ✅ | **yes** |
| `time_stop` | 3 (0 + 3) ❌ | single-engine ✅ | needs 2 more |
| `weekend_flatten` | 1 (0 + 1) ❌ | single-engine ✅ | needs 4 more |
| ~~`trailing_stop`~~ | — | parity test ✅ | reclassified broker-side |

Two replay caveats: `stale_exit`'s backtest branch lives in the walk-forward loop rather
than `_advance_position`, so it cannot be driven cross-engine; and `weekend_flatten` has
**no** cross-engine parity available at all, since `backtest.py` models no weekend or
session calendar. For those, deterministic replay means deterministic unit behaviour.

### Open items

- **Ordering divergence, undecided.** When a losing position hits both thresholds on one
  bar, the tracker reports `time_stop` (section D before E) and the backtest reports
  `stale_exit` (~504 before ~530). Reachable at stock settings — both thresholds default
  to 10. P&L is identical, the recorded reason is not, which splits exit-path attribution
  between the engines. Pinned by `TestTimeStopStaleExitOrdering`; picking a winner is a
  behaviour decision.
- **Deploy after merge.** Provenance tagging only applies to exits written by deployed
  code.
- Remaining accrual: 2 × `time_stop`, 4 × `weekend_flatten`, both of which accrue in paper.

Implementation and remaining work belong in `ctrader-trading-agent` per the ownership rule
above; this entry records the measurements and decisions.
