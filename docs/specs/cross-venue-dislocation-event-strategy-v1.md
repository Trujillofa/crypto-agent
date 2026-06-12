# Lane brief: cross-venue-dislocation-event-v1 (high-frequency rolling variant)

**Hypothesis HYP-CVE-002**: SOL 1h long entries on *rolling-percentile* positive
extremes of the cross-venue spread (binance_usdm − bybit, premium-index or
basis), exited at a fixed 12/24-bar horizon, carry a thin but harvestable net
edge at a trade frequency that satisfies house WFO standards.

## Lineage and why this lane exists

- Parent lane **cross-venue-dislocation-event-v0** closed 2026-06-12 after its
  30-run sweep failed 0/30 (#74). The failure was structural for the *fixed
  absolute-threshold* shape only: fat per-event edges (+0.35–1.79% net) at
  ~1 event/month could not satisfy `min_wfo_trades ≥ 20` inside 2-month test
  windows, while sub-threshold events carried no edge. Full-period in-engine
  performance was positive in 26/30 runs (best +43.7% / Sharpe 0.96), so the
  conditional signal exists; harvestability under OOS cadence was the failure.
- The v0 Gate 1 probe **also validated five rolling-threshold scenarios that
  the v0 family never implemented** — this lane tests exactly those, nothing
  new. Evidence (verbatim from
  `research/rbi_loop/cross-venue-dislocation-event-v0/probe-verdict.json`,
  all SOL long, no-lookahead trailing 90d thresholds, net of 0.10% costs):

| scenario | n (deduped) | net mean % | net median % | win % | conc % | events / 2-mo window |
|---|---|---|---|---|---|---|
| basis_bps rolling tail5 h12 | 528 | +0.163 | +0.118 | 51.1 | 48.6 | ~36 |
| premium rolling tail10 h12 | 838 | +0.127 | +0.040 | 50.8 | 39.9 | ~57 |
| premium rolling tail10 h24 | 549 | +0.205 | +0.114 | 51.5 | 38.0 | ~37 |
| premium rolling tail5 h24 | 408 | +0.348 | +0.123 | 52.9 | 28.6 | ~28 |
| premium rolling tail5 h6 | 705 | +0.155 | +0.034 | 50.8 | 43.4 | ~48 |

- The sparsity jaw that killed v0 does not bind here: every scenario yields
  ≥28 events per 2-month WFO test window vs the ≥20-trade gate. The open
  question shifts to whether a 51–53% win / ~+0.1% median edge survives
  Sharpe, drawdown, and bootstrap-P(loss) gates. Odds are honestly
  low-to-moderate; cost is one bounded sweep.

## Gate 1 (probe) — already satisfied

`probe_verdict: HAS_PULSE` is carried from the v0 probe run (real DB,
2026-06-12, rc=0; independently re-derived in
`docs/reports/cross-venue-dislocation-event-v0-probe-2026-06-12.md`). No new
probe run is required or permitted — re-probing the same data to refresh
evidence would be selection bias.

## Gate 2: family `dislocation_event_rolling_entry`

Extends `DislocationEventStrategy` with a rolling-threshold mode:

- **Online trailing percentile**: at each bar, threshold = the (100 − tail)th
  percentile of the spread over the trailing 90 days, computed from strictly
  prior bars only (the probe's `_precompute_rolling_thresholds` two-pointer
  window is the reference for the no-lookahead discipline; the live strategy
  maintains the window incrementally).
- Long-only, positive extremes only (unchanged from v0; shorts never passed
  any probe).
- Sampled free parameters (≤5 rule; 4 used):
  `metric` ∈ {premium_spread, basis_spread}, `tail_pct` ∈ {5, 10},
  `horizon` ∈ {12, 24} bars; `rolling_days = 90` fixed (probe-validated, not
  sampled); `cooldown = horizon` tied. h6 excluded (+0.034 median too thin).
- Exit: time stop = horizon × 60 min; SL/TP fixed wide (4.0 / 8.0 ATR,
  trailing neutralized) as in v0.

## Kill criteria (pre-registered; this lane is terminal)

- Sweep fails 0/30 under standard gates → **close the cross-venue dislocation
  primitive entirely** (both event shapes, fixed and rolling, will then have
  failed bounded OOS evaluation). No v2, no reshape, no gate relaxation.
- Family implementation needs lookahead to compute rolling thresholds, or
  >5 free parameters → close at design stage without sweeping.
- Insufficient warm-up handling: strategy must HOLD (not crash, not guess)
  during the first 90 days of window fill.

## Gates 3–6 (unchanged house rules)

- Gate 3/4: promotion pre-filter, then bootstrap=1000.
- Gate 5: `analyze_entry_overlap.py` vs `sol-1h-trend-pullback-overlay-live`
  and `sentiment-macro` (<35% Jaccard). Note: at 28–57 events/2 months this
  strategy trades far more often than v0 would have; overlap risk with
  sentiment-macro SOL entries is correspondingly higher and Gate 5 is a real
  gate here, not a formality.
- Gate 6: ≥20 closed paper trades under a distinct AGENT_ID before any live
  notional.

Human approval required at every stage transition; the lane manifest guard
(`rbi_loop_from_manifest.py`, dry by default) records each decision.
