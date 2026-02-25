# Monte Carlo Validation (Bootstrap): SOLUSDT 4h

**Date:** 2026-02-25
**Period:** 2024-01-01 to 2026-02-24
**Method:** Bootstrap resampling (2000 iterations)
**Trade sample size:** 8 trades per resample

## Baseline (Deterministic Backtest)

| Metric | Value |
|--------|-------|
| Trades | 8 |
| Win Rate | 75.00% |
| Total Return | 17.09% |
| Max Drawdown | 4.94% |
| Sharpe Ratio | 1.50 |

## Bootstrap Distribution

| Metric | Mean | Std | 5th pct | 25th pct | 50th pct | 75th pct | 95th pct |
|--------|------|-----|---------|----------|----------|----------|----------|
| Return (%) | 17.70 | 9.05 | 2.04 | 10.95 | 17.14 | 23.82 | 32.45 |
| Win Rate (%) | 75.31 | 15.33 | 50.00 | 62.50 | 75.00 | 87.50 | 100.00 |
| Trade Sharpe | 0.99 | 0.90 | 0.11 | 0.49 | 0.77 | 1.23 | 2.60 |

## Risk Assessment

- **Probability of negative return:** 2.8%
- **95% CI for total return:** [-0.64%, 34.41%]
- **Worst-case 5th percentile return:** 2.04%

## Interpretation

With only 8 realized trades, bootstrap confidence intervals are wide. This is not a flaw in the analysis — it accurately reflects the uncertainty inherent in a low-frequency strategy. The intervals will narrow as more live trades accumulate.

⚠️  **Insufficient sample**: Fewer than 30 trades. Results are directional only. Do not make config changes based solely on this analysis.
