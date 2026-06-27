# Cross-Sectional Altcoin Momentum Probe — Result

**Verdict:** **NO_PULSE**
**Date:** 2026-06-27
**Universe:** 37 USDT perps (top by 24h quote volume)
**Span:** 2023-01-01 → 2026-06-01
**Costs:** 12.0 bps/leg turnover + 3.0 bps/day funding

## Grid (geometric terminal wealth, ruin-aware)

| lookback | quantile | periods | mean_net% | wealth(x) | solvent | ruin_periods | min_period% | concentration | bootstrap_p | shuffle_net% |
|---:|---:|---:|---:|---:|:--:|---:|---:|---:|---:|---:|
| 7d | 0.20 | 177 | 2.910 | 21.28 | yes | 0 | -33.0 | 0.20 | 0.003 | 0.255 |
| 7d | 0.30 | 177 | 1.589 | 5.20 | yes | 0 | -21.2 | 0.19 | 0.012 | 0.983 |
| 14d | 0.20 | 176 | 0.914 | 0.00 | NO | 1 | -209.6 | 0.08 | 0.211 | -0.872 |
| 14d | 0.30 | 176 | 0.786 | 0.00 | NO | 1 | -141.4 | 0.06 | 0.156 | -0.186 |
| 30d | 0.20 | 173 | 1.753 | 0.00 | NO | 1 | -237.9 | 0.21 | 0.133 | -3.246 |
| 30d | 0.30 | 173 | 1.282 | 0.00 | NO | 1 | -156.8 | 0.20 | 0.109 | -1.809 |

## Pre-registered gates

- G0 solvency (no cell bankrupts the book): **False** (2/6 cells solvent)
- G1 median terminal wealth > 1.0: **False** (median 0.00x)
- G2 robust ≥⅔ cells survive AND profit: **False** (2/6)
- G3 not concentration-driven (≤0.35): **True** (worst 0.20)
- G4 significant sign-correct spread (p<0.05): **True**
- G5 shuffle null dies: **False** (real 2.249% vs shuffled 0.619%)

**Overall verdict: NO_PULSE**
