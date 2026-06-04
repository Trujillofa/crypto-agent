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
| Total search runs | ~1,040+ |
| Deployable technical agents | **1** |
| Live technical | `agent_sol_1h_trend_pullback_overlay_live` |
| Independent live strategy | `agent_sentiment_macro` (0 shared OOS entry bars) |
| New standard-gate passes beyond SOL | 0 |
| Gates lowered to inflate count | No |
| Autoresearch sweeps | **Active** — Phase 1–3 (4h / bounded / funding-primary) per next-candidate-path |

The bottleneck is **lack of independent edge**, not tooling. The infrastructure and
gates behaved correctly; ~1,040 runs did not produce additional deployable edge on
the BNB/BTC/AVAX/ETH 1h surfaces tested.

---

## Document Map

| Doc | Role | When to read it |
|-----|------|-----------------|
| **This file** | Index + current-state snapshot | First |
| [`autoresearch-postmortem-2026-06-04.md`](./autoresearch-postmortem-2026-06-04.md) | What ~1,040 runs produced and why sweeps paused | To understand *why* we're here |
| [`autoresearch-candidate-ledger.md`](./autoresearch-candidate-ledger.md) | Per-campaign data (Waves 1–6), gate definitions, decision labels | For exact numbers / before any new run |
| [`autoresearch-next-candidate-path-2026-06-04.md`](./autoresearch-next-candidate-path-2026-06-04.md) | Forward plan — phases, promotion pipeline, prerequisites | To plan the next campaign |
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

Only **Phase 0 (forward monitoring)** is active. Everything else is gated behind
forward evidence or a new surface brief.

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Forward-monitor SOL overlay + sentiment-macro | **active** |
| 1 | ETH/BTC **4h** regime-conditioned candidates | **in progress** (`w7-*-4h-regime`) |
| 2 | `range_reversion_bounded` on ETH/BTC 4h | **in progress** (`w8-*-4h-bounded`) |
| 3 | `funding_primary_standalone` on BTC 4h | **in progress** (`w8-btc-4h-funding-primary`) |
| 4 | Short-side / two-sided (paper + execution parity first) | queued |
| 5 | Longer-history revalidation | per-candidate |

Full phase detail, gate-profile choices, and stop conditions are in the
[next-candidate-path doc](./autoresearch-next-candidate-path-2026-06-04.md).

### Known prerequisite (engineering)

Live DB `positions` rows are **not yet tagged with `agent_id`** for these services.
Until the live position write path tags `agent_id`, realized live entry-overlap
(the independence check Phases 0–4 depend on) can only be measured from WFO/paper
logs, not real fills. Fix this before relying on any live-overlap number.

---

## One-line takeaway

One robust promoted agent exists. The next value is **forward validation and new
signal-surface design**, not incremental 1h parameter search.
