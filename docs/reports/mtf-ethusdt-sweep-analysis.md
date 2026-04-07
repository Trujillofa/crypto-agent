# ETHUSDT Multi-Timeframe Regime Strategy — Sweep Analysis

**Date**: 2026-03-25
**Symbol**: ETHUSDT
**Entry TF**: 1h | **Regime TF**: 4h
**Period**: 2024-01-09 to 2026-02-23 (~25 months)
**Strategy**: `MultiTimeframeRegimeRouter`
**Candidates tested**: 50 (12 unique result profiles)
**WFO**: 3 windows (6-month train / 3-month test)

## Executive Summary

**The `multi_timeframe_regime` strategy is NOT production-ready for ETHUSDT.** All 50 parameter combinations produced negative returns across the full backtest period. The best candidate lost -3.08% with a -0.55 Sharpe ratio. No candidate passes realistic production gates.

## Results Overview

| Profile | Trades | Return | Sharpe | Max DD | PF   | Win Rate | WFO Trades | WFO Return |
|---------|--------|--------|--------|--------|------|----------|------------|------------|
| Best return | 6  | -3.08% | -0.55  | 3.77%  | 0.31 | 66.7%    | 3          | -4.46%     |
| Best Sharpe | 9  | -4.03% | -0.27  | 12.06% | 0.70 | 33.3%    | 6          | -6.72%     |
| Best PF     | 20 | -7.71% | -0.36  | 17.14% | 0.71 | 40.0%    | 9          | -4.58%     |
| Most trades | 44 | -21.07%| -0.81  | 32.72% | 0.61 | 36.4%    | 20         | -15.66%    |

**All 50 candidates lost money.** Returns range from -3.08% to -21.07%.

## Parameter Sensitivity Analysis

The sweep covered:
- **Regime thresholds**: `trend_strength` [0.002, 0.003, 0.005], `vol_pct` [25, 40, 60], `trend_consistency` [25, 50]
- **Entry zones**: `entry_zone_pct` [0.01, 0.015, 0.02], `deep_pullback_pct` [0.015, 0.025]
- **RSI**: oversold/overbought pairs [(35,65), (40,60), (45,55)]
- **Confidence/aggregator**: `trending_confidence` [1.0, 1.2], `buy_threshold` [0.6, 0.7]
- **Filters**: global trend filter [on/off], allow short [on/off]

### Key observations:

1. **Only 12 distinct outcomes** from 50 candidates — many parameters have no effect on trade count or outcome. The duplicates indicate that `buy_threshold`, `trend_filter`, and `trending_confidence` don't meaningfully change behavior for this strategy.

2. **Trade count is dominated by RSI thresholds**: Tight RSI (45/55) generates 3-6 trades; moderate (40/60) generates 5-11; wide (35/65) generates 3-9; widest RSI with widest zones generates 16-44 trades.

3. **More trades = worse performance**: The relationship is monotonically negative. The strategy's entry patterns trigger at poor locations — pullbacks to VWAP/EMA50 during trending regimes don't offer enough edge.

## Root Cause Analysis

### Why signals are sparse (3-44 trades over 25 months):

The strategy requires ALL of these simultaneously:
1. **4h regime = trending** (needs slope > threshold AND consistency > threshold AND vol_pct > threshold AND momentum aligned) — only ~20% of bars qualify
2. **1h pullback to VWAP or EMA50** (price within narrow zone of anchor) — further filters to ~5% of trending bars
3. **RSI oversold/overbought** — further reduces to <1% of trending bars

### Why all candidates lose:

1. **Counter-trend entries in a trending market**: When 4h says "trending up" and price pulls back to VWAP, the pullback often continues further, triggering the stop loss.
2. **Exit rules too tight**: With `sl_atr_multiplier=2.0` and `tp_atr_multiplier=4.5`, the SL is relatively tight for a pullback strategy. A 2× ATR stop in a trending market gets whipsawed.
3. **Regime classification lag**: By the time 4h confirms a trend (high slope + consistency + vol), the best entries have already passed.
4. **ETH's price action 2024-2026**: ETHUSDT has been choppy and mean-reverting at macro scale, with many false trend signals. A trend-following pullback strategy is poorly suited to this environment.

## Recommendations

### Short-term (if pursuing this strategy):
- Widen stop loss to 3-4× ATR
- Remove RSI condition from entry patterns (it's too restrictive)
- Add trailing entry (wait for pullback to bounce before entering, not just touch)
- Consider requiring 2+ consecutive 4h trending bars before arming entries

### Medium-term (strategic):
- **Test `mtf_template` strategy** — it may have different entry logic
- **Test on BTCUSDT** — stronger trends, more directional conviction
- **Consider regime-aware mean reversion** — if ETH is ranging 80% of the time, lean into that

### Not recommended:
- Deploying any of these parameter sets in production
- Further optimizing these parameters (all on the wrong side of zero)

## Files

- Sweep results: `docs/reports/mtf-search-20260325-173556.csv`
- Sweep JSON: `docs/reports/mtf-search-20260325-173556.json`
- Strategy: `src/strategy/multi_timeframe_regime.py`
- Config: `config/settings.eth_1h_mtf.yaml`
- Sweep script: `scripts/run_mtf_search.py`
