# Regime Router Strategy - Final Status

## Current State (As-Is)

### What's Actually Working
**4h RegimeRouterStrategy** with conservative thresholds

**Configuration**: `research/candidates/btc_regime_router_conservative_3rr.yaml`
```yaml
trend_strength_threshold: 0.008
volatility_percentile_threshold: 70.0
trend_consistency_threshold: 70.0
```

**Backtest Results** (Feb 2024 - Oct 2024):
- **3 trades** in 8 months
- 66.67% win rate (2 wins, 1 loss)
- +9.13% return
- 4.49% max drawdown
- 1.59 Sharpe ratio

**Trade Log:**
1. May 24 - Jun 05: BUY +5.68%
2. Jul 25 - Jul 26: BUY +5.68%
3. Jul 31 - Aug 01: BUY -2.29%

### Why So Few Trades?

**The strategy is intentionally extremely selective:**
- trend_strength_threshold: 0.008 (requires strong trend)
- volatility_percentile: 70.0 (top 30% volatility only)
- trend_consistency: 70.0 (top 30% consistency only)
- PLUS RSI oversold condition (< 45)

**Result**: Only ~1% of bars meet all criteria → ~3-4 trades per year

## The Statistical Problem

**Current Rate**: 3 trades / 8 months = 4.5 trades/year

**Sample Size Needed**: 15-20 trades minimum for confidence

**Time to Achieve**:
- Paper trading 6 months: ~2 additional trades (total 5)
- Paper trading 12 months: ~4 additional trades (total 7)
- **Even 2 years of paper trading: ~9 trades total**

**Conclusion**: Paper trading won't solve the sample size problem at current frequency.

## Threshold Tests: COMPLETED

**Tested with FIXED code:**

| Config | Trades | Win Rate | Return | Sharpe |
|--------|--------|----------|--------|--------|
| **Conservative** | 3 | 66.67% | +9.13% | 1.59 |
| Moderate (0.005/60/60) | 2 | 50.00% | +3.26% | 1.25 |
| Aggressive (0.003/50/50) | 2 | 50.00% | +3.26% | 0.89 |
| No RSI Filter | 3 | 33.33% | +0.89% | 0.30 |

**Finding**: Relaxing thresholds does NOT increase trade count. Entry logic (VWAP/EMA50 pullback zones) is the bottleneck.

**Conclusion**: Threshold tuning is NOT a viable path to more trades.

## Options (Choose One)

### 1. Build True Multi-Timeframe (Engineering)
- Modify backtest engine to join 4h regime + 1h bars
- Estimated: 2-3 days work
- Only remaining technical option for more trades
- Risk: May not work as expected

### 2. Accept Low-Frequency System (Philosophical)
- Deploy conservative config as-is
- Accept ~3-4 high-quality trades per year
- Position size: 5% per trade ($500 on $10k)
- Risk: Insufficient statistical confidence

### 3. Abandon Approach (Pragmatic)
- Thesis may not work for BTC/4h timeframe
- Consider different strategy families
- Consider different assets

## What Was NOT Built

**MultiTimeframeRegimeRouter** exists as a class stub but is NOT integrated with the backtest engine. It cannot run without:
1. Engine modifications to fetch multi-timeframe data
2. SQL joins between 4h regime and 1h bars
3. Estimated 2-3 days work

## Honest Recommendation

### Do NOT:
- ❌ Deploy and hope paper trading solves sample size (it won't)
- ❌ Tune thresholds (already tested with fixed code - doesn't work)
- ❌ Claim multi-timeframe is "implemented" when it's not integrated

### DO ONE OF:
- **A)** Build multi-timeframe (2-3 days) - only remaining technical option
- **B)** Accept low-frequency (~3-4 trades/year) - deploy with appropriate sizing
- **C)** Abandon approach - thesis may not work for BTC/4h

## Technical Status

✅ **Code is clean**:
- All 558 tests passing
- IndicatorReader regression fixed (uses .get() for backward compat)
- No syntax errors
- Backward compatible

✅ **Files in good state**:
- `src/strategy/regime_router.py` - Working single-timeframe strategy
- `src/strategy/multi_timeframe_regime.py` - Stub (not runnable)
- `src/features/reader.py` - Fixed with .get() accessors
- `research/FINAL_STATUS.md` - This file
- `research/THRESHOLD_TEST_RESULTS.md` - Complete test data

**Do not paper trade expecting sample size to improve** - it won't at current frequency.
