# Decision: Abandon BTC/4h Regime Router Thesis

## Status: FAILED as Production Candidate

**Reason**: Profitable but not investable
- 3 trades in 8 months = insufficient sample size
- Threshold tuning exhausted (tests prove no benefit)
- Paper trading cannot solve validation (3-4 years needed for 15+ trades)
- Low-frequency edge this small is not a serious production path

## What to Do Instead

### Option A: Archive and Move On (Recommended)
1. **Freeze RegimeRouterStrategy** as research baseline
   - File: `research/BTC_REGIME_ROUTER_ARCHIVED.md`
   - Status: Profitable on paper, insufficient for production
   - Lesson: Entry logic too restrictive for BTC/4h

2. **Pivot to new thesis families**
   - Don't spend more cycles on this exact entry logic
   - Try different strategy families or assets

### Option B: Build MTF Infrastructure (If Continuing Crypto Research)

**Build multi-timeframe support as reusable infrastructure**, not to rescue this strategy.

**Why**: 4h regime + 1h entry is a general capability for testing BTC/ETH pairs.

**Investment**: 2-3 days
**Value**: Enables search for profitable agents across multiple pairs/timeframes

## Implementation Plan (If Building MTF)

### Phase 1: Engine Modifications (Day 1)
**Files to modify**:
1. `src/features/reader.py`
   - Add `fetch_multi_timeframe()` method
   - Join 4h regime indicators onto 1h bars
   - Return combined indicator dict

2. `src/backtest/engine.py`
   - Modify to support multi-timeframe data fetching
   - Pass regime timeframe + entry timeframe to strategies

### Phase 2: Strategy Updates (Day 2)
**Files to modify**:
1. `src/strategy/multi_timeframe_regime.py`
   - Update to use actual MTF data from reader
   - Test with 4h regime + 1h entry

2. Create new thesis families:
   - `src/strategy/mtf_trend_pullback.py` - 4h trend + 1h pullback
   - `src/strategy/mtf_breakout_retest.py` - 4h breakout + 1h retest
   - `src/strategy/mtf_btc_eth_futures.py` - Two-sided futures entries

### Phase 3: Research Gates (Day 3)
**Strict validation criteria**:
- Minimum 15 trades in backtest
- Positive return
- Max drawdown < 15%
- Win rate >= 40%
- Enough OOS trades to matter

**If MTF doesn't produce robust candidates**: Pivot strategy family again.

## My Recommendation

**Choose Option A (Archive and Move On)** unless:
- You specifically want crypto trading agents
- You're willing to invest 2-3 days in MTF infrastructure
- You'll test NEW thesis families, not just this one

**Don't**: Build MTF just to rescue this specific strategy. It's not worth it.

## Files to Create (If Proceeding)

Want me to create the MTF implementation plan with exact code changes?

**If yes**, I'll write:
1. Detailed MTF engine modification plan
2. SQL queries for multi-timeframe joins
3. Updated strategy templates
4. Test plan

**If no**, we archive this and move to new research.
