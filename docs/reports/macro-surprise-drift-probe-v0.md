# Macro-Surprise Drift Probe — Report

**Verdict:** **WEAK_EDGE**
**Date:** 2026-06-17
**Script:** `scripts/probe_macro_surprise_drift.py`
**Spec:** [macro-surprise-drift-probe-v0.md](../specs/macro-surprise-drift-probe-v0.md)
**Data:** TimescaleDB read-only (`127.0.0.1:15432` → local marketdata mirror); surprises from `data/macro_events/us_macro_surprises.csv` (55/56 events).

## Step 0 — Consensus data audit
- CPI+NFP frozen releases: **56**
- Rows with consensus+actual: **55**
- Missing consensus: **1**
- CPI rows: **27** | NFP rows: **28**
- Consensus sources: investing.com/Wayback
- Actual sources: bls.gov
- Point-in-time caveat: Consensus is Investing.com Forecast column from Wayback-archived calendar pages. Investing.com does not expose an auditable pre-release snapshot API; the Forecast field is the median shown on the calendar and may be revised after the fact. Treat as best-effort point-in-time, not cryptographically provable.
- Note: Nov NFP on delayed Dec-16 slot; Oct same-day had no forecast in archive
- Data gate: **PASS**

## Ex-ante expected sign (frozen)
- **CPI:** hot surprise → crypto down
- **NFP:** hot surprise → crypto down

## Pulse metrics (series × symbol × horizon)

### CPI / BTCUSDT (27 usable events)

| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |
|---------|-------|--------|------------|-------------|-------------------|-------------------|----------|--------|-------|----|----|
| +6h | 15 | 12 | 0.08 | 0.03 | -0.04 | 0.20 | -0.25 | 0.09 | 0.302 | False | False |
| +24h | 15 | 12 | 0.68 | 0.34 | -0.34 | 1.11 | -1.45 | 0.10 | 0.612 | False | False |
| +72h | 15 | 12 | 0.13 | 1.13 | 1.00 | 0.95 | 0.05 | -0.18 | 0.062 | False | False |

### NFP / BTCUSDT (28 usable events)

| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |
|---------|-------|--------|------------|-------------|-------------------|-------------------|----------|--------|-------|----|----|
| +6h | 7 | 21 | 0.73 | -1.11 | -1.85 | 0.37 | -2.21 | 0.63 | 1.149 | False | False |
| +24h | 7 | 21 | 0.65 | -1.09 | -1.75 | 1.14 | -2.88 | 0.53 | 1.168 | False | False |
| +72h | 7 | 21 | 3.33 | -1.93 | -5.26 | 1.23 | -6.49 | 0.48 | 2.019 | False | False |

### CPI / ETHUSDT (27 usable events)

| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |
|---------|-------|--------|------------|-------------|-------------------|-------------------|----------|--------|-------|----|----|
| +6h | 15 | 12 | 0.54 | 0.33 | -0.22 | 0.41 | -0.63 | -0.05 | -0.009 | False | True |
| +24h | 15 | 12 | 1.07 | 1.29 | 0.22 | 0.61 | -0.39 | -0.05 | -0.226 | False | True |
| +72h | 15 | 12 | 0.74 | 1.61 | 0.87 | 6.13 | -5.26 | -0.13 | -0.328 | False | True |

### NFP / ETHUSDT (28 usable events)

| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |
|---------|-------|--------|------------|-------------|-------------------|-------------------|----------|--------|-------|----|----|
| +6h | 7 | 21 | 0.71 | -1.64 | -2.35 | 0.57 | -2.92 | 0.65 | 1.477 | False | False |
| +24h | 7 | 21 | 0.74 | -1.58 | -2.32 | 1.10 | -3.43 | 0.57 | 1.451 | False | False |
| +72h | 7 | 21 | 3.34 | -3.14 | -6.48 | 6.15 | -12.63 | 0.48 | 2.564 | False | False |

### CPI / SOLUSDT (27 usable events)

| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |
|---------|-------|--------|------------|-------------|-------------------|-------------------|----------|--------|-------|----|----|
| +6h | 15 | 12 | 0.64 | 0.39 | -0.24 | 1.75 | -1.99 | 0.10 | 0.292 | False | False |
| +24h | 15 | 12 | 1.48 | 2.48 | 1.00 | 3.54 | -2.54 | 0.05 | 0.133 | False | False |
| +72h | 15 | 12 | 1.88 | 4.29 | 2.42 | 3.27 | -0.85 | -0.08 | -0.978 | False | True |

### NFP / SOLUSDT (28 usable events)

| Horizon | Hot n | Cold n | Hot mean % | Cold mean % | Oriented spread % | Baseline spread % | Excess % | Rank ρ | Slope | H1 | H2 |
|---------|-------|--------|------------|-------------|-------------------|-------------------|----------|--------|-------|----|----|
| +6h | 7 | 21 | 0.55 | -1.56 | -2.11 | 1.43 | -3.54 | 0.51 | 1.514 | False | False |
| +24h | 7 | 21 | 0.72 | -1.95 | -2.68 | 2.86 | -5.54 | 0.45 | 1.663 | False | False |
| +72h | 7 | 21 | 2.75 | -3.21 | -5.96 | 3.35 | -9.31 | 0.22 | 1.931 | False | False |

## Notes
- H2 pass: CPI +72h rank/slope on 2/3 symbols
- H1 bucket spread did not clear fee bar broadly; H2 monotonicity only

**Overall verdict:** WEAK_EDGE

## RBI guard

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/macro-surprise-drift-probe-v0.md \
  --probe-verdict WEAK_EDGE --pretty
```

Guard action: **CLOSE_LANE** (WEAK_EDGE ≠ HAS_PULSE). Record and decide; do not widen series/horizons.
