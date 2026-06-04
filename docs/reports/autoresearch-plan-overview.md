# Autoresearch Plan — Overview & Index

**Purpose:** single entry point for the autoresearch candidate-search effort. Start
here, then follow the links. This file is an index and current-state snapshot, not
a data log — per-campaign numbers live in the ledger, and gate thresholds live in
code.

**Last updated:** 2026-06-04

---

## Goal

Find 5–10 **independent** trading agents that each pass walk-forward + bootstrap=1000
gates and together support ~8–20 trades/month for faster forward validation.

**Count is subordinate to gates and independence.** A second weak or correlated
agent adds risk faster than it adds validation speed.

---

## Current State

| Item | Value |
|------|-------|
| Total search runs | ~1,440+ (incl. Wave 7 ~400) |
| Deployable technical agents | **1** |
| Live technical | `agent_sol_1h_trend_pullback_overlay_live` |
| Independent live strategy | `agent_sentiment_macro` (0 shared OOS entry bars) |
| New standard-gate passes beyond SOL | 0 |
| Gates lowered to inflate count | No |
| Autoresearch sweeps | **Paused** after Wave 7 (0 passes; ETH 4h bounded near-miss only) |

The bottleneck is **lack of independent edge**, not tooling. The infrastructure and
gates behaved correctly; ~1,440 runs did not produce additional deployable edge on
the BNB/BTC/AVAX/ETH 1h and ETH/BTC 4h surfaces tested.

---

## Document Map

| Doc | Role | When to read it |
|-----|------|-----------------|
| **This file** | Index + current-state snapshot | First |
| [`autoresearch-postmortem-2026-06-04.md`](./autoresearch-postmortem-2026-06-04.md) | What ~1,040 runs produced and why sweeps paused | To understand *why* we're here |
| [`autoresearch-candidate-ledger.md`](./autoresearch-candidate-ledger.md) | Per-campaign data (Waves 1–6), gate definitions, decision labels | For exact numbers / before any new run |
| [`autoresearch-next-candidate-path-2026-06-04.md`](./autoresearch-next-candidate-path-2026-06-04.md) | Forward plan — phases, promotion pipeline, prerequisites | To plan the next campaign |
| [`../specs/relative-strength-rotation-surface-v0.md`](../specs/relative-strength-rotation-surface-v0.md) | First-principles surface brief: cross-asset relative strength rotation | Before implementing the next research family |
| [`relative-strength-rotation-implementation-summary-2026-06-04.md`](./relative-strength-rotation-implementation-summary-2026-06-04.md) | Implementation summary, missing code, and launch checklist for relative strength rotation | Before assigning implementation work |
| [`entry-overlap-sol-1h.md`](./entry-overlap-sol-1h.md) | Independence evidence (SOL overlay vs sentiment-macro) | When checking candidate independence |

Gate thresholds are defined once in the ledger and implemented in `GATE_PROFILES`
in `scripts/run_autoresearch.py`. Do not restate them elsewhere.

---

## Promotion Pipeline (fixed)

```
b=100 discovery (standard gate)
   → promotion_candidate pre-filter (stricter; eligible_for_bootstrap_1000)
   → bootstrap=1000 (same standard gate, only --bootstrap 1000)
   → entry-overlap check vs live agents
   → tracked paper config
   → small live notional
```

- `standard` gate = discovery floor.
- `promotion_candidate` = stricter pre-filter, run before spending b=1000 compute.
- bootstrap=1000 = the real promotion gate. **Never paper/live from b=100 alone** —
  it is what killed the AVAX/ETH Wave-2 near-misses.
- `probe_1h` (min 15 WFO trades) may tag research near-misses only, never promote.

---

## Live Portfolio

| Agent | Symbol | TF | Role |
|-------|--------|-----|------|
| `agent_sol_1h_trend_pullback_overlay_live` | SOLUSDT | 1h | Only deployable technical (standard + b=1000) |
| `agent_sentiment_macro` | SOLUSDT | 1h | Independent sentiment/macro futures |
| `agent_sol_sparse`, `agent_sol_panic_block_paper` | SOLUSDT | 4h | Paper research — not in promotion queue |

---

## Closed Surfaces (do not re-run without a new hypothesis)

- BNBUSDT 1h standalone and overlay (standalone fired 0 trades — wrong surface)
- BTCUSDT 1h standalone and overlay (Wave 5–6: sparse standalone, worse overlay)
- AVAX / ETH Wave-2 #0004 tracked overlays (collapsed at bootstrap=1000)
- SOL 1h `mtf_breakout_standalone` (catastrophic over-trading)
- Repeated threshold / aggregator sweeps on the same 1h technical stack

---

## What Happens Next

Only **Phase 0 (forward monitoring)** is active operationally. The relative
strength surface brief exists, but its first ETH/BTC 1h feasibility probe failed
(14 events / 20,508 rows, negative forward excess), so do not proceed to full
implementation without reshaping the probe or selecting a different surface.
The surface brief is:
[`relative-strength-rotation-surface-v0.md`](../specs/relative-strength-rotation-surface-v0.md);

Phase numbers below match the phases in the
[next-candidate-path doc](./autoresearch-next-candidate-path-2026-06-04.md). The
relative-strength surface is a **new surface brief**, not one of those legacy
phases — it is listed separately as the active next research target.

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Forward-monitor SOL overlay + sentiment-macro | **active** |
| 1 | ETH/BTC **4h** regime-conditioned | **closed** (≤5 WFO trades best) |
| 2 | `range_reversion_bounded` ETH/BTC 4h | ETH **near_miss**; BTC **closed** |
| 3 | `funding_primary_standalone` BTC 4h | **closed** (0 trades) |
| 4 | Short-side / two-sided (paper + execution parity first) | queued |
| 5 | Longer-history revalidation | per-candidate |
| **New surface** | `relative_strength_rotation_standalone` (cross-asset, ETH/BTC anchor) | **paused** after sparse/negative initial probe |

Full phase detail, gate-profile choices, and stop conditions are in the
[next-candidate-path doc](./autoresearch-next-candidate-path-2026-06-04.md).

### Engineering prerequisite — RESOLVED (`bc309ae`, deployed 2026-06-04)

The live position write path now tags the **real per-agent `agent_id`** on both
`positions` and `trades`, and reads are agent-scoped (`src/portfolio/manager.py`).
Realized live entry-overlap (the independence check Phases 0–4 depend on) can now
be measured from real fills, **starting from this deploy forward**. Caveat:
pre-deploy historical rows were backfilled to the literal `'default'` bucket and
cannot be split per-agent retroactively — only post-`bc309ae` fills carry true
attribution.

---

## One-line takeaway

One robust promoted agent exists. The next value is **forward validation and new
signal-surface design**, not incremental 1h parameter search.
