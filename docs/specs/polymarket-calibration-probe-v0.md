# Lane Brief + Builder Handoff — Polymarket Calibration / Favorite-Longshot Probe v0 (Gate 0 → Gate 1)

**Status:** Gate 0 brief complete → **Gate 1 build handoff to Grok (builder).**
**Author role:** planned/specified by Claude (planner/reviewer). **Implementation by Grok.** Claude
reviews the code + verdict; Claude does not write the probe `.py`.
**Predecessors / context:**
- Program terminal state on the crypto/OHLCV universe: [../reports/research-consolidation-2026-06-19.md](../reports/research-consolidation-2026-06-19.md)
- "Different universe" candidate ranking — this is **candidate #2**; #1 is [mnav-premium-reversion-probe-v0.md](mnav-premium-reversion-probe-v0.md)
- External idea source: `vibe-investing` "Iran Polymarket Insider Trading Forensics" (a resolution-integrity caution, not an edge claim)

---

## Why this lane (first principles)

This lane flips **four axes** of the dead program at once: venue (Polymarket / on-chain, not
Binance), asset (binary event contracts, not crypto), data primitive (contract price + realized
resolution, not OHLCV), and objective (probability mispricing, not a price forecast). It cannot
fail for the same reason as anything before it.

**The mechanism is a documented behavioral bias, not a statistical hope.** Across betting and
prediction markets, the **favorite-longshot bias** is one of the most robust anomalies: low-
probability ("longshot") contracts are systematically *overpriced* (they resolve YES less often
than their price implies) and favorites are mildly underpriced. If Polymarket exhibits it, the
edge is **systematically sell/short overpriced longshots** (and/or buy underpriced favorites),
sized by the calibration gap.

This is also the **single most backtestable** prediction-market edge, because the ground truth is
objective and free: a resolved market either paid YES or NO. No look-ahead is possible on the
outcome once you condition only on the **pre-resolution price**.

## The signal / edge (precise definition — implement exactly)

For each **resolved** binary market `m`, take its traded YES price `p(m, τ)` at a fixed lead time
`τ` before resolution (default τ = 24h and 72h before close, reported separately), and its realized
binary outcome `y(m) ∈ {0,1}`.

- **Calibration curve:** bucket markets by `p` (deciles: [0,0.1), …, [0.9,1.0]); for each bucket
  compute realized YES frequency `freq = mean(y)` and mean price `mean(p)`.
- **Mispricing per bucket:** `edge = mean(p) − freq`. Positive in a bucket ⇒ contracts there are
  *overpriced* (sell YES); negative ⇒ underpriced (buy YES). The favorite-longshot signature is
  `edge > 0` concentrated in the high-price-of-the-unlikely-side / low-`freq` longshot buckets.
- **Tradeable edge per bucket:** `net_edge = |edge| − round_trip_cost`, where round-trip cost =
  half-spread at `τ` + Polymarket fees + a gas/settlement allowance (document the assumption).

## Data sources (all free, read-only, public)

| Field | Source | Notes |
|-------|--------|-------|
| Resolved markets + outcomes | Polymarket **Gamma API** (`gamma-api.polymarket.com`) | market metadata, close time, resolution/outcome |
| Historical contract prices | Polymarket **CLOB / data API** (`clob.polymarket.com` or the public data/timeseries endpoint) | YES-token price time series → price at lead time τ |

> **STEP 0 builder task:** verify the exact current public endpoints (they change) and confirm you
> can retrieve, for a usable sample of **resolved** markets: (a) the binary outcome, (b) the close
> timestamp, and (c) a price at τ before close. Document endpoints + query in
> `data/polymarket/DATA_SOURCES.md`. No API key should be required; if one is, that is a feasibility
> finding to report, not a blocker to route around with a paid source.

**No hand-seeded universe** (unlike mNAV) — the universe is "all resolved binary markets in the
window," pulled programmatically. Cache the pulled sample to
`data/polymarket/resolved_markets_<window>.jsonl` so the probe is reproducible offline.

## STEP 0 — Data feasibility (mandatory gate before any edge claim)

Pull resolved binary markets over a window (default last 18 months). Keep only markets that are
**genuinely binary, actually resolved, and have a price at τ**. Exclude: markets resolved as
invalid/refunded, near-zero-liquidity markets (no real trades), and multi-outcome markets not
cleanly reducible to binary. If `usable_markets < MIN_MARKETS` (default 300) → **BLOCKED_ON_DATA**.
Report: total pulled, usable count, exclusions by reason, liquidity distribution, and category mix
(politics / crypto / sports / other) — category concentration is a key caveat.

## STEP 1 — Edge test (calibration / favorite-longshot)

Pooled across usable markets:

- **H1 (miscalibration exists & is tradeable):** at least one price bucket shows `|edge|` exceeding
  the round-trip cost with a **binomial test** that the realized frequency differs from the bucket
  price (report p-values; apply a multiple-testing correction across buckets). The classic pass is
  a monotone longshot-overpricing pattern, but report the full calibration curve regardless.
- **H2 (out-of-sample / not regime-or-category fluke):** the same sign of mispricing in the
  qualifying buckets **persists on a time split** (train = older half, forward = newer half) **and**
  is not driven by a single category (re-run excluding the largest category; the edge should
  survive). A bias that only appears in, say, 2024-election markets is not a durable universe edge.

Resolution-integrity caution (from the vibe-investing Iran case): flag any markets with disputed /
oracle-contested resolutions; a calibration "edge" built on manipulated resolutions is not real.

## Verdict semantics (match the other probes)

- **HAS_PULSE** := H1 and H2 pass. (Authorizes a v1: realistic fill/liquidity model + Polygon/CLOB
  execution-feasibility audit — NOT deployment.)
- **WEAK_EDGE** := H1 passes but H2 fails (single-category or doesn't survive the time split).
- **NO_PULSE** := miscalibration is inside cost/spread noise or statistically insignificant.
- **BLOCKED_ON_DATA** := STEP 0 fails (too few clean resolved markets / endpoints unavailable).

## Build contract (file paths, CLI, artifacts, tests)

| Item | Requirement |
|------|-------------|
| Script | `scripts/probe_polymarket_calibration.py` |
| Pattern to mirror | `scripts/probe_funding_carry_neutral.py` (frozen dataclasses, `run_probe`/`render_report`/`main`, `--output-dir`, JSON + MD artifacts; one `aiohttp.ClientSession` for the run) |
| Reuse | `get_logger`/`configure_logger` from `src/utils/logger.py` |
| Data cache | `data/polymarket/resolved_markets_<window>.jsonl` (+ `.gitignore` allowlist) and `data/polymarket/DATA_SOURCES.md` |
| Artifacts | `research/rbi_loop/polymarket-calibration-v0/{probe_result.json,probe_report.md}` incl. the full calibration table |
| CLR flags | `--start`, `--end`, `--lead-hours` (24,72), `--buckets` (10), `--min-markets` (300), `--round-trip-cost-pct`, `--min-liquidity`, `--output-dir`, `--cache-file` |
| Tests | `tests/test_probe_polymarket_calibration.py` — calibration bucketing + edge math on a fixture, the resolved/invalid filter, the binomial significance gate, and verdict routing. **No network in tests** (inject a fixture market list). |
| Constraints | Read-only. No wallet, no on-chain tx, no orders, no `--execute`. `ruff`/`pytest` clean; hooks pass. |

## Hard "do NOT" list (review will reject on any of these)

- Do **not** condition on anything observed *after* the lead time τ (outcome leakage). Only `p(m, τ)`
  and the final outcome enter the test.
- Do **not** include unresolved, invalid/refunded, or near-zero-liquidity markets in the edge sample.
- Do **not** declare HAS_PULSE off a single category (politics-only = WEAK_EDGE).
- Do **not** ignore spread/fees/gas — a raw calibration gap that doesn't clear round-trip cost is
  NO_PULSE.
- Do **not** add wallet/CLOB order code or any deployment path — this is a read-only Gate-1 probe.

## Kill criteria

- BLOCKED_ON_DATA → public endpoints unusable or too few clean resolved markets; record, and this
  closes the candidate-#2 cheap path (do not buy a data vendor to force it).
- NO_PULSE / WEAK_EDGE → no durable cross-category miscalibration beyond cost; close, document.
- HAS_PULSE → write v1 brief (fill/liquidity realism + Polygon execution-rails feasibility); the
  on-chain rails + capital decision is a human one, not automated.
