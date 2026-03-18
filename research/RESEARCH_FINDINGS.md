# MTF Research Findings

## Date: 2024-03-17

## Executive Summary

Phase 3 research reveals critical thesis flaws in the current MTF strategy template. The strategy performs poorly on 2024 data due to overly restrictive regime classification that prevents detecting uptrends.

## Key Findings

### 1. Overall Performance (2024 Full Year)

| Metric | Value |
|--------|-------|
| Total Trades | 43 |
| Win Rate | 25.6% |
| Total Return | -$2,482 (-24.8%) |
| Max Drawdown | 31.8% |
| Sharpe Ratio | -1.40 |
| Profit Factor | 0.61 |

**Verdict: Strategy loses money in 2024. Not production-viable.**

### 2. Threshold Sensitivity

| Threshold | Trades | Win% | Return | DD% | Sharpe |
|-----------|--------|------|--------|-----|--------|
| 0.003 | 35 | 22.9% | -$2,964 | 34.8% | -1.99 |
| 0.005 | 43 | 25.6% | -$2,482 | 31.8% | -1.40 |
| 0.007 | 50 | 26.0% | -$2,664 | 33.3% | -1.41 |
| 0.010 | 59 | 28.8% | -$2,152 | 31.8% | -0.95 |

**Finding: Higher threshold = slightly better performance but still losing.**

### 3. Short vs Long Bias

| Mode | Trades | Win% | Return | Sharpe |
|------|--------|------|--------|--------|
| Longs Only | 1 | 100% | +$56 | 1.17 |
| Shorts Enabled | 43 | 25.6% | -$2,482 | -1.40 |

**Critical Finding: Strategy generates almost NO long signals. 42 of 43 trades are shorts.**

### 4. Regime Classification Problem

The strategy's `_classify_regime()` requires ALL THREE conditions:
```python
is_trending = (
    abs(ema_slope) > self._regime_threshold    # 0.005
    and trend_consistency > 60.0                 # Hardcoded!
    and volatility_percentile > 50.0              # Hardcoded!
)
```

This is TOO RESTRICTIVE because:
- Bull markets have low volatility (fails condition 3)
- Steady uptrends have moderate EMA slope (fails condition 1)
- The 60% trend consistency threshold is too high

### 5. Quarterly Breakdown (Longs Only)

| Quarter | Trades | Win% | Return |
|---------|--------|------|--------|
| Q1 2024 | 1 | 100% | +$56 |
| Q2 2024 | 0 | - | $0 |
| Q3 2024 | 0 | - | $0 |
| Q4 2024 | 0 | - | $0 |

**Finding: Even in strong bull quarters (Q1, Q4), strategy triggers almost no longs.**

## Root Cause Analysis

1. **Hardcoded thresholds**: `trend_consistency_threshold=60.0` and `volatility_percentile=50.0` are hardcoded in `_classify_regime()`, not configurable via params.

2. **Short bias**: The short entry logic triggers when RSI > 60 (overbought), which happens frequently in volatile markets. Longs require RSI < 40 (oversold), which is rarer.

3. **Missing trend detection**: The strategy relies on EMA slope but doesn't capture "steady uptrend without high volatility" scenarios common in BTC.

## Recommendations for Future Iterations

### Priority 1: Make Regime Classification Configurable
```python
# Allow params for all thresholds
self._trend_consistency_threshold = self._config.get("trend_consistency_threshold", 50.0)
self._volatility_threshold = self._config.get("volatility_threshold", 40.0)
```

### Priority 2: Balance Long/Short
- Lower RSI thresholds for longs (e.g., < 45 instead of 40)
- Raise RSI thresholds for shorts (e.g., > 65 instead of 60)
- Or disable shorts until thesis proven

### Priority 3: Alternative Regime Indicators
- Try simpler regime detection: just EMA slope direction
- Add trend angle detection
- Use ADX instead of trend consistency

### Priority 4: Different Timeframe Combinations
- Try 15m/1h for more frequent entries
- Try 4h/1d for position trading

## Scripts Created for Research

- `scripts/mtf_parameter_sweep.py` - Basic parameter sweeps
- `scripts/mtf_extended_research.py` - Extended research with longer date range
- `scripts/mtf_thesis_investigation.py` - Quarterly analysis

## Conclusion

The MTF infrastructure is verified working correctly. The strategy template needs significant refinement before it's production-viable. The core thesis (4h regime + 1h entry) is sound, but the implementation parameters need tuning.

**Next Step**: Either:
1. Fix the regime classification to be less restrictive, OR
2. Abandon this thesis and try a different MTF combination


# ## CLOSED: BTC Directional MTF Family
#
# **Date Closed**: 2024-03-17
#
# After systematic testing, the BTC directional MTF family is abandoned:
#
# | Template | Thesis | Result |
# |----------|--------|--------|
# | Pullback | 4h trend + 1h pullback | FAILED (-$2.5k, 98% shorts) |
# | Continuation | 4h trend + 1h reclaim | FAILED (-$3.7k, 100% longs) |
# | Breakout/Expansion | 4h volatility + 1h reclaim | FAILED (-$4.4k, 45-50% longs) |
#
# **Root Cause**: OHLCV-only regime indicators don't produce actionable signal on BTC 2024.
#
# **Next Track**: Relative-value / cross-asset strategies.
