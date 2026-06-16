# Higher-TF Trend-Following Probe — Report

**Verdict:** **HAS_PULSE**
**Date:** 2026-06-13
**Script:** `scripts/probe_higher_tf_trend_following.py`
**Spec:** [higher-tf-trend-following-probe-v0.md](../specs/higher-tf-trend-following-probe-v0.md)
**Data:** production TimescaleDB (SSH tunnel), `ohlcv` 1h → resampled daily.

## Config
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT
- Window: 2024-01-01 → 2026-06-01
- SMA windows: 50, 100, 200 (daily)
- One-way fee: 0.04%

## Per symbol / window (strategy vs buy-and-hold)

| Symbol | SMA | Ret% | BH Ret% | Sharpe | BH Sharpe | MaxDD% | BH MaxDD% | Switches | In-mkt% | Pass |
|--------|-----|------|---------|--------|-----------|--------|-----------|----------|---------|------|
| BTCUSDT | 50 | 72.6 | 41.7 | 0.91 | 0.56 | 26.1 | 49.5 | 46 | 55 | **True** |
| BTCUSDT | 100 | -1.2 | 6.1 | 0.12 | 0.29 | 33.2 | 49.5 | 41 | 54 | False |
| BTCUSDT | 200 | 5.1 | 14.6 | 0.24 | 0.39 | 31.3 | 49.5 | 26 | 56 | False |
| ETHUSDT | 50 | 92.6 | -32.3 | 0.89 | 0.10 | 37.0 | 63.8 | 36 | 44 | **True** |
| ETHUSDT | 100 | -1.0 | -43.1 | 0.19 | -0.04 | 37.7 | 63.2 | 40 | 41 | False |
| ETHUSDT | 200 | 0.2 | -41.8 | 0.20 | -0.06 | 38.0 | 63.2 | 18 | 35 | True |
| SOLUSDT | 50 | 1.9 | -26.6 | 0.28 | 0.24 | 52.0 | 70.1 | 46 | 48 | **True** |
| SOLUSDT | 100 | -58.7 | -52.5 | -0.57 | -0.04 | 63.0 | 70.1 | 58 | 43 | False |
| SOLUSDT | 200 | -27.9 | -48.6 | -0.09 | -0.06 | 40.2 | 70.1 | 18 | 39 | False |

**Passing windows (symbol-majority):** SMA50 (3/3 symbols pass).

## Interpretation

The thesis holds at **SMA50**: a daily long-only trend filter beats buy-and-hold on
risk-adjusted terms across all three majors, cutting max drawdown ~20–25 points on each
while keeping or improving return. ETH is the strongest (−32% buy-hold → +93%); SOL is
the weakest (barely positive return, thin Sharpe edge, but DD still much better).

This is the first positive surface after ~1,440 autoresearch runs and the full sweep of
mean-reversion/fade lanes — and it matches the standing evidence that **these assets
trend** (the only live PnL ever earned was long-in-uptrend; see
[reversion-vs-continuation root cause](research-reset-2026-06-06.md) and the
sentiment-macro March longs).

## Caveats (do not oversell)

1. **Edge concentrated at SMA50.** SMA100 fails on all three; SMA200 passes only ETH. The
   signal is not "any trend filter" — it is specifically the 50-day. Across-symbol
   robustness at SMA50 is the redeeming axis, but across-window fragility means SMA50 was
   the best of three and carries mild selection risk.
2. **In-sample / single window.** No walk-forward yet. The AVAX/ETH precedent (b=100
   near-misses collapsing at b=1000) is the default expectation until Gate 4.
3. **SOL is the weakest** — the symbol the live agents trade. The strength is in BTC/ETH.
4. **Trade density (the key win):** ~36–46 switches/symbol over 2.4y ≈ ~1.5/month/symbol,
   ≈ 4–5/month across three symbols. Unlike the 0-trade SOL overlay, a promoted version
   could reach 20 forward trades in ~4–5 months — forward validation becomes feasible.

## Next action (per RBI runbook)

HAS_PULSE → Gate 2. Write a **bounded standalone daily-trend long-only strategy surface**
(single primary parameter ≈ SMA length, centered on 50), then run config-only autoresearch
under the standard WFO/bootstrap gate. Do **not** reshape into more MA-length fishing if
WFO fails — the across-window fragility above is the thing WFO must clear.

See research-reset-2026-06-06.md for banned surfaces and next-lane rules.
