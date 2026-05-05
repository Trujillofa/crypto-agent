# Backtesting Summary

**Date:** 2026-03-18
**Latest follow-up:** [backtesting-follow-up-2026-05-05.md](backtesting-follow-up-2026-05-05.md)

**Primary sources:**
- [research/results.tsv](/home/yderf/TRADING/crypto-agent/research/results.tsv)
- [experiment-autopilot-20260311-213200-679389-427c4d-20260311-163201.json](/home/yderf/TRADING/crypto-agent/research/archive/experiment-autopilot-20260311-213200-679389-427c4d-20260311-163201.json)
- [trend-pullback-v3-search-sparse-trend-3-2-20260311-164123.json](/home/yderf/TRADING/crypto-agent/research/archive/trend-pullback-v3-search-sparse-trend-3-2-20260311-164123.json)
- [settings.sol_trend_pullback_sparse.yaml](/home/yderf/TRADING/crypto-agent/config/settings.sol_trend_pullback_sparse.yaml)
- [monte-carlo-bootstrap-solusdt-4h-2026-02-25.md](/home/yderf/TRADING/crypto-agent/docs/reports/monte-carlo-bootstrap-solusdt-4h-2026-02-25.md)

## Executive Summary

The strongest research result so far is still the SOL-only `TrendPullback` cluster, not the broader ensemble stack and not the newer BTC/BNB extensions. The first clean gate pass came only after moving that strategy into a sparse high-conviction validation regime: `SOLUSDT`, `4h`, `3/2` walk-forward windows, and the `sparse_trend_3_2` gate profile.

That result is materially better than the original baseline on risk and out-of-sample behavior, but it remains a sparse strategy. The correct operational interpretation is "research-approved paper candidate," not "proven production alpha."

## Important Results

### 1. Baseline broad ensemble failed

The original baseline run on `SOLUSDT 4h` was weak:

| Metric | Baseline |
|---|---:|
| Score | -387.798411 |
| OOS return | -7.23% |
| OOS mean Sharpe | -1.47 |
| Max drawdown | 25.20% |
| Bootstrap P(loss) | 76.80% |
| Profit concentration | 100.00% |
| Trades | 51 |

This is the result that justified moving away from the default multi-strategy stack for the SOL thesis.

### 2. `TrendPullback` was the only thesis family that improved the frontier

The first meaningful improvement came from `trend pullback only`:

| Candidate | Score | OOS return | OOS mean Sharpe | Max DD | Bootstrap P(loss) | Concentration | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|
| trend pullback only | -119.143348 | 4.66% | 0.45 | 3.53% | 0.00% | 100.00% | 2 |
| trend pullback v3 041 | -116.143348 | 4.66% | 0.45 | 5.76% | 24.60% | 100.00% | 5 |

This cluster improved robustness quickly, but it still failed the standard gate set because trade count and concentration stayed too sparse.

### 3. The decisive change was shorter WFO windows plus sparse-trend gates

The first passing result came from the same `trend_pullback_v3_041` candidate under a different evaluation regime:

- Symbol/timeframe: `SOLUSDT 4h`
- Walk-forward windows: `3` train months / `2` test months
- Gate profile: `sparse_trend_3_2`
- Preset: [settings.sol_trend_pullback_sparse.yaml](/home/yderf/TRADING/crypto-agent/config/settings.sol_trend_pullback_sparse.yaml)

Passing metrics:

| Metric | Passing result |
|---|---:|
| Score | 100770.059276 |
| Total return | 10.78% |
| Win rate | 80.00% |
| WFO windows | 7 |
| Aggregate WFO trades | 4 |
| OOS return | 7.72% |
| OOS mean Sharpe | 0.38 |
| Max drawdown | 5.76% |
| Bootstrap P(loss) | 24.60% |
| Profit concentration | 61.39% |
| Gate result | PASS |

This remains the best validated configuration in the repo.

### 4. The winning cluster is real, not a single lucky point

The bounded local robustness sweep around `trend_pullback_v3` under the sparse-trend regime tested `128` candidates. `16` passed all gates.

Stable parameters across the passing cluster:
- `rsi_reclaim_level = 48.0`
- `vwap_pullback_distance_pct = 0.05`
- `continuation_max_vwap_distance_pct = 0.04`

Parameters that stayed flexible inside the passing cluster:
- `buy_threshold = 0.45` or `0.55`
- `min_trend_strength_pct = 0.006` or `0.008`
- `strong_trend_strength_pct = 0.012` or `0.015`
- `continuation_rsi_level = 52.0` or `54.0`

That is the strongest evidence in the current research set that the SOL sparse-trend result is a stable pocket rather than one isolated parameter point.

### 5. Cross-symbol generalization failed

The same `trend_pullback_v3_041` thesis did not generalize cleanly outside SOL:

| Candidate | Symbol | Score | OOS return | OOS mean Sharpe | Max DD | Bootstrap P(loss) | Trades |
|---|---|---:|---:|---:|---:|---:|---:|
| trend pullback v3 041 BTC 4h | BTCUSDT | -240.980761 | -2.56% | -0.59 | 9.47% | 56.40% | 11 |
| trend pullback v3 041 BNB 4h | BNBUSDT | -526.911450 | -15.59% | -2.83 | 30.04% | 99.00% | 30 |

The current evidence supports this as a `SOLUSDT 4h` edge, not a general-purpose crypto trend template.

### 6. Alternative thesis work mostly solved the wrong problem

The next-best attempts increased activity or changed entry timing, but re-opened the wrong risk profile:

| Candidate | Score | OOS return | OOS mean Sharpe | Max DD | Bootstrap P(loss) | Concentration | Trades | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| trend pullback v4 deep dip | -129.934948 | 7.52% | 1.20 | 9.32% | 54.00% | 62.97% | 10 | Discard |
| cci breakout only strict | -488.909140 | -4.58% | -0.39 | 37.92% | 97.80% | 100.00% | 55 | Discard |
| trend pullback v5 deep reclaim | -119.143348 | 4.66% | 0.45 | 5.76% | 24.60% | 100.00% | 5 | Inert |
| trend pullback v6 armed reclaim | -119.143348 | 4.66% | 0.45 | 5.76% | 24.60% | 100.00% | 5 | Inert |

The code-level reclaim variants did not add new entries on the SOL sample. The deep-dip and CCI paths increased activity, but at unacceptable tail risk.

### 7. Earlier Monte Carlo work was directionally positive but under-sampled

The earlier bootstrap report for `SOLUSDT 4h` showed:

- deterministic return: `17.09%`
- deterministic Sharpe: `1.50`
- probability of negative return: `2.8%`
- trade sample size: `8`

That result was encouraging, but the report itself correctly marked it as too sparse to drive config decisions on its own. The later sparse-trend WFO work is the stronger decision basis.

## Current Recommendation

The research-backed preset remains [settings.sol_trend_pullback_sparse.yaml](/home/yderf/TRADING/crypto-agent/config/settings.sol_trend_pullback_sparse.yaml), but the May 2026 refresh documented in [backtesting-follow-up-2026-05-05.md](backtesting-follow-up-2026-05-05.md) failed the sparse gate narrowly. It should be treated as:

- valid for paper trading on `SOLUSDT 4h`
- evaluated under `3/2` walk-forward windows
- judged with the `sparse_trend_3_2` gate profile
- not ready for promotion without fresh validation

It should not be generalized to BTC or BNB without fresh evidence. The next recommended research step is a narrow `SOLUSDT 4h` long-only MA neighborhood and exit-model sweep, not cross-symbol promotion.

## Caveats

- [research/last_result.json](/home/yderf/TRADING/crypto-agent/research/last_result.json) is not the canonical "best run" artifact. It reflects the most recent run, which may be a crash or exploratory test.
- Historical experiment rows include early crash runs caused by environment and dependency issues. The passing SOL sparse-trend result above is the reliable benchmark.
- Trade counts are still low. The strategy has passed the repo's sparse-trend research gates, not a broad high-frequency robustness standard.
