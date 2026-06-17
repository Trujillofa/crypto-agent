# Lane Brief — Macro-Surprise Drift Probe v0 (Gate 0)

**Status:** Gate 0 (brief) → Gate 1 (cheap probe) pending
**Author role:** planned by Claude (planner/reviewer); data-feasibility audit + cheap probe to be built by Grok (builder)
**Predecessors / context:**
- Scheduled-calendar lane CLOSED: [macro-event-drift-probe-v0.md (report)](../reports/macro-event-drift-probe-v0.md) — **NO_PULSE** (priced in; directional consistency ~coinflip)
- Builds directly on that lane's assets (merged via PR #86): `data/macro_events/us_macro_releases.csv`, `scripts/probe_macro_event_drift.py` (point-in-time alignment + matched-baseline machinery to reuse).
- Research rules: [../reports/research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md)

---

## Why this lane (the one principled iteration on the calendar NO_PULSE)

The scheduled-calendar probe found no forward drift around macro releases, with **directional
consistency ~50%**. That is exactly what efficient-markets theory predicts for the *event itself*:
the **expected** component is already priced; only the **surprise** (actual − consensus) should move
prices, and surprises cut both ways, so they wash out when you pool all events regardless of sign.

This lane adds the missing variable — the **data surprise** — and asks whether crypto's forward
return in the event window is **conditioned on the signed/standardized surprise**, not on the bare
event. This is a genuinely new explanatory variable motivated by theory, **not** a reshape of the
closed probe (the reset doc bans reshapes; adding a new, theory-driven variable is allowed).

## Scope decision — CPI & NFP only in v0; FOMC deferred

- **CPI and NFP** have a **clean scalar surprise**: actual vs published consensus, both numeric.
  v0 focuses here.
- **FOMC is deferred.** The rate-decision "surprise" is not a simple actual−consensus scalar — the
  rate is usually anticipated and the surprise lives in the statement/dot-plot/path, requiring
  Fed-funds-futures-implied probabilities (different data, different method). Pulling FOMC in now
  would inflate scope and multiple-testing risk. Note it as a possible v1, do not build it in v0.

## STEP 0 — Data-feasibility audit (the gate, and it is HARDER than the calendar lane)

The probe's first and mandatory step. The hard part is **point-in-time consensus**, not actuals.

1. **Actuals** (easy): historical released values for CPI (headline & core MoM/YoY — pick one
   primary, e.g. headline CPI MoM) and NFP (headline payrolls change). Public: BLS / FRED.
2. **Consensus / forecast** (the make-or-break): the **median analyst forecast published BEFORE the
   release** for each event. This must be **point-in-time** — the consensus as known pre-release,
   **not** a later revision or the actual backfilled. Candidate free/affordable sources: investing.com
   economic calendar, Trading Economics, MarketWatch calendar, Econoday mirrors. Document the source,
   its point-in-time integrity, and licensing. If consensus is only available as a possibly-revised
   single column with no pre-release guarantee, **say so and treat it as a data-quality caveat.**
3. **Build** `data/macro_events/us_macro_surprises.csv` keyed to the existing event rows
   (join on event_type + release_ts_utc from `us_macro_releases.csv`): columns for `actual`,
   `consensus`, and a derived **standardized surprise** `z = (actual − consensus) / stdev(actual −
   consensus over the sample)`. Commit it with a README documenting every source and any gaps.
4. **Coverage / count check:** need consensus+actual for a strict majority of the ~28 CPI + ~28 NFP
   events. If consensus is unobtainable point-in-time for ≥ ~40 of those events → **BLOCKED_ON_DATA**:
   record exactly what is missing and stop. Do **not** fabricate or backfill consensus from actuals.

## Edge thesis (hypothesis to probe)

Crypto (BTC/ETH/SOL) forward return in the post-release window is **monotonically related to the
signed macro surprise**: a hot inflation print (CPI actual > consensus, risk-off) drives negative
forward returns; a cold print drives positive — and symmetrically for NFP (sign to be stated
ex-ante per series, not chosen after seeing results).

- **H1 (conditional directional edge):** sorting events by surprise sign, the **high-surprise** and
  **low-surprise** buckets have forward-return means of **opposite sign**, and a surprise-conditioned
  long/short would beat the matched random baseline past the fee/noise bar.
- **H2 (magnitude monotonicity):** forward return is monotone in standardized surprise `z`
  (e.g. rank correlation / sign of slope consistent across symbols) — stronger evidence than buckets.

State the **expected sign per series ex-ante** (hot CPI → risk-off → crypto down; strong NFP →
ambiguous, state the chosen prior) so a wrong-signed "edge" is not silently relabelled as a pass.

## Cheap probe plan (read-only, after Step 0 passes)

Reuse the merged point-in-time machinery from `probe_macro_event_drift.py` (entry = first bar
opening strictly after release; no overlapping bar; matched random baseline). Add the surprise join.

1. Join each CPI/NFP event to its standardized surprise `z`.
2. For horizons **{+6h, +24h, +72h}**, per symbol: forward return conditioned on surprise.
3. **Bucketed** (sign of `z`: hot vs cold) and **continuous** (return vs `z`: rank correlation /
   slope) analyses. Compare conditioned excess vs the matched random baseline.

## Pulse criteria (encode in the probe)

Per series (CPI, NFP) and horizon, aggregated across the frozen events:
- **H1 PASS:** hot vs cold buckets show **opposite-sign** mean forward returns in the **expected**
  direction, and the long/short spread exceeds the baseline beyond the fee/noise bar (state it,
  e.g. > 0.3% net one-way) on **≥2 of 3 symbols** at **≥1 horizon**.
- **H2 PASS (stronger):** sign of return-vs-`z` slope is consistent and in the expected direction on
  ≥2/3 symbols (report rank correlation).

Verdict:
- **HAS_PULSE** — H1 (ideally + H2) holds in the expected direction → bounded surprise-conditioned
  strategy surface, then Gate 2.
- **WEAK_EDGE** — a relationship exists but only one series/one symbol, or right shape but inside the
  fee bar → record, decide; do not widen series/horizons to rescue it.
- **NO_PULSE** — no conditional relationship after fees → the surprise is also priced within the
  hour; close the macro family.

## Expected failure modes (do not oversell)

- **Surprise also priced in minutes** — crypto reacts inside the first hour and the *forward* drift
  after our entry bar is gone → NO_PULSE.
- **Consensus data not truly point-in-time** → spurious or look-ahead-contaminated result; Step 0
  must be honest, and a caveat must ride with any HAS_PULSE.
- **Wrong/unstable sign** (NFP especially: "good news is bad news" regime-dependence) → at best WEAK.
- **Thin buckets** (~28 events/series split by sign ≈ 14 each) — enough for a cheap directional probe,
  borderline for Gate-2 WFO; flag it.

## Guardrails (do not violate)

1. **Point-in-time consensus only** — the forecast as known pre-release; never the actual backfilled
   or a later-revised consensus. This is the integrity crux.
2. **Frozen series + ex-ante sign** — CPI & NFP fixed, expected direction declared before measuring.
   No post-hoc series/horizon additions or sign-flips to manufacture a pass.
3. **Reuse the merged point-in-time entry/baseline machinery** — same no-look-ahead alignment.
4. **Data audit is gate 0** — consensus unobtainable point-in-time → BLOCKED_ON_DATA, stop.
5. FOMC out of scope for v0.

## Validation command plan

```bash
uv run python scripts/probe_macro_surprise_drift.py --json   # builder to create (extends the calendar probe)

uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/macro-surprise-drift-probe-v0.md \
  --probe-verdict <HAS_PULSE|WEAK_EDGE|NO_PULSE> --pretty
```

## Reviewer (Claude) checkpoints

(a) **consensus source documented with point-in-time integrity explicitly assessed** (the crux);
(b) surprise standardization correct and committed; (c) expected sign declared ex-ante per series;
(d) point-in-time entry/baseline reused unchanged (no look-ahead); (e) bucketed + continuous results
per symbol/horizon vs matched baseline; (f) verdict honest with event counts and the consensus
data-quality caveat attached.
