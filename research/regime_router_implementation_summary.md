# Regime Router Strategy - Implementation Summary

## What Was Built

### Working Strategy
**RegimeRouterStrategy** (`src/strategy/regime_router.py`)
- 4h timeframe regime-based pullback entries
- Dual anchors: VWAP and EMA50
- Bug fixes applied (entry logic, unit mismatch)
- Stateless design for backtest compatibility

### Test Results (Feb-Oct 2024)

| Configuration | Trades | Win Rate | Return | Sharpe |
|--------------|--------|----------|--------|--------|
| **Conservative** | 3 | 66.67% | +9.13% | 1.59 |
| Moderate (0.005/60/60) | 2 | 50.00% | +3.26% | 1.25 |
| Aggressive (0.003/50/50) | 2 | 50.00% | +3.26% | 0.89 |
| No RSI Filter | 3 | 33.33% | +0.89% | 0.30 |

## Key Finding

**Relaxing thresholds does NOT increase trade count.**

Even with:
- Relaxed regime thresholds (0.003 vs 0.008)
- Disabled RSI filter
- Aggressive settings

We still only get 2-3 trades in 8 months. The entry logic (VWAP/EMA50 pullback zones) is the fundamental bottleneck.

## What Was NOT Built

**MultiTimeframeRegimeRouter** (`src/strategy/multi_timeframe_regime.py`)
- Class exists but NOT integrated with backtest engine
- Cannot fetch 4h regime indicators on 1h bars
- Not runnable without engine modifications

## The Problem

**Current rate**: ~4.5 trades/year
**Needed for confidence**: 15-20 trades minimum
**Time to achieve**: 3-4 years at current rate

**Paper trading won't help**: 12 months = ~4 additional trades (total 7). Still insufficient.

## Root Cause

The strategy requires three rare conditions to align:
1. Trending regime (~5% of bars)
2. Pullback to VWAP/EMA50 (rare in strong trends)
3. RSI oversold (rare in uptrends)

Multiplicative probability = very few signals.

## Options (Choose One)

### 1. Build True Multi-Timeframe
- Modify backtest engine to join 4h regime + 1h bars
- Estimated: 2-3 days work
- Risk: May still not increase trade count significantly

### 2. Accept Low-Frequency System
- Deploy conservative config as-is
- ~3-4 high-quality trades per year
- Position size: 5% per trade ($500 on $10k)
- Risk: Insufficient statistical confidence

### 3. Abandon Approach
- Thesis may not work for BTC/4h timeframe
- Consider different assets or strategy families

## Technical Status

✅ All 558 tests passing
✅ Code is clean and backward compatible
✅ IndicatorReader regression fixed (uses .get() for regime columns)

## Files

- `src/strategy/regime_router.py` - Working strategy
- `src/strategy/multi_timeframe_regime.py` - Stub (not runnable)
- `research/candidates/btc_regime_router_conservative_3rr.yaml` - Best config
- `research/THRESHOLD_TEST_RESULTS.md` - Complete test data
- `research/FINAL_STATUS.md` - Decision matrix

## Conclusion

The RegimeRouterStrategy is **profitable** (+9.13%, 66.67% win rate) but fundamentally **low-frequency** (~3-4 trades/year).

**Threshold tuning is exhausted** - tests prove it doesn't increase trade count.

**Decision required**: Build multi-timeframe, accept low-frequency, or abandon.

**Do NOT**: Paper trade expecting sample size improvement, or tune thresholds (already tested and failed).
