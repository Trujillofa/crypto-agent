# Cross-Asset / TradFi Risk-Regime Probe — Report

**Verdict:** **WEAK_EDGE**
**Date:** 2026-06-18
**Script:** `scripts/probe_cross_asset_risk_regime.py`
**Data:** TimescaleDB read-only (`127.0.0.1:15432`); TradFi from `data/tradfi/*.csv` (Yahoo Finance).
**Spec:** [cross-asset-risk-regime-probe-v0.md](../specs/cross-asset-risk-regime-probe-v0.md)

## Step 0 — TradFi data audit
- **equity_risk** (QQQ): daily **605** (2024-01-02..2026-06-01), 1h **5070** (2023-07-24..2026-06-18)
  - Close convention: QQQ daily close 16:00 America/New_York -> UTC
  - Weekend gaps flagged: daily=126, 1h=19
- **dxy** (DX-Y.NYB): daily **607** (2024-01-02..2026-06-01), 1h **14303** (2024-01-26..2026-06-18)
  - Close convention: DXY daily close 17:00 America/New_York -> UTC
  - Weekend gaps flagged: daily=126, 1h=1
- **us10y** (^TNX): daily **605** (2024-01-02..2026-06-01), 1h **4223** (2024-01-22..2026-06-18)
  - Close convention: ^TNX daily close 16:00 America/Chicago -> UTC
  - Weekend gaps flagged: daily=126, 1h=15
- **vix** (^VIX): daily **606** (2024-01-02..2026-06-01), 1h **9941** (2023-09-01..2026-06-18)
  - Close convention: ^VIX daily close 16:00 America/Chicago -> UTC
  - Weekend gaps flagged: daily=126, 1h=6
- equity_risk: 1h Yahoo cap ~730d (2023-07-24..2026-06-18); daily full window 605 rows
- dxy: 1h Yahoo cap ~730d (2024-01-26..2026-06-18); daily full window 607 rows
- us10y: 1h Yahoo cap ~730d (2024-01-22..2026-06-18); daily full window 605 rows
- vix: 1h Yahoo cap ~730d (2023-09-01..2026-06-18); daily full window 606 rows
- Yahoo 1h history capped at ~730d; H1/H2 primary alignment uses daily closes for full 2024-01-01..2026-06-01 window. 1h series reported separately where overlapping.
- Weekend/holiday gaps flagged via is_weekend_gap_after; TradFi levels are NOT forward-filled across closed sessions.
- Data gate: **PASS**

## Ex-ante expected signs (frozen)
### H1 lead-lag (prior proxy move → crypto forward)
- **equity_risk:** +1
- **dxy:** -1
- **us10y:** -1
- **vix:** -1
### H2 regime conditioning
- **risk_on:** +1
- **dxy_strong:** -1
- **us10y_rising:** -1
- **vix_high:** -1

## H1 — Lead-lag (forward vs baseline vs contemporaneous)

### equity_risk / BTCUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 603 | -0.05 | 0.00 | 0.03 | 0.33 | -0.30 | -0.05 | False |
| +24h | 603 | -0.07 | 0.36 | -0.37 | 0.50 | -0.87 | -0.43 | False |

### dxy / BTCUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 605 | 0.03 | 0.00 | -0.01 | 0.10 | -0.11 | -0.03 | False |
| +24h | 605 | -0.02 | -0.17 | -0.04 | 0.17 | -0.20 | -0.15 | False |

### us10y / BTCUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 603 | -0.01 | 0.00 | 0.08 | 0.10 | -0.02 | 0.01 | False |
| +24h | 603 | 0.03 | -0.01 | -0.12 | 0.35 | -0.47 | -0.04 | False |

### vix / BTCUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 604 | 0.06 | 0.00 | 0.04 | 0.02 | 0.02 | -0.06 | False |
| +24h | 604 | 0.07 | -0.30 | -0.24 | 0.26 | -0.51 | -0.37 | False |

### equity_risk / ETHUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 603 | -0.05 | 0.00 | -0.03 | 0.06 | -0.09 | -0.05 | False |
| +24h | 603 | -0.05 | 0.41 | -0.32 | 0.59 | -0.90 | -0.45 | False |

### dxy / ETHUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 605 | 0.06 | 0.00 | -0.13 | 0.04 | -0.17 | -0.06 | False |
| +24h | 605 | 0.01 | -0.16 | -0.26 | 0.22 | -0.47 | -0.17 | False |

### us10y / ETHUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 603 | 0.04 | 0.00 | -0.02 | 0.21 | -0.23 | -0.04 | False |
| +24h | 603 | 0.06 | -0.02 | -0.44 | 0.61 | -1.06 | -0.08 | False |

### vix / ETHUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 604 | 0.01 | 0.00 | 0.07 | 0.22 | -0.15 | -0.01 | False |
| +24h | 604 | 0.03 | -0.33 | -0.05 | 0.24 | -0.29 | -0.36 | False |

### equity_risk / SOLUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 603 | -0.03 | 0.00 | 0.02 | 0.68 | -0.66 | -0.03 | False |
| +24h | 603 | -0.08 | 0.31 | -0.69 | 1.38 | -2.07 | -0.39 | False |

### dxy / SOLUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 605 | 0.04 | 0.00 | -0.09 | 0.18 | -0.26 | -0.04 | False |
| +24h | 605 | 0.05 | -0.13 | -0.61 | 0.13 | -0.74 | -0.18 | False |

### us10y / SOLUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 603 | 0.01 | 0.00 | 0.04 | 0.23 | -0.19 | -0.01 | False |
| +24h | 603 | 0.04 | 0.03 | -0.43 | 0.29 | -0.72 | -0.07 | False |

### vix / SOLUSDT (1d)

| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | Excess vs base % | Excess vs contemp | H1 pass |
|---------|---|--------|-----------|-------------------|------------|------------------|-------------------|---------|
| +6h | 604 | 0.00 | 0.00 | 0.19 | 0.27 | -0.07 | -0.00 | False |
| +24h | 604 | 0.08 | -0.27 | -0.58 | 1.10 | -1.67 | -0.34 | False |

## H2 — Regime conditioning

### risk_on / BTCUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 404 | 180 | 0.00 | -0.13 | 0.14 | 0.01 | 0.13 | False |
| +24h | 404 | 180 | 0.04 | -0.07 | 0.11 | 0.38 | -0.27 | False |

### dxy_strong / BTCUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 267 | 317 | -0.07 | -0.02 | 0.05 | 0.11 | -0.07 | False |
| +24h | 267 | 317 | -0.10 | 0.10 | 0.19 | 0.47 | -0.27 | False |

### us10y_rising / BTCUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 306 | 278 | -0.08 | 0.01 | 0.09 | 0.04 | 0.05 | False |
| +24h | 306 | 278 | -0.10 | 0.13 | 0.23 | 0.07 | 0.16 | False |

### vix_high / BTCUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 123 | 461 | -0.11 | -0.02 | 0.09 | 0.01 | 0.07 | False |
| +24h | 123 | 461 | 0.28 | -0.07 | -0.34 | 0.16 | -0.50 | False |

### risk_on / ETHUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 404 | 180 | 0.13 | -0.24 | 0.37 | 0.27 | 0.10 | False |
| +24h | 404 | 180 | 0.06 | -0.19 | 0.25 | 1.11 | -0.86 | False |

### dxy_strong / ETHUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 267 | 317 | -0.02 | 0.05 | 0.07 | 0.06 | 0.01 | False |
| +24h | 267 | 317 | -0.15 | 0.10 | 0.24 | 0.10 | 0.15 | False |

### us10y_rising / ETHUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 306 | 278 | -0.02 | 0.06 | 0.08 | 0.23 | -0.15 | False |
| +24h | 306 | 278 | -0.07 | 0.04 | 0.11 | 0.53 | -0.42 | False |

### vix_high / ETHUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 123 | 461 | -0.11 | 0.05 | 0.17 | 0.29 | -0.12 | False |
| +24h | 123 | 461 | 0.41 | -0.13 | -0.54 | 0.53 | -1.06 | False |

### risk_on / SOLUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 404 | 180 | 0.11 | -0.13 | 0.24 | 0.45 | -0.21 | False |
| +24h | 404 | 180 | -0.02 | 0.09 | -0.11 | 1.13 | -1.24 | False |

### dxy_strong / SOLUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 267 | 317 | -0.10 | 0.15 | 0.25 | 0.04 | 0.20 | False |
| +24h | 267 | 317 | -0.24 | 0.23 | 0.47 | 1.12 | -0.64 | False |

### us10y_rising / SOLUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 306 | 278 | -0.08 | 0.17 | 0.24 | 0.02 | 0.22 | False |
| +24h | 306 | 278 | -0.14 | 0.19 | 0.33 | 0.65 | -0.32 | False |

### vix_high / SOLUSDT

| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | Baseline % | Excess % | H2 pass |
|---------|-------|---------|------------|--------------|-------------------|------------|----------|---------|
| +6h | 123 | 461 | -0.10 | 0.07 | 0.17 | 0.16 | 0.01 | False |
| +24h | 123 | 461 | 0.58 | -0.13 | -0.71 | 1.22 | -1.93 | False |

## H3 — Weekend gap (secondary)
- Observations: **125**
- Friday risk-off weekend mean: **1.52%**
- Friday risk-on weekend mean: **-0.40%**
- Oriented spread (risk-off − risk-on): **1.91%**
- Ex-ante expected sign: **-1**

## Correlation-regime-stability caveat
- Crypto–equity correlation is known to vary across 2024–2026; any in-sample lead-lag or regime filter must be treated as unstable until walk-forward validation.

## Notes
- relationship present but below fee bar or contemporaneous separation threshold

**Overall verdict:** WEAK_EDGE

## RBI guard

```bash
uv run python scripts/rbi_loop_guard.py \
  --lane-brief docs/specs/cross-asset-risk-regime-probe-v0.md \
  --probe-verdict WEAK_EDGE --pretty
```

Guard action: **CLOSE_LANE** (WEAK_EDGE ≠ HAS_PULSE). H1 lead-lag did not clear fee bar or
contemporaneous separation on ≥2/3 symbols; H2 regime buckets similarly weak. Record and decide;
do not add proxies or flip signs.
