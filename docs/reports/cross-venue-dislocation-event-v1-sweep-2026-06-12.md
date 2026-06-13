# cross-venue-dislocation-event-v1 — Gate 2 sweep review (2026-06-12)

Reviewer verification of the RUN_AUTORESEARCH 30-run bounded sweep
(`dislocation_event_rolling_entry` family — the rolling-threshold variant,
SOLUSDT 1h, train 3 / test 2 months, standard gates, executed via the lane
manifest with `--execute`). **This is the terminal lane for the cross-venue
dislocation primitive.**

- Run health: 30/30 rc=0, 350.2s. Verified clean independently.
- Result: **0/30 passed the standard gate. Verdict: discard_all.**
- **Recommendation: CLOSE the cross-venue dislocation primitive entirely**
  (pre-registered terminal condition met).
- Evidence: `research/results.tsv` (run_ids 20260612-2241*–2247*), 30 archive
  JSONs, `research/rbi_loop/cross-venue-dislocation-event-v1/last_autoresearch_result.json`

## Stale-log red herring (resolved before trusting the result)

The runner's `grep -i "traceback|exception" autoresearch_loop.log | tail -20`
surfaced 30 `InvalidPasswordError: ... user "trading"` strings, which would
ordinarily mean a tainted run. Independently confirmed they are **stale**:
all 30 sit at lines 73–2335, every one tagged `"commit": "c1525eb"` — the
2026-06-11 cross-venue-basis-v1 crashed sweep. Today's run (commit `3699fcf`)
begins at line 12147; its log segment to EOF contains **zero** auth errors and
zero tracebacks, and shows real data loads (1417–1489 points per WFO window)
and real trades. `autoresearch_loop.log` is append-only, so the grep tail
reached back into old content. The result stands.

## What the sweep showed (re-derived from results.tsv + 30 archives)

The rolling variant did exactly what it was designed to do — and that is what
kills the primitive.

| metric | tail | h | WFO ret % | sharpe | DD % | P(loss) % | conc % | trades |
|---|---|---|---|---|---|---|---|---|
| basis | 5 | 24 | −15.3 | −0.19 | 45.8 | 67.0 | 70.9 | 144 |
| basis | 5 | 12 | −38.5 | −0.93 | 64.1 | 96.0 | 64.4 | 182 |
| premium | 5 | 24 | −34.3 | −0.73 | 56.7 | 87.6 | 49.7 | 134 |
| premium | 5 | 12 | −34.4 | −0.61 | 58.7 | 96.6 | 100.0 | 178 |
| premium | 10 | 24 | −54.8 | −1.50 | 78.6 | 99.2 | 67.1 | 195 |
| premium | 10 | 12 | −49.3 | −1.46 | 77.3 | 99.0 | 79.4 | 289 |

- **The v0 sparsity jaw is gone**: trade counts 134–289, WFO trade counts ~66
  — comfortably above the min_wfo_trades=20 gate that structurally killed v0.
- But **full-period in-engine return is negative in 30/30** (range −74.7% to
  −16.3%; best −16.3%, Sharpe −0.05). This is the opposite of v0, where 26/30
  were full-period positive. Frequency was bought at the cost of edge.
- Win rates ~51% match the probe (50.8–52.9%) exactly — the strategy fires on
  the right events. The failure is edge **magnitude**, not event detection.

## Why the probe edge did not survive

The probe measured net medians of +0.03% to +0.12% per event at ~51–53% win,
using a flat 0.10% cost and a clean exit exactly at the horizon. The backtest
engine instead applies ATR-based position sizing, the executor exit model
(ATR SL/TP, trailing, time stop), and the real fee schedule. On an edge that
thin, the gap between an idealized horizon exit and a realistic exit path is
larger than the edge itself — so a marginally-positive probe signal becomes a
negative live strategy. Bootstrap P(loss) 67–99% confirms the distribution is
dominated by losses, not a few unlucky draws.

## Both shapes have now failed — close the primitive

| variant | event shape | trade counts | full-period | WFO | failure |
|---|---|---|---|---|---|
| v0 (fixed abs) | rare, fat | 7–20 WFO | +43.7% best | negative | sparsity vs min_wfo_trades |
| v1 (rolling) | frequent, thin | ~66 WFO | negative | negative | edge too thin for engine |

The two variants span the frequency/edge trade-off the probe offered, and both
fail bounded OOS for opposite reasons. There is no remaining region: raising
frequency thins the edge below engine realism; lowering it starves WFO trades.
Per the v1 brief's pre-registered kill criteria, this closes the **cross-venue
dislocation primitive** entirely — no v2, no reshape, no gate relaxation.

Positive residue (unchanged from v0): the probe machinery is sound and
reusable, and cross-venue dislocation events carry genuine conditional
information on SOL — it is simply not harvestable under house OOS standards
with a horizon-exit event strategy.

Guard state after this record: `last_result.summary.passes_gates=false` →
ITERATE_OR_CLOSE (not allowed). Human decision required to close.
