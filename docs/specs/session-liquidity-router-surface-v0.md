# Session Liquidity Router — Surface v0

**Status:** probe passed — implement per implementation brief (no live deploy yet)
**Implementation brief:** `session-liquidity-router-implementation-brief-v0.md`
**Date:** 2026-06-05
**Priority:** next first-principles surface after Wave 10 (vol squeeze bounded closed)
**Symbol order for probe:** BTCUSDT → ETHUSDT → SOLUSDT

---

## Why this surface (vs another indicator shape)

Wave 10 repeated the failure mode: **frequency in a cheap probe ≠ survivable WFO edge**
once exits, drawdown, bootstrap risk, and concentration apply. Session routing targets
**when** to allow risk, not a new entry pattern — it can gate existing stacks without
inventing another thin breakout/mean-reversion shape.

| Criterion | Session router | Cross-exchange basis |
|-----------|----------------|----------------------|
| Data in DB today | OHLCV + indicators (`volume`, `atr_pct`, high/low range proxy) | Perp premium / basis history **not** in DB |
| Engineering cost | Probe + config router | New ingestion + validation |
| Role | Filter/router on promoted agents | Standalone crowding primitive |
| Cheap probe | Hour-of-week stratification | Blocked without data |

**Cross-exchange basis** stays queued until reliable basis/perp premium history exists.

---

## Hypothesis

**HYP-001:** Long-hold forward returns and adverse excursion on BTC/ETH/SOL 1h differ
materially by **UTC liquidity window** (volume + volatility regime), versus an
all-hours baseline.

**HYP-002:** A router that **blocks entries outside favorable windows** can improve
WFO risk metrics on an existing strategy without a new entry signal.

**HYP-003:** If no window beats baseline on return **and** adverse excursion with
enough samples, session routing is not worth implementing (kill early).

---

## Scope (v0)

| In scope | Out of scope |
|----------|--------------|
| BTC, ETH, SOL — 1h bars | BNB, AVAX, new symbols |
| UTC session buckets (disjoint 8h + optional overlap report) | Sub-hour microstructure |
| Long-bias forward stats (probe) | Short-side router until Option E parity |
| Router as **entry gate** on existing strategies | New standalone entry family first |
| Prod DB OHLCV + indicators | New ingest pipelines |

---

## Data (already available)

| Field | Source | Use |
|-------|--------|-----|
| `time` | `indicators` / `ohlcv` | UTC hour → session bucket |
| `close_price`, `high_price`, `low_price` | `ohlcv` | Forward return, adverse excursion |
| `volume` | `ohlcv` | Liquidity proxy (regime) |
| `atr_pct`, `volume_regime` | `indicators` | Volatility / participation context |
| Spread proxy | `(high - low) / close` | Bar-range liquidity stress |

No spread feed required for v0 probe.

---

## Session windows (v0 — disjoint UTC, 1h bars)

Primary buckets (exactly one per bar):

| Window | UTC hours `[start, end)` | Rationale |
|--------|------------------------|-----------|
| `asia` | 00 – 08 | Asia session dominance |
| `europe` | 08 – 16 | EU open through US pre-market |
| `americas` | 16 – 24 | US cash + close |

Optional v2: overlapping peaks (07–12, 13–20) — probe v0 uses **disjoint** buckets only
to avoid double-counting bars.

---

## Probe methodology

For every bar `t` with a full forward window (default **12 bars = 12h** on 1h):

1. **Forward return (long):** `(close[t+H] / close[t] - 1) * 100`
2. **Adverse excursion (long MAE):** `(close[t] - min(low[t+1:t+H])) / close[t] * 100`
3. Tag bar with disjoint session from `time.hour` (UTC).
4. Aggregate per window and **baseline** (all hours pooled).

**Baseline** = all eligible bars. A window must beat baseline on **both**:

- higher mean forward return (strict `>`)
- lower mean adverse excursion (strict `<`)

### Kill / proceed gates (per symbol)

| Verdict | Condition |
|---------|-----------|
| `NO_PULSE` | Fewer than `min_bars` eligible bars total |
| `SPARSE` | No window has ≥ `min_bars_per_window` samples |
| `WEAK_EDGE` | Enough samples but no window beats baseline on return **and** MAE |
| `HAS_PULSE` | ≥1 window beats baseline on **both** metrics with enough samples |

Default floors (probe CLI):

- `min_bars_per_window` = 500 (~8% of 20k 1h bars — adjust only with reason)
- Forward horizon = 12 bars (aligned with Wave 10 bounded hold lesson)

**Proceed to implementation only if:** ≥1 symbol shows `HAS_PULSE` **and** at least one
favorable window is **not** SOL-only (avoid router that only helps correlated live SOL).

If only SOL passes: reshape windows once; if still SOL-only, treat as weak independence.

---

## Feasibility probe

```bash
uv run python -m scripts.probe_session_liquidity_router --symbol BTCUSDT
./scripts/run_session_liquidity_probe.sh   # BTC → ETH → SOL on prod
```

Script: `scripts/probe_session_liquidity_router.py`

Report: `docs/reports/session-liquidity-router-probe-2026-06-05.md`

### Production probe result — 2026-06-05

| Symbol | Baseline 12h mean | Best window | Window 12h mean | Window MAE | Verdict |
|--------|-------------------|-------------|-----------------|------------|---------|
| BTCUSDT | +0.042% | `americas` | +0.058% | 1.175% | `HAS_PULSE` |
| ETHUSDT | +0.025% | `americas` | +0.117% | 1.727% | `HAS_PULSE` |
| SOLUSDT | +0.034% | `americas` | +0.168% | 2.160% | `HAS_PULSE` |

Decision: proceed to a router implementation brief. Do not deploy from this probe
alone; first validate whether an `americas` entry gate improves existing strategy
WFO/shadow behavior without destroying trade frequency.

---

## Implementation (next)

Follow `session-liquidity-router-implementation-brief-v0.md`:

- v1 window: **`americas` only** (16:00–24:00 UTC)
- BUY → HOLD outside window; SELL/exits unchanged
- Phase 2: SOL overlay backtest A/B before paper shadow
- No live deploy until shadow + overlap; autoresearch only after Phase 2 pass

---

## Stop conditions (research)

- 0 symbols `HAS_PULSE` → **CLOSED** in ledger; pick next brief (basis only if data lands).
- Pulse on SOL only → do not deploy BTC/ETH router; reshape or close.
- WFO improves frequency but fails DD/P(loss)/conc → **same as Wave 10** — do not retune windows.

---

## Related / closed

- Wave 10: `volatility_squeeze_bounded` — 0/80 passes — do not retune squeeze
- Wave 9: funding-primary SOL — closed
- Option E / basis: blocked on data
- Phase 0: continue weekly for SOL overlay + sentiment-macro
