# cross-venue-dislocation-event-v0 — Gate 2 sweep review (2026-06-12)

Reviewer verification of the RUN_AUTORESEARCH 30-run bounded sweep
(`dislocation_event_entry` family, SOLUSDT 1h, train 3 / test 2 months,
standard gates, executed via the lane manifest with `--execute`).

- Run health: 30/30 rc=0, no crashes or tracebacks (verified in session JSON,
  loop log, and results.tsv). Duration 366.0s. Guard decision RUN_AUTORESEARCH
  allowed (probe_verdict HAS_PULSE from #72).
- Result: **0/30 passed the standard gate. Verdict: discard_all.**
- Evidence: `research/results.tsv` (run_ids 20260612-*), 30 archive JSONs,
  `research/rbi_loop/cross-venue-dislocation-event-v0/last_autoresearch_result.json`

## What the sweep showed (all numbers re-derived from archives)

The failure has unusual structure: **the edge survives the engine but dies in
the WFO fold design.**

| min_bps | h | full-period ret | win % | sharpe | n | WFO ret | WFO n |
|---|---|---|---|---|---|---|---|
| 6.43–6.61 | 24 | **+43.2…+43.7%** | 71–72 | 0.96 | 24–25 | −7.4% | 10 |
| 6.31–6.37 | 24 | +34.5% | 69 | 0.80 | 26 | −13.4% | 11 |
| 5.46–5.53 | 24 | +33.0% | 60–62 | 0.71 | 34–35 | −22.4…−22.9% | 15–16 |
| 6.47–6.48 | 12 | +25.5% | 58 | 0.79 | 26 | −11.8% | 10 |
| 5.33–5.34 | 12 | +3.8% | 48 | 0.19 | 42 | **−35.0%** | **20** |
| 4.51–4.62 | 12 | −18.7…−20.8% | 52 | −0.4 | 56–58 | −44.0…−45.6% | 25–26 |

- 26/30 runs are profitable over the full period in the real engine (fees,
  ATR sizing, executor exit model, time stop) — peaking at +43.7% / Sharpe
  0.96 at the threshold corner the probe ranked strongest (abs ≈6.5–7.0, h24).
- Performance is **monotone in min_spread_bps** (anti-inertness confirmed;
  family is genuinely engaged).
- **WFO test-window return is negative for every one of the 30 candidates**
  (−7.4% best, −45.6% worst).

## Why this is structural, not parametric

Two jaws of a vise, verified at fold level (e.g. min_bps=6.43 h24: seven
2-month test windows containing 0, 2, 2, 3, 1, 2, 0 trades, net −7.4%):

1. **Sparsity jaw**: at edge-bearing thresholds, events occur ~1/month. A
   2-month test window catches 0–3 trades; the whole WFO pass collects 7–16,
   below the min_wfo_trades=20 gate. No threshold inside (or beyond) the
   validated band changes event frequency enough without destroying the edge.
2. **Concentration jaw**: where thresholds are low enough to produce ≥20 WFO
   trades, OOS returns are −30% to −46% — sub-threshold noise events carry no
   edge (exactly consistent with the probe: pass range started at 4.5 bps and
   strengthened toward 7.0). Profit concentration is 66.5–100%: the P&L lives
   in a few episodes, which mostly fall in train-only months, so OOS coverage
   (14 of 26 months) misses them.

Tightening one jaw opens the other. This is the known episodic-event-strategy
vs WFO-cadence conflict, and the lane brief's own standard ("WFO-realistic
counts") is the gate it fails.

## Recommendation: CLOSE the lane

- The standalone shape was the fair test that the cross-venue-basis-v1 closure
  asked for. It got it: real engine, real costs, monotone parameter response —
  and uniformly negative OOS.
- Iterating thresholds cannot fix month coverage; the only paths forward are
  lowering gates (banned) or redesigning evaluation cadence for episodic
  strategies (out of scope for this lane; if ever pursued, it is a research
  program decision, not a lane reshape).
- Positive residue worth keeping: the probe machinery
  (`probe_dislocation_event_strategy.py`) is sound and reusable; the
  full-period +43%/Sharpe 0.96 result documents that cross-venue dislocation
  events DO carry conditional information on SOL — the failure is
  harvestability under house OOS standards, not signal existence.

Guard state after this record: `last_result.summary.passes_gates=false` →
ITERATE_OR_CLOSE (not allowed). Human decision required to close.
