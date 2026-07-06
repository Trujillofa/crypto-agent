# sol_sparse Dryness Diagnosis — 2026-07-06

**Agent:** `agent_sol_sparse` (`config/settings.sol_trend_pullback_sparse.yaml`) — SOLUSDT 4h,
single `trend_pullback` strategy, paper mode.
**Question:** why has the agent not paper-traded since 2026-05-05 (~2 months), per Issue 3 of
`docs/issues/STRATEGY_DROUGHT_REMEDIATION_PLAN.md`? Is it a gate/config defect (like the
overlay's untradeable threshold) or regime?
**Method:** read-only bar-by-bar replay, 2026-04-15 → 2026-07-06. SOLUSDT 4h klines from
Binance public REST; indicators computed with the project's own
`src/features/technical.py::compute_indicators` over trailing 200-bar windows (mirroring
production `computer.py` `limit=200`); signals from the actual `TrendPullbackStrategy` class
with the exact config values from the agent's YAML. First-failing-gate recorded per bar.

## Validation

The replay independently emits a **BUY on 2026-05-05 12:00 UTC @ 85.50** — matching the
agent's real last trade date — plus one on 2026-05-07 (plausibly skipped live due to an open
position/cooldown). The method reproduces production behavior.

## Result: regime, not malfunction

498 bars evaluated. First-failing-gate attribution:

| Binding gate | Bars | Share |
|---|---:|---:|
| `in_uptrend` false (outermost gate) | 399 | 80.1% |
| in uptrend, but >2% from EMA50 (no pullback) | 77 | 15.5% |
| in uptrend + near EMA50, but >3% from VWAP | 11 | 2.2% |
| pullback zone, but no RSI/MACD/price recovery | 9 | 1.8% |
| **BUY** | 2 | 0.4% |

Sub-attribution of the 399 no-uptrend bars: **price ≤ EMA200 on 314**, EMA50 ≤ EMA200 on 45,
trend strength < 0.8% on 39, ATR < 0.8% on 1.

Per month:

| Month | Dominant state |
|---|---|
| Apr (from 15th) | no uptrend (88/96 bars) |
| May | mixed — the two BUYs fired here; mid-May onward trend broke down |
| Jun | **no uptrend on 180/180 bars** — SOL spent the entire month below its 4h EMA200 |
| Jul (to the 6th) | trend re-formed: 28/36 bars are "in uptrend, waiting for a pullback" |

## Conclusions

1. **No defect.** Unlike the overlay (whose `buy_threshold` made entries structurally
   impossible), sol_sparse's gates are reachable — the replay produces BUYs exactly where
   the live agent traded. The strategy is long-only trend-pullback and there was simply no
   4h uptrend to pull back in: SOL was below its EMA200 for essentially all of June.
2. **The agent is currently "armed and waiting", not stuck.** As of early July the uptrend
   gate passes again on most bars; the binding condition is now "price within 2% of EMA50",
   i.e. it is waiting for the first pullback in the recovering trend. If the trend holds, a
   signal is plausible without any config change.
3. **Implication for the Track C capital decision:** funding the spot account would not have
   produced trades during this window — dryness is not evidence for or against funding. The
   real blocker for Track C remains sample size: 8 lifetime trades (+$97.74) is below the
   evidentiary bar this project has used to retire agents, and this analysis does not
   strengthen that sample. If Track C proceeds, it should be on the strength of a proper
   WFO at corrected costs for this config, not on the live 8-trade record.
4. **Do not "fix" the dryness by loosening gates.** 80% of bars fail on the uptrend
   condition; making entries fire in that state converts a trend-pullback system into
   buying a downtrend — the exact failure mode measured on sentiment_macro (2W/13L, all
   longs below trend).

## Reproduction

Replay script: fetch SOLUSDT 4h klines (public REST), rolling 200-bar
`compute_indicators`, evaluate `TrendPullbackStrategy` with the agent YAML's config, record
first failing gate per bar. No DB, no keys, no orders. (Script run from scratchpad; logic
documented above is complete for re-implementation.)
