# Lane Brief — Scheduled Macro-Event Drift Probe v0 (Gate 0)

**Status:** Gate 0 (brief) → Gate 1 (cheap probe) pending
**Author role:** planned by Claude (planner/reviewer); data-feasibility audit + cheap probe to be built by Grok (builder)
**Predecessors / context:**
- Trend family CLOSED: [daily-trend-breadth-probe-v0.md (report)](../reports/daily-trend-breadth-probe-v0.md) — NO_PULSE, shared-beta corr 0.59
- Research rules / banned lanes / next-family ranking: [../reports/research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md) — **news/event calendar filter is the #1 unexplored family**

---

## Why this lane (first principles)

Every closed lane to date used the **same explanatory variable**: price/volume structure on one
or more crypto symbols (sweeps, ranges, trend, breadth, basis). They share one market beta and one
information set, so they fail together. The research-reset runbook's explicit conclusion: the next
primitive must require **new information**, not another OHLCV transform.

The cheapest source of genuinely new, *exogenous*, *backtestable* information is the **scheduled
US macro calendar** — FOMC decisions, CPI, and Nonfarm Payrolls (NFP). These are:
- **Exogenous** to crypto price action (driven by the macro economy / Fed),
- **Known ex-ante** — release timestamps are public and scheduled months ahead → **zero look-ahead
  on the trigger** (the single hardest problem for any news lane),
- **Free** and **static** (a small historical timestamp table, not a paid streaming API),
- Increasingly relevant — since 2022 crypto trades heavily as a macro/liquidity risk asset.

## The critical constraint that defines this lane

**Backtestability requires point-in-time data.** The existing `sentiment-macro` agent uses a
*runtime LLM* (xAI/DeepSeek) — that is **not backtestable** (you cannot replay what an LLM would
have said in Jan 2024). This lane deliberately avoids LLM/news-feed sentiment for exactly that
reason and uses the **scheduled calendar**, whose timestamps are objective and historically fixed.

## Edge thesis (hypothesis to probe)

Crypto (BTC/ETH/SOL) exhibits a **statistically reliable forward return and/or volatility
signature in the window around scheduled high-impact US macro releases** — distinct from a random
same-length window. Two sub-hypotheses, probe both, report separately:

- **H1 (directional drift):** mean forward return in the event window (e.g. release → +6h / +24h)
  differs reliably from baseline, with consistent sign across events → a tradeable long/short or
  exposure tilt around scheduled events.
- **H2 (volatility / risk gate):** realized vol in the event window is reliably elevated → a
  risk-management overlay (cut size / sit out) rather than a standalone direction.

H1 is the candidate-#2 directional prize; H2 is a weaker but still useful fallback. **If only H2
holds, that is WEAK_EDGE** (overlay, not standalone) — record and decide, do not oversell.

## STEP 0 — Data-source feasibility audit (the gate that comes before any edge test)

The probe's **first and mandatory** step. Do not measure any edge until this passes.

1. Identify a source of **historical release timestamps** for FOMC / CPI / NFP covering
   **2024-01-01 → 2026-06-01**, with **release date AND time** (UTC), and an impact/actual/forecast
   field if available. Acceptable: a free economic-calendar API, a public dataset, or a small
   hand-verified static CSV committed to the repo (`data/macro_events/*.csv`). Document the source
   and its licensing.
2. **Verify timestamp quality** — releases must be timestamped to at least the hour (FOMC ~19:00
   UTC, CPI/NFP ~13:30 UTC). Date-only is insufficient for a +6h window on a 24/7 asset; if only
   date-only is obtainable, restrict windows to ≥ +24h and flag the precision loss.
3. **Coverage / count check:** count usable events in-window. Expected ≈ FOMC 8/yr + CPI 12/yr +
   NFP 12/yr ≈ **~32/yr × 2.4y ≈ 75 events**. This is the *event* count; trades count is per-symbol.
4. If no source gives ≥ ~50 reliably-timestamped events over the window → **BLOCKED_ON_DATA**:
   record what is missing and stop. Do not run an edge test on a thin/sloppy calendar.

## Frozen event set (defined ex-ante — no event cherry-picking)

Primary set: **FOMC rate decisions, US CPI, US NFP.** This set is fixed *before* any return is
measured. Do **not** add ECB/PCE/jobless-claims/etc. *after* seeing results to manufacture a pass —
that is multiple-testing fishing. (A pre-registered secondary set MAY be reported separately as a
robustness check, clearly labelled, never to rescue a failed primary.)

## Cheap probe plan (read-only, after Step 0 passes)

1. Load the event timestamp table; align each event to BTC/ETH/SOL OHLCV (existing prod `ohlcv`,
   1h, the symbols we already have deep history for).
2. For each event and each forward horizon **{+6h, +24h, +72h}** (point-in-time, measured strictly
   *after* the release timestamp — no bar overlapping the release):
   - forward return (H1), and realized vol vs trailing baseline (H2).
3. Compare event-window distribution vs a **matched random-window baseline** (same count, same
   symbols, non-event timestamps).

## Pulse criteria (encode in the probe)

Per horizon, aggregated across the frozen event set:
- **H1 PASS:** mean event-window forward return is **directionally consistent** (≥ ~60% same sign)
  **and** materially exceeds the matched-baseline mean beyond a plausible fee/noise bar
  (state the bar, e.g. > 0.3% net of one-way fee), on **≥2 of 3 symbols** at **≥1 horizon**.
- **H2 PASS:** event-window realized vol exceeds baseline vol reliably (consistent across symbols).

Verdict:
- **HAS_PULSE** — H1 holds → write a bounded standalone event-window strategy surface, then Gate 2.
- **WEAK_EDGE** — only H2 holds (vol/risk overlay, not standalone direction) → record; decide
  whether a risk-gate is worth pursuing vs the next family. Do not reshape H1 by adding events.
- **NO_PULSE** — neither holds → close; events are priced in / not predictive after fees.

## Why this satisfies the allowed-next-family rules

| Rule (reset doc) | This lane |
|---|---|
| Different primitive | **Exogenous macro calendar** — new information, not an OHLCV transform. |
| Cheap probe first | Read-only, after a data-feasibility audit. |
| Standalone surface | Not attached to the SOL overlay or sentiment-macro. |
| WFO-realistic count | ~75 events × symbols; borderline — Step 0 count check is explicit. |
| Independent directionality | Driven by the macro calendar, not crypto price/trend — genuinely independent of the long-biased live agents. |

## Expected failure modes (do not oversell)

- **Priced in:** efficient reaction means no exploitable *forward* drift after the release → NO_PULSE.
- **Sign inconsistency:** events cut both ways (hot vs cold print) without the actual-vs-forecast
  surprise, which a pure-calendar probe does not have → low directional consistency → at best H2.
- **Too few events for WFO** even if the in-sample signature looks real (the AVAX/ETH precedent).
- **Timestamp sloppiness** introducing look-ahead — guard hard in Step 0.

## Validation command plan

```bash
# Step 0 + Gate 1 cheap probe (read-only)
uv run python scripts/probe_macro_event_drift.py --json   # builder to create

uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/macro-event-drift-probe-v0.md \
  --probe-verdict <HAS_PULSE|WEAK_EDGE|NO_PULSE> --pretty
```

## Guardrails (do not violate)

1. **Point-in-time only** — forward windows start strictly after the release timestamp; no bar
   overlapping the release; no use of actual/forecast values the probe could not have known pre-release.
2. **Frozen event set** (FOMC/CPI/NFP) defined before measuring returns; no post-hoc event additions
   to rescue a fail.
3. **Standalone**, long-and-short allowed (this is where independent directionality is welcome).
4. **Data audit is gate 0** — thin/sloppy calendar → BLOCKED_ON_DATA, stop; do not run on bad timestamps.
5. If only H2 (vol) holds → WEAK_EDGE, not HAS_PULSE. Be honest about overlay-vs-standalone.

## Reviewer (Claude) checkpoints

(a) data source documented + timestamp precision honest; (b) point-in-time alignment verified (no
look-ahead, no release-overlapping bar); (c) event set frozen ex-ante; (d) event-window metrics
compared to a matched random baseline, not vs zero; (e) verdict HAS_PULSE/WEAK_EDGE/NO_PULSE stated
honestly with per-symbol/per-horizon numbers and the event count attached.
