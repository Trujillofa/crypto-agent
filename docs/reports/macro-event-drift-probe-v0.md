# Scheduled Macro-Event Drift Probe — Report

**Verdict:** **NO_PULSE**
**Date:** 2026-06-17
**Script:** `scripts/probe_macro_event_drift.py`
**Spec:** [macro-event-drift-probe-v0.md](../specs/macro-event-drift-probe-v0.md)
**Data:** TimescaleDB read-only (`127.0.0.1:15432` → local marketdata mirror, 21,169 BTC/ETH/SOL 1h bars).

## Step 0 — Data feasibility audit

| Check | Result |
|-------|--------|
| Frozen event set | FOMC + US CPI + US NFP (ex-ante, no post-hoc additions) |
| Total events (2024-01-01 → 2026-06-01) | **75** (FOMC 19, CPI 28, NFP 28) |
| Timestamp precision | **Minute-level**, DST-aware ET→UTC |
| CPI/NFP release time | 08:30 ET → 13:30 UTC (EST) / 12:30 UTC (EDT) |
| FOMC release time | 14:00 ET → 19:00 UTC (EST) / 18:00 UTC (EDT) |
| Sources | [federalreserve.gov](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm), [bls.gov](https://www.bls.gov/schedule/news_release/) (US government, public domain) |
| 2025 lapse handling | Oct CPI/NFP canceled; delayed releases per [BLS revised dates](https://www.bls.gov/bls/2025-lapse-revised-release-dates.htm) |
| Data gate | **PASS** (≥50 events, no date-only timestamps) |

Static table: `data/macro_events/us_macro_releases.csv`

## Config

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT (1h OHLCV)
- Horizons: +6h, +24h, +72h (strictly after release; no overlapping bar)
- H1 fee/noise bar: **0.3%** net vs matched random baseline
- H1 directional consistency gate: **≥60%** same sign
- H2 vol gate: event-window vol > trailing baseline on **≥50%** of events, mean ratio > 1.0
- Baseline: matched random windows (n=75 per symbol, 48h event exclusion, seed=42)

## Pulse metrics (per symbol, per horizon)

All symbols: **75 usable events** (full frozen set aligned to OHLCV).

### BTCUSDT

| Horizon | Mean ret % | Baseline % | Excess % | Dir consist % | Vol ratio | H1 | H2 |
|---------|------------|------------|----------|---------------|-----------|----|----|
| +6h | −0.21 | −0.04 | −0.16 | 57 (pos) | 1.07 | False | **True** |
| +24h | −0.09 | −0.14 | +0.06 | 51 (pos) | 0.88 | False | False |
| +72h | −0.13 | −0.26 | +0.13 | 53 (pos) | 0.87 | False | False |

### ETHUSDT

| Horizon | Mean ret % | Baseline % | Excess % | Dir consist % | Vol ratio | H1 | H2 |
|---------|------------|------------|----------|---------------|-----------|----|----|
| +6h | −0.22 | −0.03 | −0.20 | 53 (pos) | 0.98 | False | False |
| +24h | +0.07 | −0.23 | +0.30 | 60 (pos) | 0.87 | False | False |
| +72h | −0.11 | +0.01 | −0.12 | 55 (pos) | 0.91 | False | False |

### SOLUSDT

| Horizon | Mean ret % | Baseline % | Excess % | Dir consist % | Vol ratio | H1 | H2 |
|---------|------------|------------|----------|---------------|-----------|----|----|
| +6h | −0.19 | −0.15 | −0.05 | 51 (neg) | 1.02 | False | False |
| +24h | +0.11 | −0.80 | +0.91 | 51 (neg) | 0.89 | False | False |
| +72h | −0.17 | −0.62 | +0.45 | 53 (neg) | 0.91 | False | False |

## Interpretation

**H1 (directional drift) does not pass.** No symbol clears both the 60% directional-consistency
gate and the 0.3% excess-return bar vs matched random windows at any horizon. ETH +24h is the
closest (60% consistency, +0.30% excess) but fails the strict >0.3% bar; signs are inconsistent
across symbols and horizons (BTC/SOL diverge).

**H2 (volatility elevation) is marginal, not lane-passing.** BTC +6h shows modest vol lift
(ratio 1.07, 56% of events elevated) but only **one** symbol passes — below the ≥2-symbol
threshold for WEAK_EDGE. Longer horizons show *lower* event vol than baseline.

**Verdict: NO_PULSE** — scheduled macro releases do not produce a reliable, fee-surviving
forward drift or a consistent vol-overlay signal on BTC/ETH/SOL in this window. Close lane;
do not add events post-hoc or reshape horizons to rescue.

## RBI guard

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/macro-event-drift-probe-v0.md \
  --probe-verdict NO_PULSE --pretty
```

Expected: `CLOSE_LANE` (cheap probe completed, no pulse).
