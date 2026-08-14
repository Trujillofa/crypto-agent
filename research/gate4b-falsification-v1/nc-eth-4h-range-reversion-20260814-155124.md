# Experiment Autopilot: ETHUSDT 4h

- Config: `research/cost-realism-rerun/resolved/eth-4h-range-reversion-bounded.yaml`
- Range: 2024-01-01 → 2026-06-01
- Gate result: FAIL

## Baseline

| Metric | Value |
|---|---:|
| Total trades | 147 |
| Win rate | 48.30% |
| Total return | -71.44% |
| Max drawdown | 73.80% |
| Sharpe ratio | -1.55 |

## OOS Validation

| Metric | Value |
|---|---:|
| WFO windows | 4 |
| Aggregate WFO trades | 74 |
| Mean OOS Sharpe | -2.16 |
| Compound OOS return | -47.20% |
| Bootstrap P(loss) | 99.60% |
| Synthetic pass rate | INCONCLUSIVE (4/6 paths traded) |
| Profit concentration | 100.00% |
| Blocked BUY (session router) | 0 |
| Blocked BUY (basis filter) | 0 |
| Blocked BUY (cross-venue dislocation) | 0 |

diagnostic only; does not affect Gate result

## WFO Windows

| # | Test Start | Test End | Trades | Return | Sharpe | Max DD |
|---:|---|---|---:|---:|---:|---:|
| 1 | 2024-07-01 | 2024-10-01 | 25 | -11.22% | -1.24 | 19.92% |
| 2 | 2025-01-01 | 2025-04-01 | 20 | -9.56% | -0.74 | 17.50% |
| 3 | 2025-07-01 | 2025-10-01 | 14 | 0.73% | 0.24 | 9.62% |
| 4 | 2026-01-01 | 2026-04-01 | 15 | -34.72% | -6.89 | 39.30% |

## Gate Thresholds

- min_trades: 0
- min_wfo_trades: 20
- min_wfo_sharpe: 0.5
- max_drawdown_pct: 10.0
- max_bootstrap_p_loss_pct: 25.0
- max_mc_drawdown_p95_pct: 0.0
- min_oos_return_pct: 0.0
- max_profit_concentration_pct: 50.0

## Failures

- min_wfo_sharpe failed (-2.16 < 0.50)
- max_drawdown_pct failed (73.80% > 10.00%)
- max_bootstrap_p_loss_pct failed (99.60% > 25.00%)
- min_oos_return_pct failed (-47.20% < 0.00%)
- max_profit_concentration_pct failed (100.00% > 50.00%)
