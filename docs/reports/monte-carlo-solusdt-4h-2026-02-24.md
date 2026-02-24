# Monte Carlo Validation: SOLUSDT 4h

**Date:** 2026-02-24
**Period:** 2024-01-01 to 2026-02-24
**Simulations:** 100
**Configuration:** SL=2%, TP=5%, fee=0.1%, slippage variance=0.1%

## Baseline (Deterministic)

| Metric | Value |
|--------|-------|
| Trades | 8 |
| Win Rate | 75.00% |
| Return | 17.09% |
| Max DD | 4.94% |
| Sharpe | 1.50 |

## Monte Carlo Statistics (500 runs)

| Metric | Mean | Std | 5th | 25th | 50th | 75th | 95th |
|--------|------|-----|-----|------|------|------|------|--------|
| Sharpe | 1.50 | 0.00 | 1.50 | 1.50 | 1.50 | 1.50 | 1.50 |
| Win Rate | 75.00% | 0.00% | N/A | N/A | N/A | N/A | N/A |
| Return | 17.09% | 0.00% | N/A | N/A | N/A | N/A | N/A |
| Max DD | 4.94% | N/A | N/A | N/A | N/A | N/A | N/A |

## Statistical Significance

Baseline Sharpe: 1.50
MC Sharpe Mean: 1.50
Z-score: nan
**Conclusion**: Not statistically significant (may be luck)
