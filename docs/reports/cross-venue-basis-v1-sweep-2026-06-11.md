# cross-venue-basis-v1 — RUN_AUTORESEARCH sweep result (2026-06-11)

**Verdict: 0/30 candidates passed the standard gate. Guard state: ITERATE_OR_CLOSE.**

## Run record

- Command (from lane manifest): `autoresearch_loop.py --symbol SOLUSDT --timeframe 1h --train-months 3 --test-months 2 --gate-profile standard --families cross_venue_dislocation,venue_basis_filter --max-runs 30`
- Executed 2026-06-11 20:59:26–21:04:36 UTC, 310.5s wall, 30/30 runs rc=0 (no crashes).
- 7 OOS WFO windows + 500-iteration bootstrap per candidate; `min_spread_bps` sampled
  3.53–7.06 from measured SOL 1h cross-venue spread percentiles (p90–p99, no lookahead).
- Artifacts: `research/rbi_loop/cross-venue-basis-v1/last_autoresearch_result.json`,
  30 rows in `research/results.tsv` (run_ids `20260611-205926*`–`20260611-210427*`),
  30 per-run archives under `research/archive/experiment-autopilot-20260611-*` (untracked),
  session JSON `research/autoresearch_session.json` (untracked).

## Results by family

### cross_venue_dislocation (require mode, 13 runs)

Entry allowed only during a dislocation (|binance_usdm − bybit spread| ≥ threshold).

- Blocked 67–217 BUYs per run → aggregate WFO trades collapsed to 0–7.
- Every run failed `min_wfo_trades` (20); most also failed sharpe, bootstrap P(loss),
  and profit concentration (100% — single-digit trade counts).
- Structural failure: dislocation events are too rare relative to the SOL 1h winner-stack
  entry cadence. Conditioning entries on them starves the strategy regardless of threshold.

### venue_basis_filter (block mode, 17 runs)

Entry blocked during a dislocation (|spread| ≥ threshold).

- Blocked only 1–11 BUYs per run over ~2 years — the gate barely engages at p90–p99 thresholds.
- Underlying stack metrics unimproved: bootstrap P(loss) 77.8–99.8% (gate: ≤25%),
  WFO sharpe −0.67 to 0.36 (gate: ≥0.5), max DD mostly 18–32% (gate: ≤10%).
- Best candidate overall: `min_bps=5.87 buy=0.97` — WFO sharpe 0.32, 70 trades,
  concentration 27.2% (passing), but P(loss) 92% and DD 25.9% (hard fails).

## Implementation validation (non-inertness)

The gate demonstrably drove behavior on real DB data: `dislocation_blocked_buy_count`
varied with sampled threshold and mode (67–217 for require, 1–11 for block;
`basis_blocked_buy_count` 0 everywhere as the single-venue filter was disabled), with
per-bar engine block logs citing concrete spread values vs the candidate threshold.
Independently verified against session JSON, results.tsv, and all 30 archive JSONs.

## Reviewer recommendation

**Close this lane** (overlay-on-SOL-1h surface). The probe's HAS_PULSE was a conditional
forward-drift edge *after dislocation events*; as an entry overlay the edge cannot be
harvested — require mode starves trades, block mode is a no-op at sensible thresholds.
This is the same structural failure as basis-premium-filter-v0 (probe pulse, WFO reject).

If the dislocation edge is pursued further, it should be a **new lane with a new brief**:
a standalone event-driven strategy that trades the dislocation itself (enter on event,
exit on horizon/convergence), which is what the probe actually measured — not threshold
iteration inside this lane.
