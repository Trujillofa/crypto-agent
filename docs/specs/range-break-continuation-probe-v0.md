# Range-Break / Structural Continuation — Cheap Probe v0

**Status:** **QUEUED** — implementation landed; run on prod DB next (Hetzner). Target HAS_PULSE before any strategy/autoresearch.
**Date:** 2026-06-06 (post liquidity close)
**Prerequisite:** [research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md) — mean-reversion after 1h sweeps is **banned**.
**Script:** `scripts/probe_range_break_continuation.py`
**Rationale:** Liquidity sweep (failed = mean-rev) and short crowding both showed **continuation** after structural events on 1h majors. Probe the *with-the-break* surface instead of fading it.

---

## Role (v0)

**Read-only feasibility probe — not a strategy, not an overlay, not attached to promoted SOL stack.**

Question:

> After a liquidity sweep *or* breakout that *closes outside* the prior N-bar range (confirmed structural break), does price continue in the break direction over 6h/12h/24h with controlled MAE?

Trade *with* the move on confirmed range breaks. This is a new primitive (price structure + momentum after break), outside the banned family (no SOL overlay, no session, no funding direct, no crowding mean-rev, no more 1h aggregator retuning).

| Role | v0 | Deferred |
|------|-----|----------|
| Cheap pulse test | **Yes** | — |
| Strategy class | **No** | Only if HAS_PULSE |
| SOL overlay attachment | **No** | Never as first test |
| Live / paper / autoresearch | **No** | Downstream (after report + human gate) |

---

## Event definitions (1h bars)

Parameters (defaults match liquidity probe for comparability):

| Param | Default | Meaning |
|-------|---------|---------|
| `lookback_bars` | 24 | Prior range window (24h) |
| `range_expansion_mult` | 1.2 | Bar range ≥ 1.2× mean prior range |
| `volume_expansion_mult` | 1.2 | Bar volume ≥ 1.2× mean prior volume |
| `forward_bars_6h` | 6 | Forward horizon |
| `forward_bars_12h` | 12 | Forward horizon |
| `forward_bars_24h` | 24 | Forward horizon |

### Upside range break → **long** continuation

At bar `t` (entry at `close[t]`):

1. `high[t] > max(high[t-N:t])` — sweep/break above prior N-bar high.
2. `close[t] > max(high[t-N:t])` — **closes outside** (above), not rejected inside.  ← key difference from failed probe
3. Range expansion + volume expansion (same as liquidity probe).

### Downside range break → **short** continuation

1. `low[t] < min(low[t-N:t])` — sweep/break below prior N-bar low.
2. `close[t] < min(low[t-N:t])` — **closes outside** (below).
3. Same expansion filters.

One event per bar per side (no overlap dedup in v0). Events are "confirmed structural breaks".

---

## Metrics per event

| Metric | Long (upside break) | Short (downside break) |
|--------|---------------------|------------------------|
| Forward return | `(close[t+h]-close[t])/close[t]` | `(close[t]-close[t+h])/close[t]` |
| MAE | `(close[t]-min(low))/close[t]` (pullback against the continuation) | `(max(high)-close[t])/close[t]` |
| MFE | `(max(high)-close[t])/close[t]` | `(close[t]-min(low))/close[t]` |

Horizons: **6h, 12h, 24h**.

Baseline: all-bar random-entry MAE/forward (same horizons).

Fee drag: **0.08%** round-trip.

---

## Symbols and window

- **Symbols:** BTCUSDT, ETHUSDT, SOLUSDT (majors first)
- **Timeframe:** 1h
- **Window:** 2024-01-01 → 2026-06-01 (match prior probes)
- **Data:** `ohlcv` only (volume required)

---

## Pass gates (HAS_PULSE)

Per side (long upside-break-cont / short downside-break-cont), per symbol **or** pooled:

| Gate | Threshold |
|------|-----------|
| Events | ≥ 20 per symbol **or** ≥ 80 pooled |
| Forward edge | net mean > **0.15%** on 6h, 12h, or 24h (after fees) |
| MAE edge | ≥ **10%** lower mean MAE vs baseline (any horizon) |
| Event concentration | max single-event share of positive forwards ≤ **50%** |
| Month dominance | max single-month share ≤ **40%** |
| Cross-symbol | BTC/ETH/SOL not contradictory (opposing forward signs on qualifying events) |

HAS_PULSE only if **both** forward edge *and* MAE improvement are satisfied (same bar as liquidity probe). WEAK_EDGE if dense events but gates not both passed. NO_PULSE if too few events.

---

## Decision rules (from research reset)

- If HAS_PULSE → surface brief + consider strategy skeleton (standalone, not overlay first).
- If WEAK_EDGE → record (like liquidity), close lane or propose *different* primitive (not a tweak of this event def).
- Do not reshape the *failed* liquidity sweep definition quietly and call it "continuation" — this is the explicit new primitive.
- Next after this (if closed): another first-principles surface outside the banned list in reset.

See [research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md) for full banned list and "Allowed next hypothesis family".
